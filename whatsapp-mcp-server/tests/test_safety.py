import os

import pytest

import safety


def test_permite_arquivo_na_pasta_liberada(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_SEND_DIRS", str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert safety.check_send_path(str(f)) == os.path.realpath(f)


def test_bloqueia_fora_das_pastas(tmp_path, monkeypatch):
    liberada = tmp_path / "ok"
    liberada.mkdir()
    fora = tmp_path / "fora.txt"
    fora.write_text("x")
    monkeypatch.setenv("WHATSAPP_ALLOWED_SEND_DIRS", str(liberada))
    with pytest.raises(safety.PathNotAllowed):
        safety.check_send_path(str(fora))


def test_bloqueia_symlink_que_escapa(tmp_path, monkeypatch):
    liberada = tmp_path / "ok"
    liberada.mkdir()
    segredo = tmp_path / "segredo.txt"
    segredo.write_text("x")
    (liberada / "link.txt").symlink_to(segredo)
    monkeypatch.setenv("WHATSAPP_ALLOWED_SEND_DIRS", str(liberada))
    with pytest.raises(safety.PathNotAllowed):
        safety.check_send_path(str(liberada / "link.txt"))


def test_bloqueia_arquivos_internos_do_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_SEND_DIRS", str(tmp_path))
    for name in ("messages.db", "contacts.json", "whatsapp.db"):
        f = tmp_path / name
        f.write_text("x")
        with pytest.raises(safety.PathNotAllowed):
            safety.check_send_path(str(f))


def test_bloqueia_pasta_oculta_da_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    chave = home / ".ssh" / "id_ed25519"
    chave.write_text("x")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("WHATSAPP_ALLOWED_SEND_DIRS", raising=False)
    with pytest.raises(safety.PathNotAllowed):
        safety.check_send_path(str(chave))


def test_arquivo_inexistente(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_SEND_DIRS", str(tmp_path))
    with pytest.raises(safety.PathNotAllowed):
        safety.check_send_path(str(tmp_path / "nao-existe"))
