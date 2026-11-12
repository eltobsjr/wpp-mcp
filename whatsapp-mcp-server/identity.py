"""Resolve quem é quem: JID/LID -> telefone -> nome.

O WhatsApp novo identifica a maioria dos contatos por LID (`123@lid`), não pelo
telefone. O bridge grava o chat com o LID, mas os nomes (whatsmeow_contacts) e o mapa
LID->telefone (whatsmeow_lid_map) ficam no whatsapp.db. Este módulo junta tudo em
memória (cache de poucos segundos) para a busca por nome, por telefone e a exibição de
remetentes funcionarem mesmo quando o chat só tem o número/LID gravado.

Prioridade de nome: agenda local (contacts.json) > nome do chat > contatos do WhatsApp.
"""
import os
import re
import sqlite3
import time
from typing import Dict, List, Optional, Set

import contacts as saved_contacts
from textutil import fold, phone_keys  # noqa: F401 (reexportados)

_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whatsapp-bridge", "store")
MESSAGES_DB_PATH = os.path.join(_STORE, "messages.db")
WHATSAPP_DB_PATH = os.path.join(_STORE, "whatsapp.db")
_TTL_S = 30

_cache: Dict[str, object] = {"t": 0.0}


def is_placeholder(name: Optional[str]) -> bool:
    """Nome vazio ou só dígitos (o bridge grava o número quando não acha o nome)."""
    return not name or re.fullmatch(r"\+?\d+", name.strip()) is not None


def user_of(jid: str) -> str:
    return (jid or "").split("@")[0].split(":")[0]


def _connect_ro(path: str) -> Optional[sqlite3.Connection]:
    if not os.path.exists(path):
        return None
    try:
        return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error:
        return None


def _refresh(force: bool = False) -> Dict[str, object]:
    if not force and time.time() - float(_cache["t"]) < _TTL_S:
        return _cache
    lid2pn: Dict[str, str] = {}
    wa_names: Dict[str, str] = {}
    chat_names: Dict[str, str] = {}

    conn = _connect_ro(WHATSAPP_DB_PATH)
    if conn:
        try:
            lid2pn = {lid: pn for lid, pn in conn.execute("SELECT lid, pn FROM whatsmeow_lid_map")}
            for jid, full, first, push, biz in conn.execute(
                "SELECT their_jid, full_name, first_name, push_name, business_name FROM whatsmeow_contacts"
            ):
                name = next((n for n in (full, first, biz, push) if n), "")
                if name:
                    for k in phone_keys(user_of(jid)):
                        wa_names.setdefault(k, name)
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    conn = _connect_ro(MESSAGES_DB_PATH)
    if conn:
        try:
            chat_names = {jid: name for jid, name in conn.execute("SELECT jid, name FROM chats") if name}
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    agenda: Dict[str, str] = {}
    for c in saved_contacts._load():
        for k in phone_keys(c["phone"]):
            agenda.setdefault(k, c["name"])

    pn2lids: Dict[str, List[str]] = {}
    for lid, pn in lid2pn.items():
        for k in phone_keys(pn):
            pn2lids.setdefault(k, []).append(lid)

    _cache.update(t=time.time(), lid2pn=lid2pn, pn2lids=pn2lids, wa_names=wa_names,
                  chat_names=chat_names, agenda=agenda)
    return _cache


def invalidate() -> None:
    """Chame depois de gravar na agenda para a mudança valer na hora."""
    _cache["t"] = 0.0


def phone_of(jid_or_user: str) -> str:
    """Telefone (só dígitos) de um JID. Resolve @lid pelo mapa; '' se não souber."""
    c = _refresh()
    user = user_of(jid_or_user)
    if jid_or_user.endswith("@lid"):
        return c["lid2pn"].get(user, "")
    return c["lid2pn"].get(user, user)


def lookup_name(jid_or_user: str) -> str:
    """Melhor nome conhecido (agenda > chat > contatos do WhatsApp) ou ''."""
    c = _refresh()
    user = user_of(jid_or_user)
    pn = c["lid2pn"].get(user, user)
    for k in phone_keys(pn):
        if k in c["agenda"]:
            return c["agenda"][k]
    for suffix in ("@s.whatsapp.net", "@lid"):
        n = c["chat_names"].get(user + suffix)
        if n and not is_placeholder(n):
            return n
    for k in phone_keys(pn):
        if k in c["wa_names"]:
            return c["wa_names"][k]
    return ""


def display_name(jid: str, stored: Optional[str] = None) -> Optional[str]:
    """Nome para mostrar de um chat. Grupos mantêm o nome do grupo."""
    if jid.endswith("@g.us") or jid.endswith("@newsletter") or jid.endswith("@broadcast"):
        return stored
    resolved = lookup_name(jid)
    if resolved:
        return resolved
    return stored


def equivalent_users(identifier: str) -> Set[str]:
    """Todas as formas de identificar o mesmo contato no banco: telefone (com e sem 9º
    dígito), LIDs mapeados e o valor original. Serve para filtrar `messages.sender`."""
    c = _refresh()
    user = user_of(identifier)
    out = {user}
    pn = c["lid2pn"].get(user, user)
    for k in phone_keys(pn):
        out.add(k)
        out.update(c["pn2lids"].get(k, []))
    return {u for u in out if u}


def equivalent_jids(identifier: str) -> Set[str]:
    """JIDs de chat individuais possíveis para o contato (telefone e LID)."""
    out = set()
    if "@" in identifier and not identifier.endswith(("@s.whatsapp.net", "@lid")):
        return {identifier}
    for u in equivalent_users(identifier):
        out.add(f"{u}@s.whatsapp.net")
        out.add(f"{u}@lid")
    return out


def phone_matches(phone: str, query_digits: str) -> bool:
    return bool(query_digits) and any(query_digits in k for k in phone_keys(phone))


def all_wa_contacts() -> Dict[str, str]:
    """Contatos do WhatsApp (livro de endereços sincronizado): telefone -> nome."""
    return dict(_refresh()["wa_names"])


def agenda_names() -> Dict[str, str]:
    return dict(_refresh()["agenda"])
