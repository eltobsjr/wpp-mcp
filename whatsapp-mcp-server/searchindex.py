"""Índice de busca de mensagens (FTS5 com trigramas) num arquivo SEPARADO do banco do bridge.

Por que: a busca por texto sem acento chamava `fold()` em Python para cada uma das
centenas de milhares de mensagens (~1,8 s). Com o índice a busca vira consulta indexada.

Como funciona:
- `search_index.db` (na pasta `store/`) guarda o texto já sem acento/caixa, com o mesmo
  rowid da tabela `messages`. O banco do bridge nunca é alterado.
- A primeira construção roda numa thread em segundo plano; enquanto não termina, a
  busca usa o caminho antigo (mais lento, mas correto).
- Depois, cada busca indexa só as mensagens novas (rowid maior que o último indexado).
- Mensagens editadas depois de indexadas não atualizam o índice; a busca confere cada
  candidato de novo, então só pode deixar de achar o texto novo de uma mensagem editada.
  `rebuild()` refaz tudo se isso incomodar.
"""
import logging
import os
import sqlite3
import threading
from typing import Optional
from urllib.parse import quote

import config
from textutil import fold

logger = logging.getLogger("whatsapp-mcp")

INDEX_PATH = config.SEARCH_INDEX_PATH
MIN_TERM_LEN = 3  # trigramas: termos menores não são indexáveis
_BATCH = 20000

_thread_lock = threading.Lock()
_build_thread: Optional[threading.Thread] = None


def _open_index() -> sqlite3.Connection:
    conn = sqlite3.connect(INDEX_PATH, timeout=2)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS msgfts USING fts5(content, filename, tokenize='trigram')")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def _meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


def _open_source(messages_db: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{quote(os.path.abspath(messages_db))}?mode=ro", uri=True, timeout=5)


def sync(messages_db: str) -> bool:
    """Indexa as mensagens novas. Devolve True se o índice está completo.
    Se outro processo está escrevendo agora, devolve o estado atual sem esperar muito."""
    if not os.path.exists(messages_db):
        return False
    idx = src = None
    try:
        idx = _open_index()
        src = _open_source(messages_db)
        while True:
            # Caminho comum (nada novo): confere sem pegar a trava de escrita.
            if (_meta(idx, "ready") == "1" and int(_meta(idx, "last_rowid", "0")) == src.execute(
                    "SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()[0]):
                return True
            # A trava de escrita vem ANTES de ler o ponto de partida: assim dois processos
            # nunca indexam o mesmo trecho (rowid repetido no FTS5 dá erro).
            idx.execute("BEGIN IMMEDIATE")
            last = int(_meta(idx, "last_rowid", "0"))
            top = src.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()[0]
            if last > top:  # banco do bridge recriado: o índice antigo não vale mais
                idx.execute("DELETE FROM msgfts")
                last = 0
                _set_meta(idx, "ready", "0")
            if last >= top:
                _set_meta(idx, "ready", "1")
                idx.commit()
                return True
            rows = src.execute(
                "SELECT rowid, content, filename FROM messages WHERE rowid > ? ORDER BY rowid LIMIT ?",
                (last, _BATCH)).fetchall()
            idx.executemany(
                "INSERT INTO msgfts(rowid, content, filename) VALUES (?, ?, ?)",
                [(r[0], fold(r[1]), fold(r[2])) for r in rows if r[1] or r[2]])
            _set_meta(idx, "last_rowid", str(rows[-1][0]))
            idx.commit()
    except sqlite3.Error as e:
        logger.info(f"search index: sync adiado ({e})")
        return is_ready()
    finally:
        for c in (idx, src):
            if c is not None:
                c.close()


def is_ready() -> bool:
    if not os.path.exists(INDEX_PATH):
        return False
    try:
        conn = sqlite3.connect(f"file:{quote(INDEX_PATH)}?mode=ro", uri=True, timeout=2)
        try:
            return _meta(conn, "ready") == "1"
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def start_background_build(messages_db: str) -> None:
    """Dispara (uma vez por processo) a construção inicial sem bloquear quem chamou."""
    global _build_thread
    with _thread_lock:
        if _build_thread is not None and _build_thread.is_alive():
            return
        _build_thread = threading.Thread(target=sync, args=(messages_db,), name="search-index", daemon=True)
        _build_thread.start()


def rebuild(messages_db: str) -> bool:
    """Apaga o índice e reconstrói (síncrono)."""
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(INDEX_PATH + suffix)
        except FileNotFoundError:
            pass
    return sync(messages_db)


def attach_if_ready(conn: sqlite3.Connection, messages_db: str) -> bool:
    """Anexa o índice à conexão como `idx` se ele estiver pronto (atualizando com as
    mensagens novas). Sem índice pronto, agenda a construção e devolve False."""
    if not sync_quick(messages_db):
        start_background_build(messages_db)
        return False
    try:
        conn.execute("ATTACH DATABASE ? AS idx", (f"file:{quote(INDEX_PATH)}?mode=ro",))
        return True
    except sqlite3.Error as e:
        logger.info(f"search index: não anexou ({e})")
        return False


def sync_quick(messages_db: str) -> bool:
    """Pronto e atualizado? Só tenta sincronizar se o índice já foi concluído uma vez;
    a construção inicial é sempre em segundo plano."""
    if not is_ready():
        return False
    return sync(messages_db)


def match_expression(words: list) -> str:
    """Expressão FTS5: cada palavra (>= MIN_TERM_LEN) como frase entre aspas, todas exigidas."""
    return " AND ".join('"' + w.replace('"', '""') + '"' for w in words if len(w) >= MIN_TERM_LEN)
