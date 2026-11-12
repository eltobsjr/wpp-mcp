"""Modo conversacional: vigia mensagens novas e acorda uma rotina do OpenBot para responder.

Fluxo:
  mensagem chega -> este vigia (processo separado, lê o messages.db em modo leitura)
  -> espera a conversa "assentar" (debounce) -> POST na rotina do OpenBot
  -> o agente da rotina lê, redige no tom do contato (`prepare_message`) e
     - reply_mode "draft": só relata o rascunho ao dono;
     - reply_mode "auto": envia com `send_message`.

Travas (todas valem juntas):
  - desligado por padrão; ligar zera o ponto de leitura (não responde o histórico);
  - só conversas 1:1 de contatos da agenda com reply_mode "draft" ou "auto";
  - nunca grupos, canais nem status;
  - mensagens minhas não disparam nada, e se eu respondi antes do debounce a espera é cancelada;
  - limite de acionamentos por conversa por hora;
  - o vigia não escreve no banco do bridge nem envia mensagens: só acorda o agente.

Rodar:  python conversation.py watch [--interval 2]
"""
import contextlib
import fcntl
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional

import requests

import config
import contacts
import identity
import whatsapp

logger = logging.getLogger("wpp-conversation")

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "enabled_at": None,
    "debounce_seconds": 20,
    "max_triggers_per_hour": 6,
    "cursor": None,
    "agent_id": "",
    "routine_id": "",
}
_NON_PERSONAL_SUFFIXES = ("@g.us", "@newsletter", "@broadcast")


def _path() -> str:
    return config.CONVERSATION_STATE_PATH


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    with open(os.path.abspath(_path()) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load() -> Dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {**DEFAULTS, **data}


def _write(state: Dict[str, Any]) -> None:
    path = os.path.abspath(_path())
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def update(**fields: Any) -> Dict[str, Any]:
    """Altera só os campos informados, relendo o arquivo sob trava (o vigia e o MCP
    escrevem nele ao mesmo tempo)."""
    unknown = set(fields) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Campo(s) desconhecido(s): {', '.join(sorted(unknown))}")
    with _locked():
        state = load()
        state.update(fields)
        _write(state)
    return state


def current_top() -> int:
    conn = whatsapp._connect()
    try:
        return conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()[0]
    finally:
        conn.close()


def set_enabled(enabled: bool) -> Dict[str, Any]:
    """Liga/desliga. Ligar exige rotina configurada e começa a ler dali em diante."""
    if enabled:
        state = load()
        if not (state["agent_id"] and state["routine_id"]):
            raise ValueError("Configure agent_id e routine_id da rotina do OpenBot antes de ligar.")
        return update(enabled=True, enabled_at=datetime.now().isoformat(timespec="seconds"), cursor=current_top())
    return update(enabled=False)


def eligible_contacts() -> List[Dict[str, str]]:
    return [{"name": c["name"], "phone": c["phone"], "reply_mode": c["profile"]["reply_mode"]}
            for c in contacts.list_contacts()
            if (c.get("profile") or {}).get("reply_mode") in ("draft", "auto")]


@dataclass
class Trigger:
    chat_jid: str
    name: str
    mode: str
    count: int
    first_rowid: int


def eligibility(chat_jid: str) -> Optional[Dict[str, str]]:
    """{'name', 'mode'} se a conversa pode ser respondida; None caso contrário."""
    if chat_jid.endswith(_NON_PERSONAL_SUFFIXES) or chat_jid.startswith("status@"):
        return None
    phone = identity.phone_of(chat_jid)
    if not phone:
        return None
    try:
        contact = contacts.find_contact(phone)
    except contacts.RecipientError:
        return None
    mode = ((contact or {}).get("profile") or {}).get("reply_mode")
    if contact and mode in ("draft", "auto"):
        return {"name": contact["name"], "mode": mode}
    return None


def _read_headers(path: str) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.partition(":")
            if sep and key.strip():
                headers[key.strip()] = value.strip()
    return headers


def webhook_notify(trigger: Trigger) -> bool:
    """Acorda a rotina do OpenBot. A URL e os cabeçalhos (com o token) vêm dos arquivos
    locais de automação; nada disso é registrado em log."""
    state = load()
    try:
        with open(os.path.join(config.AUTOMATION_DIR, "url"), encoding="utf-8") as f:
            base = f.read().strip().rstrip("/")
        headers = _read_headers(os.path.join(config.AUTOMATION_DIR, "headers"))
    except OSError as e:
        logger.error(f"automação do OpenBot indisponível: {type(e).__name__}")
        return False
    url = f"{base}/v1/agents/{state['agent_id']}/routines/{state['routine_id']}/run"
    payload = (f"Modo conversacional do WhatsApp: {trigger.count} mensagem(ns) nova(s) de "
               f"{trigger.name} ({trigger.chat_jid}). reply_mode={trigger.mode}. "
               f"Leia com read_new_messages(cursor={trigger.first_rowid - 1}, chat_jid=\"{trigger.chat_jid}\", "
               f"include_from_me=true).")
    try:
        response = requests.post(url, headers=headers, json={"payload": payload}, timeout=10)
    except requests.RequestException as e:
        logger.error(f"falha ao acionar a rotina: {type(e).__name__}")
        return False
    if not response.ok:
        logger.error(f"rotina recusou o acionamento: HTTP {response.status_code}")
    return response.ok


class Watcher:
    """Estado em memória do vigia. `poll()` é chamado em laço e devolve quantas rotinas acordou."""

    def __init__(self, notify: Callable[[Trigger], bool] = webhook_notify, clock: Callable[[], float] = time.time):
        self.notify = notify
        self.clock = clock
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.sent: Dict[str, Deque[float]] = defaultdict(deque)
        self.read_cursor: Optional[int] = None
        self._seen_enabled_at: Optional[str] = None

    def poll(self) -> int:
        state = load()
        if not state["enabled"]:
            self.pending.clear()
            self.read_cursor = None
            self._seen_enabled_at = None
            return 0
        if self.read_cursor is None or state["enabled_at"] != self._seen_enabled_at:
            # Recém-ligado (ou vigia reiniciado): retoma do cursor salvo, sem reler o histórico.
            self.pending.clear()
            self._seen_enabled_at = state["enabled_at"]
            self.read_cursor = state["cursor"] if state["cursor"] is not None else current_top()
        now = self.clock()
        self._ingest(now)
        fired = self._fire_due(now, state)
        self._persist_cursor()
        return fired

    def _ingest(self, now: float) -> None:
        conn = whatsapp._connect()
        try:
            top = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()[0]
            rows = conn.execute(
                "SELECT chat_jid, is_from_me, COUNT(*), MIN(rowid), MAX(rowid) FROM messages "
                "WHERE rowid > ? GROUP BY chat_jid, is_from_me", (self.read_cursor,)).fetchall()
        finally:
            conn.close()
        incoming: Dict[str, Any] = {}
        mine_max: Dict[str, int] = {}
        for jid, from_me, count, lo, hi in rows:
            if jid.endswith(_NON_PERSONAL_SUFFIXES) or jid.startswith("status@"):
                continue
            if from_me:
                mine_max[jid] = hi
            else:
                incoming[jid] = (count, lo, hi)
        for jid, hi_mine in mine_max.items():
            # Eu respondi depois da última mensagem recebida: nada a fazer.
            if jid in self.pending and hi_mine > self.pending[jid]["last_rowid"]:
                self.pending.pop(jid)
        for jid, (count, lo, hi) in incoming.items():
            if hi < mine_max.get(jid, -1):
                continue
            p = self.pending.setdefault(jid, {"first_rowid": lo, "count": 0, "last_rowid": hi, "last_seen": now})
            p["count"] += count
            p["last_rowid"] = hi
            p["last_seen"] = now
        self.read_cursor = top

    def _fire_due(self, now: float, state: Dict[str, Any]) -> int:
        fired = 0
        for jid, p in list(self.pending.items()):
            if now - p["last_seen"] < state["debounce_seconds"]:
                continue
            who = eligibility(jid)
            if who is None:
                self.pending.pop(jid)
                continue
            recent = self.sent[jid]
            while recent and recent[0] < now - 3600:
                recent.popleft()
            if len(recent) >= state["max_triggers_per_hour"]:
                logger.info(f"limite por hora atingido chat={_tag(jid)}")
                self.pending.pop(jid)
                continue
            trigger = Trigger(jid, who["name"], who["mode"], p["count"], p["first_rowid"])
            if self.notify(trigger):
                recent.append(now)
                self.pending.pop(jid)
                fired += 1
                logger.info(f"rotina acionada chat={_tag(jid)} n={p['count']} modo={who['mode']}")
            else:
                p["last_seen"] = now  # tenta de novo depois de outro intervalo de debounce
        return fired

    def _persist_cursor(self) -> None:
        """Guarda onde retomar: antes da mais antiga espera pendente, para não perdê-la se o vigia reiniciar."""
        resume = min((p["first_rowid"] - 1 for p in self.pending.values()), default=self.read_cursor)
        if resume is not None and resume != load()["cursor"]:
            update(cursor=resume)


def _tag(jid: str) -> str:
    return hashlib.sha1(jid.encode()).hexdigest()[:8]


def watch(interval: float = 2.0) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(message)s")
    watcher = Watcher()
    logger.info("vigia do modo conversacional iniciado")
    while True:
        try:
            watcher.poll()
        except Exception as e:  # o vigia não pode morrer por um erro isolado
            logger.error(f"erro no ciclo: {type(e).__name__}: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "watch":
        every = float(sys.argv[sys.argv.index("--interval") + 1]) if "--interval" in sys.argv else 2.0
        watch(every)
    else:
        print(__doc__)
        sys.exit(2)
