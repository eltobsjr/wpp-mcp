"""Agenda de contatos local e editável.

Fica em whatsapp-bridge/store/contacts.json (fora do git). Tem prioridade sobre a
busca nos chats do WhatsApp, que às vezes falha. Formato:
    {"contacts": [{"name": "Maria", "phone": "5511999998888", "notes": ""}]}
O arquivo pode ser editado à mão.
"""
import json
import quopri
import os
import re
import tempfile
from typing import Dict, List, Optional

from textutil import fold, phone_keys

CONTACTS_PATH = os.environ.get("WHATSAPP_CONTACTS_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "whatsapp-bridge", "store", "contacts.json"
)


def normalize_phone(raw: str, default_country: str = "55") -> str:
    """Só dígitos; se parecer número BR sem DDI (10-11 dígitos), prefixa 55."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in (10, 11):
        digits = default_country + digits
    return digits


def _load() -> List[Dict[str, str]]:
    try:
        with open(CONTACTS_PATH, encoding="utf-8") as f:
            return json.load(f).get("contacts", [])
    except FileNotFoundError:
        return []


def _save(contacts: List[Dict[str, str]]) -> None:
    path = os.path.abspath(CONTACTS_PATH)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"contacts": sorted(contacts, key=lambda c: c["name"].lower())},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_contacts(query: Optional[str] = None) -> List[Dict[str, str]]:
    """Sem query, todos. Com query: todas as palavras no nome (sem acento/maiúscula)
    ou o telefone contendo os dígitos (aceita número com ou sem o 9º dígito)."""
    contacts = _load()
    if query:
        words = fold(query).split()
        qd = re.sub(r"\D", "", query)
        contacts = [c for c in contacts
                    if (words and all(w in fold(c["name"]) for w in words))
                    or (len(qd) >= 4 and any(qd in k for k in phone_keys(c["phone"])))]
    return contacts


def save_contact(name: str, phone: str, notes: str = "") -> Dict[str, str]:
    """Cria ou atualiza (a chave é o telefone)."""
    name, phone = name.strip(), normalize_phone(phone)
    if not name or not phone:
        raise ValueError("name e phone são obrigatórios")
    contacts = _load()
    for c in contacts:
        if c["phone"] == phone:
            c["name"] = name
            if notes:
                c["notes"] = notes
            break
    else:
        c = {"name": name, "phone": phone, "notes": notes}
        contacts.append(c)
    _save(contacts)
    return c


def remove_contact(identifier: str) -> int:
    """Remove por telefone ou por nome exato. Retorna quantos removeu."""
    phone = normalize_phone(identifier)
    contacts = _load()
    kept = [c for c in contacts
            if c["phone"] != phone and c["name"].lower() != identifier.strip().lower()]
    _save(kept)
    return len(contacts) - len(kept)


def resolve(recipient: str) -> str:
    """Se `recipient` for o nome de um contato salvo (único), devolve o telefone.
    Caso contrário devolve o valor original."""
    if "@" in recipient or re.fullmatch(r"\+?[\d\s\-()]{8,}", recipient):
        return recipient
    exact = [c for c in _load() if c["name"].lower() == recipient.strip().lower()]
    if len(exact) == 1:
        return exact[0]["phone"]
    return recipient


def parse_vcf(text: str) -> List[Dict[str, str]]:
    """Extrai (nome, telefone) de um .vcf. Um contato com N telefones gera N entradas."""
    text = re.sub(r"(?<==)\r?\n", "", text)  # soft breaks do quoted-printable
    text = re.sub(r"\r?\n[ \t]", "", text)  # junta linhas dobradas
    out = []
    for block in re.findall(r"BEGIN:VCARD(.*?)END:VCARD", text, flags=re.S | re.I):
        name = ""
        phones = []
        for line in block.splitlines():
            key, _, val = line.partition(":")
            base = key.split(";")[0].upper()
            if "QUOTED-PRINTABLE" in key.upper():
                cs = re.search(r"CHARSET=([\w-]+)", key, re.I)
                val = quopri.decodestring(val.encode()).decode(cs.group(1) if cs else "utf-8", "replace")
            if base == "FN" and not name:
                name = val.strip()
            elif base == "N" and not name:
                name = " ".join(p for p in reversed(val.split(";")[:2]) if p).strip()
            elif base == "TEL":
                phones.append(val.strip())
        for p in phones:
            n = normalize_phone(p)
            if name and len(n) >= 10:
                out.append({"name": name, "phone": n})
    return out


def import_vcf(path: str) -> Dict[str, int]:
    with open(path, encoding="utf-8", errors="replace") as f:
        found = parse_vcf(f.read())
    contacts = _load()
    by_phone = {c["phone"]: c for c in contacts}
    added = 0
    for item in found:
        cur = by_phone.get(item["phone"])
        if cur is None:
            c = {"name": item["name"], "phone": item["phone"], "notes": ""}
            contacts.append(c)
            by_phone[c["phone"]] = c
            added += 1
        elif cur["name"] != item["name"]:
            # não sobrescreve edições manuais: só conta como já existente
            pass
    _save(contacts)
    return {"found": len(found), "added": added, "already_present": len(found) - added}
