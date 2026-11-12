import pytest

import contacts
import conversation
import live

JID = "5511999990000@s.whatsapp.net"


@pytest.fixture
def s(store, agenda, tmp_path, monkeypatch):
    monkeypatch.setattr(live.config, "LIVE_SESSION_PATH", str(tmp_path / "live_session.json"))
    monkeypatch.setattr(conversation.config, "CONVERSATION_STATE_PATH", str(tmp_path / "conversation_mode.json"))
    store.chat(JID, "Ana")
    agenda([{"name": "Ana", "phone": "5511999990000", "notes": ""}])
    calls = []
    store.calls = calls
    real = live.send

    def send_with_fake(text, how, sender=None, now=None):
        def fake(recipient, msg):
            calls.append((recipient, msg))
            return store.send_ok, "ok" if store.send_ok else "sem rede"
        return real(text, how, sender=fake, now=now)

    store.send_ok = True
    monkeypatch.setattr(live, "send", send_with_fake)
    return store


def test_start_registra_cursor_contexto_e_perfil(s):
    s.add(JID, "mensagem antiga", "2026-11-12 09:00:00")
    st = live.start("Ana", context="combinar o churrasco")
    assert st["active"] and st["mode"] == "approve" and st["cursor"] == 1
    assert st["chat_jid"] == JID and st["state"] == "waiting_reply"
    assert contacts.get_profile("Ana")["context"] == "combinar o churrasco"


def test_start_contato_inexistente(s):
    with pytest.raises(contacts.RecipientError):
        live.start("Fulano")


def test_start_recusa_resposta_em_dobro(s):
    contacts.set_profile("Ana", reply_mode="auto")
    conversation.update(agent_id="a", routine_id="r")
    conversation.set_enabled(True)
    with pytest.raises(ValueError, match="em dobro"):
        live.start("Ana")
    assert live.start("Ana", force=True)["active"]


def test_guard_bloqueia_fora_do_auto(s):
    live.start("Ana", mode="approve")
    ok, why = live.guard("oi, tudo bem?")
    assert not ok and "aprovação" in why[0]


def test_guard_auto_e_travas(s):
    live.start("Ana", mode="auto")
    assert live.guard("fechou, até sábado!")[0]
    assert not live.guard("manda o pix pra mim")[0]
    assert not live.guard("a senha é 1234")[0]
    assert not live.guard("pode me emprestar 200 reais?")[0]
    assert not live.guard("x" * 401)[0]


def test_limite_por_hora_e_sequencia(s):
    live.start("Ana", mode="auto")
    live.update(max_auto_per_hour=2, max_auto_streak=99)
    assert live.send("um", "auto", now=1000.0)["success"]
    assert live.send("dois", "auto", now=1010.0)["success"]
    out = live.send("três", "auto", now=1020.0)
    assert not out["success"] and "por hora" in out["blocked"][0]
    assert live.send("quatro", "auto", now=1000.0 + 3700)["success"]  # janela de 1 h passou
    live.update(max_auto_per_hour=99, max_auto_streak=2, auto_streak=2)
    assert "seguidas" in live.send("cinco", "auto", now=9999.0)["blocked"][0]
    live.resume()
    assert live.send("seis", "auto", now=10000.0)["success"]


def test_send_auto_registra_e_chama_o_sender(s):
    live.start("Ana", mode="auto")
    assert live.send("beleza!", "auto")["success"]
    assert s.calls == [("5511999990000", "beleza!")]
    st = live.load()
    assert st["sent"][0]["text"] == "beleza!" and st["auto_streak"] == 1 and st["state"] == "waiting_reply"


def test_send_bloqueado_nao_envia(s):
    live.start("Ana", mode="auto")
    assert not live.send("passa o pix", "auto")["success"]
    assert s.calls == []


def test_send_aprovado_funciona_em_modo_approve_e_zera_sequencia(s):
    live.start("Ana", mode="approve")
    live.update(auto_streak=5)
    assert live.send("combinado", "approved")["success"]
    assert live.load()["auto_streak"] == 0 and len(s.calls) == 1


def test_falha_no_envio_vira_precisa_de_voce(s):
    live.start("Ana", mode="approve")
    s.send_ok = False
    out = live.send("oi", "approved")
    assert not out["success"]
    st = live.load()
    assert st["state"] == "needs_you" and "sem rede" in st["state_note"] and st["sent"] == []


def test_send_sem_sessao_e_texto_vazio(s):
    with pytest.raises(ValueError):
        live.send("oi", "approved")
    live.start("Ana")
    with pytest.raises(ValueError):
        live.send("  ", "approved")
    with pytest.raises(ValueError):
        live.send("oi", "talvez")


def test_modo_estado_e_contexto(s):
    live.start("Ana")
    assert live.set_mode("paused")["state"] == "paused"
    assert live.set_mode("auto")["state"] == "waiting_reply"
    with pytest.raises(ValueError):
        live.set_mode("louco")
    with pytest.raises(ValueError):
        live.set_state("dormindo")
    assert live.set_state("drafting", "pensando")["state_note"] == "pensando"
    live.set_context("falar do filme")
    assert contacts.get_profile("Ana")["context"] == "falar do filme"


def test_painel_junta_os_dois_lados(s):
    s.add(JID, "e aí, bora?", "2026-11-12 10:00:00")
    live.start("Ana", mode="auto", context="sair sábado")
    live.send("bora sim!", "auto", now=None)
    s.add(JID, "fechou", "2026-11-12 23:59:00", from_me=True)
    p = live.panel()
    assert p["mode_label"].startswith("🤖") and p["context"] == "sair sábado"
    assert p["auto_last_hour"] == "1/8"
    texto = "\n".join(p["recent"])
    assert "Ana: e aí, bora?" in texto and "Eu (automático): bora sim!" in texto and "Você (celular): fechou" in texto
    assert "Sessão ao vivo com Ana" in live.panel_text()


def test_wait_entrega_mensagem_dele_apos_silencio(s):
    live.start("Ana")
    s.add(JID, "oi", "2026-11-12 10:00:00")
    s.add(JID, "tá aí?", "2026-11-12 10:00:03")
    code, text = live.wait(timeout=5, quiet=0, poll=0.01)
    assert code == 0 and "oi" in text and "tá aí?" in text
    assert live.wait(timeout=0.1, quiet=0, poll=0.01)[0] == 3  # nada novo


def test_wait_entrega_na_hora_o_que_voce_digita_no_celular(s):
    live.start("Ana")
    live.update(auto_streak=4)
    s.add(JID, "respondi aqui", "2026-11-12 10:00:00", from_me=True)
    code, text = live.wait(timeout=5, quiet=30, poll=0.01)
    assert code == 0 and "Eu: respondi aqui" in text and live.load()["auto_streak"] == 0


def test_wait_sessao_encerrada(s):
    live.start("Ana")
    live.stop()
    assert live.wait(timeout=1, poll=0.01)[0] == 4


def test_cli(s, capsys):
    assert live.cli(["start", "Ana", "--mode", "auto", "--context", "teste"]) == 0
    assert "Modo: 🤖" in capsys.readouterr().out
    assert live.cli(["panel", "--json"]) == 0
    assert '"contact": "Ana"' in capsys.readouterr().out
    assert live.cli(["send", "passa o pix", "--how", "auto"]) == 1
    assert live.cli(["send", "valeu!", "--how", "auto"]) == 0
    assert live.cli(["mode", "paused"]) == 0
    assert live.cli(["state", "dormindo"]) == 1
    assert live.cli(["stop"]) == 0
    assert "ENCERRADA" in live.panel_text()
    assert live.cli(["xyz"]) == 2
