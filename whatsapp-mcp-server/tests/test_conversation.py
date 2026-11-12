import pytest

import contacts
import conversation
from conversation import Watcher

JID = "5511999990000@s.whatsapp.net"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(store, agenda, tmp_path, monkeypatch):
    monkeypatch.setattr(conversation.config, "CONVERSATION_STATE_PATH", str(tmp_path / "conversation_mode.json"))
    store.chat(JID, "Ana")
    store.chat("120363@g.us", "Grupo")
    agenda([{"name": "Ana", "phone": "5511999990000", "notes": "", "profile": {"reply_mode": "draft"}}])
    conversation.update(agent_id="agent-x", routine_id="routine-y", debounce_seconds=20)
    sent = []
    clock = Clock()
    watcher = Watcher(notify=lambda t: sent.append(t) or True, clock=clock)
    store.sent, store.clock, store.watcher = sent, clock, watcher
    return store


def ligar():
    conversation.set_enabled(True)


def test_ligar_exige_rotina(store, tmp_path, monkeypatch):
    monkeypatch.setattr(conversation.config, "CONVERSATION_STATE_PATH", str(tmp_path / "s.json"))
    with pytest.raises(ValueError):
        conversation.set_enabled(True)


def test_desligado_nao_dispara(env):
    env.add(JID, "oi", "2026-11-12 10:00:00")
    env.clock.t += 100
    assert env.watcher.poll() == 0 and env.sent == []


def test_ligar_nao_responde_historico(env):
    env.add(JID, "mensagem antiga", "2026-11-12 09:00:00")
    ligar()
    env.clock.t += 100
    env.watcher.poll()
    env.clock.t += 100
    assert env.watcher.poll() == 0 and env.sent == []


def test_dispara_depois_do_debounce(env):
    ligar()
    env.watcher.poll()
    env.add(JID, "oi", "2026-11-12 10:00:00")
    env.add(JID, "tudo bem?", "2026-11-12 10:00:05")
    env.watcher.poll()
    env.clock.t += 10
    assert env.watcher.poll() == 0  # ainda dentro dos 20 s
    env.clock.t += 15
    assert env.watcher.poll() == 1
    trig = env.sent[0]
    assert (trig.chat_jid, trig.name, trig.mode, trig.count) == (JID, "Ana", "draft", 2)
    env.clock.t += 100
    assert env.watcher.poll() == 0 and len(env.sent) == 1  # não repete


def test_nova_mensagem_reinicia_a_espera(env):
    ligar()
    env.watcher.poll()
    env.add(JID, "oi", "2026-11-12 10:00:00")
    env.watcher.poll()
    env.clock.t += 15
    env.add(JID, "esqueci de dizer", "2026-11-12 10:00:15")
    env.watcher.poll()
    env.clock.t += 15
    assert env.watcher.poll() == 0
    env.clock.t += 10
    assert env.watcher.poll() == 1 and env.sent[0].count == 2


def test_ignora_grupo_e_contato_sem_reply_mode(env, agenda):
    ligar()
    env.watcher.poll()
    env.add("120363@g.us", "bom dia", "2026-11-12 10:00:00", sender="777")
    contacts.set_profile("Ana", reply_mode="off")
    env.add(JID, "oi", "2026-11-12 10:00:01")
    env.watcher.poll()
    env.clock.t += 60
    assert env.watcher.poll() == 0 and env.sent == []


def test_minha_resposta_cancela_a_espera(env):
    ligar()
    env.watcher.poll()
    env.add(JID, "oi", "2026-11-12 10:00:00")
    env.watcher.poll()
    env.add(JID, "respondi pelo celular", "2026-11-12 10:00:05", from_me=True)
    env.clock.t += 60
    assert env.watcher.poll() == 0 and env.sent == []


def test_mensagem_minha_nunca_dispara(env):
    ligar()
    env.watcher.poll()
    env.add(JID, "enviei", "2026-11-12 10:00:00", from_me=True)
    env.clock.t += 60
    assert env.watcher.poll() == 0 and env.sent == []


def test_limite_por_hora(env):
    conversation.update(max_triggers_per_hour=1)
    ligar()
    env.watcher.poll()
    for i in range(2):
        env.add(JID, f"oi {i}", f"2026-11-12 10:0{i}:00")
        env.watcher.poll()
        env.clock.t += 30
        env.watcher.poll()
        env.clock.t += 30
    assert len(env.sent) == 1
    env.clock.t += 3700
    env.add(JID, "depois de uma hora", "2026-11-12 12:00:00")
    env.watcher.poll()
    env.clock.t += 30
    assert env.watcher.poll() == 1


def test_falha_do_webhook_tenta_de_novo(env):
    respostas = iter([False, True])
    env.watcher.notify = lambda t: next(respostas)
    ligar()
    env.watcher.poll()
    env.add(JID, "oi", "2026-11-12 10:00:00")
    env.watcher.poll()
    env.clock.t += 25
    assert env.watcher.poll() == 0  # falhou, segue pendente
    env.clock.t += 25
    assert env.watcher.poll() == 1


def test_reinicio_do_vigia_nao_perde_pendencia(env):
    ligar()
    env.watcher.poll()
    env.add(JID, "oi", "2026-11-12 10:00:00")
    env.watcher.poll()  # pendente, cursor salvo antes dela
    novo = Watcher(notify=env.watcher.notify, clock=env.clock)
    novo.poll()
    env.clock.t += 30
    assert novo.poll() == 1 and env.sent[0].count == 1


def test_desligar_limpa_pendencias(env):
    ligar()
    env.watcher.poll()
    env.add(JID, "oi", "2026-11-12 10:00:00")
    env.watcher.poll()
    conversation.set_enabled(False)
    env.clock.t += 60
    assert env.watcher.poll() == 0 and not env.watcher.pending


def test_webhook_monta_url_cabecalhos_e_payload(env, tmp_path, monkeypatch, caplog):
    auto = tmp_path / "automation"
    auto.mkdir()
    (auto / "url").write_text("http://127.0.0.1:9999/\n")
    (auto / "headers").write_text("Authorization: Bearer SEGREDO-123\nX-Teste: 1\n")
    monkeypatch.setattr(conversation.config, "AUTOMATION_DIR", str(auto))
    visto = {}

    class Resp:
        ok = True
        status_code = 200

    def falso_post(url, headers, json, timeout):
        visto.update(url=url, headers=headers, json=json)
        return Resp()

    monkeypatch.setattr(conversation.requests, "post", falso_post)
    with caplog.at_level("DEBUG"):
        assert conversation.webhook_notify(conversation.Trigger(JID, "Ana", "draft", 2, 11))
    assert visto["url"] == "http://127.0.0.1:9999/v1/agents/agent-x/routines/routine-y/run"
    assert visto["headers"]["Authorization"] == "Bearer SEGREDO-123"
    assert "cursor=10" in visto["json"]["payload"] and JID in visto["json"]["payload"]
    assert "SEGREDO" not in caplog.text


def test_webhook_sem_arquivos_de_automacao(env, tmp_path, monkeypatch):
    monkeypatch.setattr(conversation.config, "AUTOMATION_DIR", str(tmp_path / "nao-existe"))
    assert conversation.webhook_notify(conversation.Trigger(JID, "Ana", "draft", 1, 5)) is False


def test_ferramenta_conversation_mode(env):
    import main

    estado = main.conversation_mode()
    assert estado["enabled"] is False and estado["routine_configured"] is True
    assert estado["eligible_contacts"] == [{"name": "Ana", "phone": "5511999990000", "reply_mode": "draft"}]
    assert main.conversation_mode(enabled=True)["enabled"] is True
    assert main.conversation_mode(enabled=False)["enabled"] is False
    conversation.update(routine_id="")
    erro = main.conversation_mode(enabled=True)
    assert erro["success"] is False and "routine_id" in erro["message"]


def test_cli_status_ligar_desligar_e_contato(env, capsys):
    assert conversation.cli(["status"]) == 0
    assert "desligado" in capsys.readouterr().out
    assert conversation.cli(["on"]) == 0
    assert "LIGADO" in capsys.readouterr().out
    assert conversation.cli(["contact", "Ana", "auto"]) == 0
    assert "Ana (auto)" in capsys.readouterr().out
    assert contacts.get_profile("Ana")["reply_mode"] == "auto"
    assert conversation.cli(["contact", "5511999990000", "off"]) == 0
    assert "Ana" not in capsys.readouterr().out.split("Contatos atendidos")[1]
    assert conversation.cli(["off"]) == 0
    assert "desligado" in capsys.readouterr().out


def test_cli_erros(env, capsys):
    assert conversation.cli(["contact", "Fulano", "draft"]) == 1
    assert "não encontrado" in capsys.readouterr().err
    assert conversation.cli(["contact", "Ana", "sempre"]) == 1
    assert "modo deve ser" in capsys.readouterr().err
    conversation.update(routine_id="")
    assert conversation.cli(["on"]) == 1
    assert conversation.cli(["xyz"]) == 2
