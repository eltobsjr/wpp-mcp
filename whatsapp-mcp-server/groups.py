"""Grupos que o vigia acompanha em modo RASCUNHO.

Grupo nunca é respondido sozinho: a resposta chegaria a várias pessoas, então o único modo ligado é `draft`
(o agente avisa o dono e propõe um texto; só o dono envia). Para não acionar o agente a cada mensagem do grupo,
só contam as que MENCIONAM o dono (`@<número>`) ou trazem uma palavra-chave configurada para o grupo.
"""
import os
import re
import sqlite3
from typing import Dict, List, Optional

import config
import jsonstate

GROUP_MODES = ("off", "draft")  # `auto` em grupo não existe, de propósito


def _read() -> Dict[str, Dict[str, object]]:
    return jsonstate.read(config.GROUPS_PATH, {"groups": {}})["groups"]


def list_groups() -> List[Dict[str, object]]:
    return [{"jid": j, **g} for j, g in sorted(_read().items(), key=lambda kv: str(kv[1].get("name", "")).lower())]


def get_group(jid: str) -> Optional[Dict[str, object]]:
    g = _read().get(jid)
    return {"jid": jid, **g} if g else None


def set_group(jid: str, name: str, mode: Optional[str] = None, context: Optional[str] = None,
              keywords: Optional[List[str]] = None) -> Dict[str, object]:
    """Cria ou atualiza o grupo. `mode` só aceita off/draft (auto é recusado)."""
    if not jid.endswith("@g.us"):
        raise ValueError("só grupos (@g.us)")
    if mode is not None and mode not in GROUP_MODES:
        raise ValueError("em grupo só existem os modos off e draft (nunca auto)")
    groups = jsonstate.read(config.GROUPS_PATH, {"groups": {}})["groups"]
    g = groups.setdefault(jid, {"name": name, "reply_mode": "off", "context": "", "keywords": []})
    g["name"] = name or g.get("name", "")
    if mode is not None:
        g["reply_mode"] = mode
    if context is not None:
        g["context"] = context
    if keywords is not None:
        g["keywords"] = [k.strip() for k in keywords if k.strip()]
    jsonstate.update(config.GROUPS_PATH, {"groups": {}}, groups=groups)
    return {"jid": jid, **groups[jid]}


def owner_digits() -> str:
    """Número do dono (a conta pareada), para detectar menções `@número`. Vazio se não souber."""
    path = config.WHATSAPP_DB_PATH
    if not os.path.exists(path):
        return ""
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        row = conn.execute("SELECT jid FROM whatsmeow_device LIMIT 1").fetchone()
    except sqlite3.Error:
        return ""
    finally:
        if conn is not None:
            conn.close()
    return re.sub(r"\D", "", row[0].split("@")[0].split(":")[0]) if row else ""


def mention_patterns(group: Dict[str, object], owner: Optional[str] = None) -> List[str]:
    """Trechos que, estando no texto, fazem a mensagem contar (já em minúsculas)."""
    owner = owner_digits() if owner is None else owner
    pats = [f"@{owner}"] if owner else []
    pats += [str(k).lower() for k in (group.get("keywords") or []) if str(k).strip()]
    return pats
