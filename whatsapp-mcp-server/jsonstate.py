"""Arquivo JSON de estado com trava entre processos e escrita atômica (permissão 600)."""
import contextlib
import fcntl
import json
import os
import tempfile
from typing import Any, Dict, Iterator


@contextlib.contextmanager
def locked(path: str) -> Iterator[None]:
    with open(os.path.abspath(path) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read(path: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return {**defaults, **json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(defaults)


def update(path: str, defaults: Dict[str, Any], **fields: Any) -> Dict[str, Any]:
    unknown = set(fields) - set(defaults)
    if unknown:
        raise ValueError(f"Campo(s) desconhecido(s): {', '.join(sorted(unknown))}")
    with locked(path):
        state = read(path, defaults)
        state.update(fields)
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return state
