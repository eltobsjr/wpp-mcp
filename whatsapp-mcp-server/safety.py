"""Travas de segurança para envio de arquivos.

O servidor MCP roda com o usuário logado, então sem trava qualquer caminho local
poderia ser enviado por WhatsApp (chaves SSH, bancos do próprio bridge...).
"""
import os
import tempfile
from typing import List

# Arquivos internos do bridge que nunca devem sair por mensagem.
_PROTECTED_NAMES = {"messages.db", "whatsapp.db", "contacts.json", "mensagens_log.txt"}


class PathNotAllowed(ValueError):
    """O arquivo está fora das pastas permitidas ou é sensível."""


def allowed_roots() -> List[str]:
    """Pastas de onde se pode enviar. Padrão: a home do usuário e a pasta temporária.
    `WHATSAPP_ALLOWED_SEND_DIRS` (separado por ':') substitui o padrão."""
    raw = os.environ.get("WHATSAPP_ALLOWED_SEND_DIRS")
    roots = [p for p in raw.split(":") if p] if raw else [os.path.expanduser("~"), tempfile.gettempdir()]
    return [os.path.realpath(p) for p in roots]


def check_send_path(path: str) -> str:
    """Devolve o caminho real se o envio é permitido; senão levanta PathNotAllowed."""
    real = os.path.realpath(os.path.expanduser(path or ""))
    if not os.path.isfile(real):
        raise PathNotAllowed(f"Media file not found: {path}")
    if os.path.basename(real) in _PROTECTED_NAMES:
        raise PathNotAllowed("Esse arquivo é interno do bridge e não pode ser enviado.")
    home = os.path.realpath(os.path.expanduser("~"))
    for root in allowed_roots():
        if real == root or real.startswith(root + os.sep):
            # Dentro da home, pastas ocultas (.ssh, .config, .gnupg...) ficam de fora,
            # exceto se o usuário listou essa pasta de propósito em WHATSAPP_ALLOWED_SEND_DIRS.
            if root == home:
                rel = os.path.relpath(real, home)
                if any(part.startswith(".") for part in rel.split(os.sep)):
                    raise PathNotAllowed("Pastas ocultas da home não podem ser enviadas.")
            return real
    raise PathNotAllowed(f"Caminho fora das pastas permitidas: {path}")
