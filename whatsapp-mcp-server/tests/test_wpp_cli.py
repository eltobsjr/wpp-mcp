"""wpp_cli: trava do envio automático e registro do que é enviado pela API (o bridge não o grava)."""

import json
import time

import pytest

import contacts
import conversation
import wpp_cli

AUTO = "5589999990001"
DRAFT = "5589999990002"


@pytest.fixture
def env(agenda, tmp_path, monkeypatch):
    agenda(
        [
            {"name": "Ana Auto", "phone": AUTO, "notes": "", "profile": {"reply_mode": "auto"}},
            {"name": "Duda Draft", "phone": DRAFT, "notes": "", "profile": {"reply_mode": "draft"}},
        ]
    )
    monkeypatch.setattr(wpp_cli, "_SENT_LOG", str(tmp_path / "sent_log.json"))
    monkeypatch.setattr(wpp_cli, "_AUTO_STATE", str(tmp_path / "auto_replies.json"))
    monkeypatch.setattr(conversation, "load", lambda: {"enabled": True})
    monkeypatch.setattr(wpp_cli.identity, "phone_of", lambda jid: jid.split("@")[0])
    sent = []
    return sent, (lambda chat, text: sent.append((chat, text)) or (True, "ok"))


def jid(phone):
    return f"{phone}@s.whatsapp.net"


def test_reply_passa_no_caso_simples_e_registra(env):
    sent, sender = env
    r = wpp_cli.auto_reply(jid(AUTO), "Oi! Tudo bem por aqui.", sender=sender)
    assert r["success"] and len(sent) == 1
    assert wpp_cli.sent_for(jid(AUTO))[0]["text"] == "Oi! Tudo bem por aqui."


@pytest.mark.parametrize(
    "chat,text,motivo",
    [
        (jid(DRAFT), "oi", "reply_mode"),
        ("120363000000000001@g.us", "oi", "1:1"),
        (jid("5589888880000"), "oi", "agenda"),
        (jid(AUTO), "me manda o pix", "sensível"),
        (jid(AUTO), "veja http://x.co/a", "link"),
        (jid(AUTO), "liga 89994364683", "número longo"),
        (jid(AUTO), "a" * 401, "400"),
        (jid(AUTO), "   ", "vazio"),
    ],
)
def test_reply_bloqueia(env, chat, text, motivo):
    sent, sender = env
    r = wpp_cli.auto_reply(chat, text, sender=sender)
    assert not r["success"] and sent == []
    assert any(motivo in m for m in r["blocked"])


def test_reply_bloqueia_com_modo_desligado(env, monkeypatch):
    sent, sender = env
    monkeypatch.setattr(conversation, "load", lambda: {"enabled": False})
    assert not wpp_cli.auto_reply(jid(AUTO), "oi", sender=sender)["success"] and sent == []


def test_limite_por_hora_bloqueia_o_nono(env):
    sent, sender = env
    t0 = time.time()
    for i in range(wpp_cli.AUTO_MAX_PER_HOUR):
        assert wpp_cli.auto_reply(jid(AUTO), f"ok {i}", sender=sender, now=t0 + i)["success"]
    r = wpp_cli.auto_reply(jid(AUTO), "mais um", sender=sender, now=t0 + 10)
    assert not r["success"] and len(sent) == wpp_cli.AUTO_MAX_PER_HOUR


def test_registro_casa_nome_telefone_e_jid(env):
    wpp_cli.log_sent("Ana Auto", "pelo nome", "manual")  # `wpp send "Ana Auto"` registra o nome
    wpp_cli.log_sent(AUTO, "pelo número", "manual")
    wpp_cli.log_sent(jid(AUTO), "pelo jid", "auto")
    for consulta in ("Ana Auto", AUTO, jid(AUTO), "52480574513257@lid".replace("52480574513257", AUTO)):
        assert [m["text"] for m in wpp_cli.sent_for(consulta)] == ["pelo nome", "pelo número", "pelo jid"]


def test_registro_nao_mistura_conversas(env):
    wpp_cli.log_sent(AUTO, "da Ana", "manual")
    wpp_cli.log_sent(DRAFT, "da Duda", "manual")
    assert [m["text"] for m in wpp_cli.sent_for(AUTO)] == ["da Ana"]


def test_profile_acha_contato_por_lid_nome_e_telefone(env):
    for ident in (AUTO, jid(AUTO), "Ana Auto"):
        r = wpp_cli.profile_of(ident)
        assert r["found"] and r["reply_mode"] == "auto"
    assert wpp_cli.profile_of(jid(DRAFT))["reply_mode"] == "draft"
    assert wpp_cli.profile_of("5500000000000@s.whatsapp.net")["found"] is False


def test_timeline_mistura_os_dois_lados_em_ordem(env, tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "messages.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TEXT,"
        " is_from_me BOOLEAN, media_type TEXT);"
    )
    con.execute(
        "INSERT INTO messages VALUES ('1', ?, 'x', 'Oi filho foi bem', '2026-11-12 23:17:40-03:00', 0, NULL)",
        (jid(AUTO),),
    )
    con.execute(
        "INSERT INTO messages VALUES ('2', ?, 'x', 'Nada não, dormi', '2026-11-12 23:26:40-03:00', 0, NULL)",
        (jid(AUTO),),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(wpp_cli.identity, "equivalent_jids", lambda c: {c})
    from datetime import datetime

    wpp_cli.log_sent(
        AUTO, "Como foi seu dia?", "manual", now=datetime.fromisoformat("2026-11-12 23:17:29-03:00").timestamp()
    )
    wpp_cli.log_sent(
        AUTO, "O que a senhora fez hoje?", "manual", now=datetime.fromisoformat("2026-11-12 23:26:28-03:00").timestamp()
    )
    textos = [x[2] for x in wpp_cli.timeline(jid(AUTO), 10, db_path=str(db))]
    assert textos == ["Como foi seu dia?", "Oi filho foi bem", "O que a senhora fez hoje?", "Nada não, dormi"]


def test_edit_contact_auto_exige_confirmacao(env):
    r = wpp_cli.edit_contact("Duda Draft", mode="auto")
    assert not r["success"] and "--yes" in r["message"]
    assert contacts.get_profile("Duda Draft")["reply_mode"] == "draft"
    assert wpp_cli.edit_contact("Duda Draft", mode="auto", yes=True)["profile"]["reply_mode"] == "auto"


def test_edit_contact_muda_contexto_sem_tocar_no_modo(env):
    r = wpp_cli.edit_contact("Ana Auto", context="combinar o churrasco")
    assert r["success"] and r["profile"]["context"] == "combinar o churrasco" and r["profile"]["reply_mode"] == "auto"


def test_edit_contact_inexistente(env):
    assert not wpp_cli.edit_contact("Ninguém")["success"]


def test_set_auto_exige_rotina_configurada(env, monkeypatch):
    estado = {"enabled": False, "agent_id": "", "routine_id": ""}
    monkeypatch.setattr(conversation, "load", lambda: estado)
    monkeypatch.setattr(conversation, "set_enabled", lambda v: estado.update(enabled=v))
    assert not wpp_cli.set_auto(True)["success"]
    estado.update(agent_id="a", routine_id="r")
    assert wpp_cli.set_auto(True) == {"success": True, "enabled": True}
    assert wpp_cli.set_auto(False)["enabled"] is False


def test_doctor_aponta_o_que_falta(env, monkeypatch, tmp_path):
    monkeypatch.setattr(wpp_cli, "_http_ok", lambda *a, **k: False)
    monkeypatch.setattr(wpp_cli, "_service_active", lambda n: False)
    monkeypatch.setattr(conversation, "load", lambda: {"enabled": False, "agent_id": "", "routine_id": ""})
    monkeypatch.setattr(wpp_cli.config, "AUTOMATION_DIR", str(tmp_path / "nada"))
    falhas = [rot for ok, rot, _, _ in wpp_cli.doctor() if not ok]
    assert "bridge do WhatsApp responde" in falhas
    assert "vigia (wpp-conversation) rodando" in falhas
    assert "modo conversacional ligado" in falhas
    assert "OpenBot aberto e alcançável" in falhas


def test_doctor_tudo_certo(env, monkeypatch, tmp_path):
    (tmp_path / "url").write_text("http://127.0.0.1:1/")
    monkeypatch.setattr(wpp_cli, "_http_ok", lambda *a, **k: True)
    monkeypatch.setattr(wpp_cli, "_service_active", lambda n: True)
    monkeypatch.setattr(conversation, "load", lambda: {"enabled": True, "agent_id": "a", "routine_id": "r"})
    monkeypatch.setattr(wpp_cli.config, "AUTOMATION_DIR", str(tmp_path))
    assert all(ok for ok, _, _, _ in wpp_cli.doctor())


def test_get_contact_profile_do_mcp_acha_por_jid(env):
    from conftest import carregar_mcp

    main = carregar_mcp()
    r = main.get_contact_profile(jid(AUTO))
    assert r["success"] and r["profile"]["reply_mode"] == "auto"
    assert main.get_contact_profile("5500000000000@s.whatsapp.net")["success"] is False


# ---------------- paridade com o MCP e comandos portados ----------------

# toda ferramenta do MCP precisa ter um comando equivalente no wpp (ou estar coberta por composição)
PARIDADE = {
    "search_contacts": "find",
    "list_saved_contacts": "agenda",
    "save_contact": "save",
    "remove_saved_contact": "forget",
    "import_contacts_vcf": "import-vcf",
    "set_contact_profile": "contact",
    "get_contact_profile": "profile",
    "prepare_message": "thread",  # perfil + histórico: wpp profile + wpp thread
    "conversation_mode": "auto",
    "list_messages": "msgs",
    "read_new_messages": "new",
    "list_chats": "chats",
    "get_contact_chats": "chats",
    "get_chat": "chat",
    "get_direct_chat_by_contact": "chat",
    "get_last_interaction": "last",
    "get_message_context": "context",
    "read_message_log": "log",
    "send_message": "send",
    "send_file": "send-file",
    "send_audio_message": "audio",
    "download_media": "media",
}


def _comandos():
    import argparse

    sub = [a for a in wpp_cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction)][0]
    return set(sub.choices)


def test_toda_ferramenta_do_mcp_tem_equivalente_no_wpp():
    import asyncio

    from conftest import carregar_mcp

    main = carregar_mcp()

    tools = {t.name for t in asyncio.run(main.mcp.list_tools())}
    assert tools - set(PARIDADE) == set(), "ferramenta nova no MCP sem equivalente mapeado no wpp"
    assert set(PARIDADE.values()) <= _comando_set()


def _comando_set():
    return _comandos()


def test_alias_contacts_e_versao():
    p = wpp_cli.build_parser()
    assert p.parse_args(["contacts"]).cmd == "contacts"  # alias aceito; run() trata como agenda
    assert wpp_cli.VERSION.count(".") == 2


def test_send_path_bloqueia_chave_ssh_e_banco_do_bridge(env, tmp_path):
    import os

    for caminho in (os.path.expanduser("~/.ssh/id_ed25519"), str(tmp_path / "messages.db")):
        r = wpp_cli.send_path(jid(AUTO), caminho, file_sender=lambda *a: (True, "ENVIOU"))
        assert r["success"] is False


def test_send_path_envia_arquivo_normal_e_registra(env, tmp_path):
    arq = tmp_path / "foto.txt"
    arq.write_text("x")
    enviados = []
    r = wpp_cli.send_path(jid(AUTO), str(arq), file_sender=lambda d, p: enviados.append(p) or (True, "ok"))
    assert r["success"] and len(enviados) == 1
    assert wpp_cli.sent_for(AUTO)[-1]["text"] == "[arquivo] foto.txt"
    a = wpp_cli.send_path(jid(AUTO), str(arq), audio=True, audio_sender=lambda d, p: (True, "ok"))
    assert a["success"] and wpp_cli.sent_for(AUTO)[-1]["text"] == "[áudio] foto.txt"


def test_send_path_recusa_pasta_oculta(env):
    import os

    r = wpp_cli.send_path(jid(AUTO), os.path.expanduser("~/.cache/qualquer.txt"), file_sender=lambda *a: (True, "x"))
    assert r["success"] is False


def test_forget_exige_yes_e_save_grava(env):
    assert wpp_cli.save_contact("Novo Nome", "5589999990009")["success"]
    assert not wpp_cli.forget_contact("Novo Nome")["success"]
    assert contacts.find_contact("Novo Nome") is not None
    assert wpp_cli.forget_contact("Novo Nome", yes=True)["success"]
    assert contacts.find_contact("Novo Nome") is None
    assert not wpp_cli.forget_contact("Novo Nome", yes=True)["success"]


def test_find_prioriza_agenda(env):
    r = wpp_cli.find("Ana")
    assert r and r[0]["source"] == "agenda" and r[0]["phone"] == AUTO


def test_edit_contact_idioma_e_assinatura(env):
    r = wpp_cli.edit_contact("Ana Auto", language="en", signature="— Elton")
    assert r["profile"]["language"] == "en" and r["profile"]["signature"] == "— Elton"


def test_configure_auto(env, monkeypatch):
    estado = {"enabled": False, "debounce_seconds": 20, "max_triggers_per_hour": 6, "agent_id": "", "routine_id": ""}
    monkeypatch.setattr(conversation, "load", lambda: estado)
    monkeypatch.setattr(conversation, "update", lambda **k: estado.update(k))
    r = wpp_cli.configure_auto(debounce=30, max_per_hour=4, agent_id="a", routine_id="r")
    assert r["debounce_seconds"] == 30 and r["max_triggers_per_hour"] == 4 and r["routine_configured"]


def test_message_context_marca_a_mensagem_pedida(env, monkeypatch):
    from datetime import datetime

    from whatsapp import Message, MessageContext

    def msg(i, txt, me=False):
        return Message(datetime(2026, 11, 12, 23, 17, i), "Contato", txt, me, "c@s", str(i))

    ctx = MessageContext(message=msg(2, "meio"), before=[msg(1, "antes")], after=[msg(3, "depois", True)])
    monkeypatch.setattr(wpp_cli, "get_message_context", lambda *a: ctx)
    linhas = wpp_cli.message_context("2")
    assert [x.split(": ", 1)[1] for x in linhas] == ["antes", "meio", "depois"]
    assert linhas[1].startswith(">>") and linhas[2].endswith("depois") and "EU:" in linhas[2]


def test_main_transforma_erro_em_recusa_limpa(capsys, monkeypatch):
    def explode(a):
        raise ValueError("Message with ID 0 not found")

    monkeypatch.setattr(wpp_cli, "run", explode)
    with pytest.raises(SystemExit) as e:
        wpp_cli.main(["last", "x"])
    assert e.value.code == 1
    saida = json.loads(capsys.readouterr().out)
    assert saida["success"] is False and "not found" in saida["message"]


def test_doctor_sem_contato_ligado_e_aviso_nao_falha(env, monkeypatch, tmp_path):
    (tmp_path / "url").write_text("http://127.0.0.1:1/")
    monkeypatch.setattr(wpp_cli, "_http_ok", lambda *a, **k: True)
    monkeypatch.setattr(wpp_cli, "_service_active", lambda n: True)
    monkeypatch.setattr(conversation, "load", lambda: {"enabled": True, "agent_id": "a", "routine_id": "r"})
    monkeypatch.setattr(conversation, "eligible_contacts", lambda: [])
    monkeypatch.setattr(wpp_cli.config, "AUTOMATION_DIR", str(tmp_path))
    itens = {rot: (ok, crit) for ok, rot, _, crit in wpp_cli.doctor()}
    assert itens["contato em draft/auto"] == (False, False)
    assert all(ok for rot, (ok, crit) in itens.items() if crit)


# ---------------- dedupe de contatos ----------------


def _ag(*itens):
    return [
        {"name": n, "phone": p, "notes": nt, "profile": pf} if pf else {"name": n, "phone": p, "notes": nt}
        for n, p, nt, pf in itens
    ]


def test_dedupe_junta_mesmo_numero_com_e_sem_9_e_com_zero():
    ag = _ag(
        ("Maria Souza", "558988880001", "", None),
        ("Mariinha", "5589988880001", "", None),
        ("Maria Souza", "089988880001", "", None),
        ("Outra", "5589999990001", "", None),
    )
    nova, juntados, _ = wpp_cli.dedupe_plan(ag)
    assert len(nova) == 2 and len(juntados) == 1
    unido = [c for c in nova if c["phone"] == "5589988880001"][0]  # mantém o formato canônico com 9
    assert "também salvo como" in unido["notes"]


def test_dedupe_nao_junta_mesmo_nome_com_numero_diferente():
    ag = _ag(("Breno", "558988880002", "", None), ("Breno", "558988880003", "", None))
    nova, juntados, avisos = wpp_cli.dedupe_plan(ag)
    assert len(nova) == 2 and juntados == [] and len(avisos) == 1


def test_dedupe_preserva_perfil_e_usa_o_modo_mais_restritivo():
    ag = _ag(
        ("Caio", "5581988880004", "", {"tone": "informal", "reply_mode": "auto"}),
        ("O Bom", "558188880004", "amigo", {"context": "futebol", "reply_mode": "draft"}),
    )
    nova, juntados, _ = wpp_cli.dedupe_plan(ag)
    assert len(nova) == 1
    perfil = nova[0]["profile"]
    assert perfil["tone"] == "informal" and perfil["context"] == "futebol" and perfil["reply_mode"] == "draft"
    assert "amigo" in nova[0]["notes"]


def test_dedupe_simula_por_padrao_e_so_grava_com_apply(env):
    agenda_path = contacts.CONTACTS_PATH
    antes = open(agenda_path, encoding="utf-8").read()
    r = wpp_cli.dedupe()
    assert r["applied"] is False and open(agenda_path, encoding="utf-8").read() == antes


def test_busca_acha_apelido_nas_notas(tmp_path, monkeypatch):
    import contacts as saved

    monkeypatch.setattr(saved, "CONTACTS_PATH", str(tmp_path / "c.json"))
    saved.save_contact("Maria Souza", "5511999990001", notes="também salvo como Mariinha")
    achou = saved.list_contacts("mariinha")
    assert [c["name"] for c in achou] == ["Maria Souza"]

def test_nome_desconhecido_orienta_a_perguntar_o_numero():
    import pytest

    import contacts as saved

    with pytest.raises(saved.RecipientError) as e:
        saved.resolve("Fulano Inexistente")
    assert "pergunte o número" in str(e.value) and "wpp save" in str(e.value)


def _db_com_grupos(tmp_path):
    import sqlite3

    p = tmp_path / "m.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE chats (jid TEXT, name TEXT)")
    c.executemany("INSERT INTO chats VALUES (?,?)", [
        ("111@g.us", "Família Souza"), ("222@g.us", "Promoções A"), ("333@g.us", "Promoções B"), ("5511@s.whatsapp.net", "Zé")])
    c.commit()
    c.close()
    return str(p)


def test_grupo_resolve_por_nome_e_ambiguo_pede_escolha(tmp_path, monkeypatch):
    import pytest

    import contacts as saved

    monkeypatch.setattr(saved.config, "MESSAGES_DB_PATH", _db_com_grupos(tmp_path))
    assert saved.resolve("familia souza") == "111@g.us"
    assert saved.resolve("Promoções A") == "222@g.us"
    with pytest.raises(saved.RecipientError):
        saved.resolve("promocoes")
    assert [g["name"] for g in saved.group_chats("promocoes")] == ["Promoções A", "Promoções B"]
    assert saved.group_chats("zé") == []  # só grupos


def test_contato_com_dois_numeros_envia_ao_principal_e_reconhece_o_extra(tmp_path, monkeypatch):
    import contacts as saved

    monkeypatch.setattr(saved, "CONTACTS_PATH", str(tmp_path / "c.json"))
    saved.save_contact("Breno", "5511999990001", also=["5511999990002", "5511999990001"])
    c = saved.find_contact("5511999990002")
    assert c["name"] == "Breno" and c["phone"] == "5511999990001" and c["alt_phones"] == ["5511999990002"]
    assert saved.resolve("Breno") == "5511999990001"  # por nome: sempre o principal
    assert saved.list_contacts("99990002")[0]["name"] == "Breno"


def test_forget_deixa_copia_bak(tmp_path, monkeypatch):
    import contacts as saved

    monkeypatch.setattr(saved, "CONTACTS_PATH", str(tmp_path / "c.json"))
    saved.save_contact("Ana", "5511999990003")
    saved.save_contact("Caio", "5511999990004")
    saved.remove_contact("Ana")
    assert "Ana" in (tmp_path / "c.json.bak").read_text(encoding="utf-8")


def test_timeline_nao_duplica_mensagem_enviada_pela_api(tmp_path, monkeypatch):
    import sqlite3
    import time as _t
    from datetime import datetime

    db = tmp_path / "m.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN, media_type TEXT)")
    agora = _t.time()
    c.execute("INSERT INTO messages VALUES ('A','5511@s.whatsapp.net','eu','oi',?,1,'')", (datetime.fromtimestamp(agora).isoformat(),))
    c.commit()
    c.close()
    monkeypatch.setattr(wpp_cli, "sent_for", lambda chat, limit: [{"t": agora, "text": "oi", "how": "manual"}])
    itens = wpp_cli.timeline("5511@s.whatsapp.net", 10, db_path=str(db))
    assert [(w, x) for _, w, x in itens] == [("EU (manual)", "oi")]


def test_grupo_so_aceita_off_ou_draft_e_vigia_so_conta_mencoes(tmp_path, monkeypatch):
    import groups

    monkeypatch.setattr(groups.config, "GROUPS_PATH", str(tmp_path / "g.json"))
    g = groups.set_group("1@g.us", "Família", mode="draft", keywords=["maria"])
    assert g["reply_mode"] == "draft"
    import pytest

    with pytest.raises(ValueError):
        groups.set_group("1@g.us", "Família", mode="auto")
    with pytest.raises(ValueError):
        groups.set_group("5511@s.whatsapp.net", "x", mode="draft")
    assert groups.mention_patterns(g, owner="5511999990000") == ["@5511999990000", "maria"]
    assert conversation.eligibility("1@g.us") == {"name": "Família", "mode": "draft"}
    assert conversation.eligibility("2@g.us") is None
    groups.set_group("1@g.us", "Família", mode="off")
    assert conversation.eligibility("1@g.us") is None


def test_vigia_aciona_grupo_so_com_mencao(tmp_path, monkeypatch):
    import sqlite3

    import groups
    import whatsapp

    db = tmp_path / "m.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE messages (id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TEXT, is_from_me INTEGER)")
    c.executemany("INSERT INTO messages VALUES (?,?,?,?,?,0)", [
        ("a", "1@g.us", "x", "bom dia pessoal", "t"),
        ("b", "1@g.us", "x", "oi @5511999990000 vem?", "t"),
        ("c", "1@g.us", "x", "alguém viu a Maria?", "t"),
    ])
    c.commit()
    c.close()
    monkeypatch.setattr(whatsapp, "_connect", lambda: sqlite3.connect(db))
    monkeypatch.setattr(groups.config, "GROUPS_PATH", str(tmp_path / "g.json"))
    monkeypatch.setattr(groups, "owner_digits", lambda: "5511999990000")
    groups.set_group("1@g.us", "Família", mode="draft", keywords=["maria"])
    w = conversation.Watcher(notify=lambda t: True)
    w.read_cursor = 0
    incoming = {}
    w._group_mentions(incoming)
    assert incoming["1@g.us"][0] == 2  # a menção e a palavra-chave; "bom dia" não conta


def test_arquivo_no_modo_auto_exige_permissao_pasta_tipo_e_tamanho(tmp_path, monkeypatch):
    envio = tmp_path / "Envio"
    envio.mkdir()
    ok_file = envio / "a.pdf"
    ok_file.write_bytes(b"x")
    fora = tmp_path / "b.pdf"
    fora.write_bytes(b"x")
    exe = envio / "c.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(wpp_cli, "AUTO_FILE_DIR", str(envio))
    monkeypatch.setattr(wpp_cli, "check_auto_reply", lambda chat, text, now=None: (True, []))
    contato = {"name": "Ana", "phone": "5511999990009", "profile": {"reply_mode": "auto", "allow_files": "on"}}
    monkeypatch.setattr(wpp_cli.saved_contacts, "find_contact", lambda i: contato)
    ok, why = wpp_cli.check_auto_file("5511999990009", str(ok_file), "oi")
    assert ok and why == []
    assert "fora de" in " ".join(wpp_cli.check_auto_file("5511999990009", str(fora))[1])
    assert "tipo de arquivo" in " ".join(wpp_cli.check_auto_file("5511999990009", str(exe))[1])
    contato["profile"]["allow_files"] = "off"
    assert "sem permissão" in " ".join(wpp_cli.check_auto_file("5511999990009", str(ok_file))[1])
