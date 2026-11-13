"""Agenda de contatos local e editável.

Fica em whatsapp-bridge/store/contacts.json (fora do git). Tem prioridade sobre a
busca nos chats do WhatsApp, que às vezes falha. Formato:
    {"contacts": [{"name": "Maria", "phone": "5511999998888", "notes": ""}]}
O arquivo pode ser editado à mão.
"""
import contextlib
import fcntl
import json
import os
import quopri
import re
import shutil
import tempfile
from typing import Dict, Iterator, List, Optional

import config
from textutil import fold, phone_keys

CONTACTS_PATH = config.CONTACTS_PATH


class RecipientError(ValueError):
    """O destinatário não pôde ser resolvido com segurança (ausente ou ambíguo)."""

    def __init__(self, message: str, candidates: Optional[List[Dict[str, str]]] = None):
        super().__init__(message)
        self.candidates = candidates or []


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """Exclusão mútua entre processos: vários servidores MCP escrevem no mesmo arquivo,
    e ler-alterar-gravar sem trava perde edições."""
    path = os.path.abspath(CONTACTS_PATH) + ".lock"
    with open(path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


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


def phones_of(c: Dict[str, object]) -> List[str]:
    """Telefone principal + números extras do mesmo contato (envio por nome vai sempre para o principal)."""
    return [c["phone"]] + [p for p in (c.get("alt_phones") or []) if p]


def _save(contacts: List[Dict[str, str]]) -> None:
    path = os.path.abspath(CONTACTS_PATH)
    if os.path.exists(path):  # cópia da versão anterior: desfaz um `forget`/import enganado
        shutil.copy2(path, path + ".bak")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"contacts": sorted(contacts, key=lambda c: c["name"].lower())},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_contacts(query: Optional[str] = None) -> List[Dict[str, str]]:
    """Sem query, todos. Com query: todas as palavras no nome ou nas notas/apelidos (sem acento/maiúscula)
    ou o telefone contendo os dígitos (aceita número com ou sem o 9º dígito)."""
    contacts = _load()
    if query:
        words = fold(query).split()
        qd = re.sub(r"\D", "", query)
        contacts = [c for c in contacts
                    if (words and all(w in fold(c["name"] + " " + (c.get("notes") or "")) for w in words))
                    or (len(qd) >= 4 and any(qd in k for p in phones_of(c) for k in phone_keys(p)))]
    return contacts


def save_contact(name: str, phone: str, notes: str = "", also=None) -> Dict[str, str]:
    """Cria ou atualiza (a chave é o telefone principal). `also`: números extras da mesma pessoa."""
    name, phone = name.strip(), normalize_phone(phone)
    extra = [x for x in dict.fromkeys(normalize_phone(p) for p in (also or [])) if x and x != phone]
    if not name or not phone:
        raise ValueError("name e phone são obrigatórios")
    with _locked():
        contacts = _load()
        for c in contacts:
            if c["phone"] == phone:
                c["name"] = name
                if notes:
                    c["notes"] = notes
                if extra:
                    c["alt_phones"] = list(dict.fromkeys((c.get("alt_phones") or []) + extra))
                break
        else:
            c = {"name": name, "phone": phone, "notes": notes}
            if extra:
                c["alt_phones"] = extra
            contacts.append(c)
        _save(contacts)
    return c


def remove_contact(identifier: str) -> int:
    """Remove por telefone ou por nome exato. Retorna quantos removeu."""
    phone = normalize_phone(identifier)
    with _locked():
        contacts = _load()
        kept = [c for c in contacts
                if c["phone"] != phone and c["name"].lower() != identifier.strip().lower()]
        _save(kept)
    return len(contacts) - len(kept)


PROFILE_TEXT_FIELDS = ("tone", "address_as", "language", "signature", "avoid", "context", "allow_files")  # allow_files: on|off
REPLY_MODES = ("off", "draft", "auto")


def _find_in(contacts: List[Dict[str, object]], identifier: str) -> Optional[Dict[str, object]]:
    ident = (identifier or "").strip()
    if not ident:
        return None
    key = fold(ident)
    exact = [c for c in contacts if fold(c["name"]) == key]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise RecipientError(f"Mais de um contato chamado '{ident}'. Use o telefone.", exact[:5])
    digits = re.sub(r"\D", "", ident)
    if len(digits) >= 8:
        keys = phone_keys(digits) | phone_keys(normalize_phone(digits))
        for c in contacts:
            if any(phone_keys(p) & keys for p in phones_of(c)):
                return c
    return None


def find_contact(identifier: str) -> Optional[Dict[str, object]]:
    """Contato salvo por nome exato (sem acento/caixa) ou por telefone (com/sem 9º dígito).
    None se não houver; `RecipientError` se o nome for ambíguo."""
    return _find_in(_load(), identifier)


def get_profile(identifier: str) -> Optional[Dict[str, object]]:
    """Perfil de comunicação do contato ({} se ainda não tem) ou None se o contato não existe."""
    contact = find_contact(identifier)
    if contact is None:
        return None
    return dict(contact.get("profile") or {})


def set_profile(identifier: str, **fields: Optional[str]) -> Dict[str, object]:
    """Atualiza só os campos informados. `None` mantém o valor; string vazia apaga o campo.

    Campos: tone, address_as, language, signature, avoid, context (textos livres; context é o
    assunto/objetivo da conversa em andamento) e reply_mode
    (off | draft | auto). Levanta `RecipientError` se o contato não existir ou for ambíguo
    e `ValueError` para campo desconhecido ou reply_mode inválido.
    """
    unknown = set(fields) - set(PROFILE_TEXT_FIELDS) - {"reply_mode"}
    if unknown:
        raise ValueError(f"Campo(s) de perfil desconhecido(s): {', '.join(sorted(unknown))}")
    mode = fields.get("reply_mode")
    if mode is not None and mode != "" and mode not in REPLY_MODES:
        raise ValueError(f"reply_mode deve ser um de: {', '.join(REPLY_MODES)}")
    with _locked():
        contacts = _load()
        target = _find_in(contacts, identifier)
        if target is None:
            raise RecipientError(f"Contato '{identifier}' não encontrado na agenda. Salve-o antes com wpp save.")
        profile = dict(target.get("profile") or {})
        for name, value in fields.items():
            if value is None:
                continue
            value = value.strip()
            if value:
                profile[name] = value
            else:
                profile.pop(name, None)
        if profile:
            target["profile"] = profile
        else:
            target.pop("profile", None)
        _save(contacts)
        return dict(profile)


_PHONE_RE = re.compile(r"\+?[\d\s\-()]{8,}")


def group_chats(query: str) -> List[Dict[str, str]]:
    """Grupos (@g.us) conhecidos pelo bridge cujo nome contém todas as palavras da busca.
    Retorna [{"name", "phone": <jid>}] (o JID vai no campo phone para reaproveitar a exibição)."""
    import sqlite3

    words = fold(query).split()
    if not words or not os.path.exists(config.MESSAGES_DB_PATH):
        return []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{config.MESSAGES_DB_PATH}?mode=ro", uri=True, timeout=2)
        rows = conn.execute(
            "SELECT jid, name FROM chats WHERE jid LIKE '%@g.us' AND name IS NOT NULL AND name != ''"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if conn is not None:
            conn.close()
    return [{"name": n, "phone": j} for j, n in rows if all(w in fold(n) for w in words)]


def resolve(recipient: str) -> str:
    """Transforma o destinatário em algo que o bridge aceita: JID (com '@'), telefone
    (só dígitos) ou o telefone de um contato salvo.

    Levanta `RecipientError` se o nome não existir ou for ambíguo, com candidatos para o
    chamador mostrar. Antes o nome cru seguia para o bridge, que o tratava como número.
    """
    recipient = (recipient or "").strip()
    if not recipient:
        raise RecipientError("Recipient must be provided")
    if "@" in recipient:
        return recipient
    if _PHONE_RE.fullmatch(recipient):
        return re.sub(r"\D", "", recipient)
    key = fold(recipient)
    exact = [c for c in _load() if fold(c["name"]) == key]
    if len(exact) == 1:
        return exact[0]["phone"]
    if len(exact) > 1:
        raise RecipientError(
            f"Mais de um contato chamado '{recipient}'. Use o telefone.", exact[:5])
    groups = group_chats(recipient)
    exact_groups = [g for g in groups if fold(g["name"]) == key]
    if len(exact_groups) == 1:
        return exact_groups[0]["phone"]
    if len(groups) == 1 and not list_contacts(recipient):
        return groups[0]["phone"]
    if groups:
        raise RecipientError(
            f"Mais de um grupo/contato para '{recipient}'. Escolha pelo JID: "
            + ", ".join(f"{g['name']} ({g['phone']})" for g in groups[:5]), groups[:5])
    similar = list_contacts(recipient)[:5]
    hint = (" Candidatos: " + ", ".join(f"{c['name']} ({c['phone']})" for c in similar)) if similar else (
        " Se for uma pessoa, pergunte o número ao dono e registre com: wpp save \"<nome>\" <número>. Se for grupo, confira o nome com wpp chats \"<nome>\".")
    raise RecipientError(f"Contato '{recipient}' não encontrado na agenda.{hint}", similar)


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
    added = 0
    with _locked():
        contacts = _load()
        by_phone = {c["phone"]: c for c in contacts}
        for item in found:
            # Já existente (mesmo com nome diferente) não é sobrescrito: preserva edições manuais.
            if item["phone"] not in by_phone:
                c = {"name": item["name"], "phone": item["phone"], "notes": ""}
                contacts.append(c)
                by_phone[c["phone"]] = c
                added += 1
        _save(contacts)
    return {"found": len(found), "added": added, "already_present": len(found) - added}
