"""Normalização de texto e telefone, compartilhada pela agenda e pela busca."""
import re
import unicodedata
from typing import Set

# Marcas de acento combinantes (U+0300–U+036F) que o NFKD separa das letras. Regex compilada
# roda em C; o filtro caractere a caractere em Python era o gargalo das buscas.
_COMBINING = re.compile("[\u0300-\u036f]")


def fold(text) -> str:
    """Minúsculas e sem acento: 'João' e 'JOAO' viram 'joao'."""
    if not text:
        return ""
    text = str(text)
    if text.isascii():  # caminho rápido: ASCII não tem acento para remover
        return text.casefold()
    return _COMBINING.sub("", unicodedata.normalize("NFKD", text)).casefold()


def phone_keys(phone: str) -> Set[str]:
    """Variantes do mesmo número. No Brasil o 9º dígito é opcional em contas antigas:
    5589994127883 e 558994127883 são a mesma pessoa."""
    p = re.sub(r"\D", "", phone or "")
    keys = {p} if p else set()
    if p.startswith("55"):
        if len(p) == 13 and p[4] == "9":
            keys.add(p[:4] + p[5:])
        elif len(p) == 12 and p[4] in "6789":
            keys.add(p[:4] + "9" + p[4:])
    return keys
