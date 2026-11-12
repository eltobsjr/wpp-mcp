import pytest

import searchindex
import whatsapp

JID = "5511999990000@s.whatsapp.net"
GROUP = "120363000000000000@g.us"


@pytest.fixture
def conv(store):
    store.chat(JID, "Ana")
    store.chat(GROUP, "Time")
    store.add(JID, "oi, tudo bem?", "2026-11-12 09:00:00")
    store.add(JID, "tudo ótimo, e a reunião?", "2026-11-12 09:01:00", from_me=True)
    store.add(JID, "reunião é às 15h", "2026-11-12 09:02:00")
    store.add(JID, "", "2026-11-13 08:00:00", media="image")
    store.add(GROUP, "bora almoçar", "2026-11-12 12:00:00", sender="777")
    return store


def test_compacto_cronologico_com_cabecalho_unico(conv):
    out = whatsapp.list_messages(chat_jid=JID, limit=10)
    lines = out.split("\n")
    assert lines[0] == f"# Ana | {JID}"
    assert lines[1].startswith("2026-11-12 09:00 Contato: oi")
    assert lines[2] == "09:01 Eu: tudo ótimo, e a reunião?"  # mesmo dia: só a hora
    assert lines[4].startswith("2026-11-13 08:00 Contato: [image id=M4]")
    assert sum(1 for line in lines if line.startswith("#")) == 1


def test_sem_busca_nao_traz_contexto(conv):
    out = whatsapp.list_messages(chat_jid=JID, limit=1)
    assert out.count("\n") == 1  # cabeçalho + 1 mensagem


def test_busca_ignora_acento_e_traz_contexto(conv):
    out = whatsapp.list_messages(query="REUNIAO", limit=1)
    assert "reunião é às 15h" in out
    assert "tudo ótimo" in out  # vizinha anterior, vinda do contexto automático


def test_grupo_mostra_remetente(conv):
    out = whatsapp.list_messages(chat_jid=GROUP)
    assert "777: bora almoçar" in out


def test_formato_antigo_continua_disponivel(conv):
    out = whatsapp.list_messages(chat_jid=JID, limit=2, compact=False, include_context=False)
    assert "Chat: Ana" in out and "From:" in out


def test_indice_e_varredura_dao_o_mesmo_resultado(conv, monkeypatch):
    assert searchindex.sync(conv.db) is True
    com_indice = whatsapp.list_messages(query="reuniao", limit=10)
    monkeypatch.setattr(searchindex, "attach_if_ready", lambda conn, db: False)
    sem_indice = whatsapp.list_messages(query="reuniao", limit=10)
    assert com_indice == sem_indice != "No messages to display."


def test_indice_incremental_acha_mensagem_nova(conv):
    searchindex.sync(conv.db)
    conv.add(JID, "pagamento do aluguel", "2026-11-14 10:00:00")
    assert "aluguel" in whatsapp.list_messages(query="aluguel")


def test_indice_se_invalida_quando_o_banco_e_recriado(conv):
    searchindex.sync(conv.db)
    conv.conn.execute("DELETE FROM messages WHERE rowid > 1")
    conv.conn.commit()
    assert searchindex.sync(conv.db) is True
    assert "reuni" not in whatsapp.list_messages(query="reuniao")


def test_termo_curto_cai_na_varredura(conv):
    searchindex.sync(conv.db)
    assert "bora almoçar" in whatsapp.list_messages(query="bo")


def test_read_new_messages_cursor(conv):
    inicio = whatsapp.read_new_messages()
    assert inicio["messages"] == "" and inicio["next_cursor"] == 5
    conv.add(JID, "chegou agora", "2026-11-14 11:00:00")
    conv.add(JID, "resposta minha", "2026-11-14 11:01:00", from_me=True)
    novas = whatsapp.read_new_messages(cursor=inicio["next_cursor"])
    assert novas["count"] == 1 and "chegou agora" in novas["messages"]
    assert "resposta minha" not in novas["messages"]
    assert novas["next_cursor"] == 7 and novas["chat_jids"] == [JID]
    assert whatsapp.read_new_messages(cursor=novas["next_cursor"])["count"] == 0


def test_read_new_messages_paginacao(conv):
    p1 = whatsapp.read_new_messages(cursor=0, limit=2, include_from_me=True)
    assert p1["has_more"] and p1["count"] == 2
    p2 = whatsapp.read_new_messages(cursor=p1["next_cursor"], limit=10, include_from_me=True)
    assert p2["count"] == 3 and not p2["has_more"]


def test_read_message_log_do_fim_para_o_comeco(tmp_path, monkeypatch):
    log = tmp_path / "mensagens_log.txt"
    log.write_text("\n".join(f"linha {i} reunião" if i % 2 else f"linha {i}" for i in range(1, 11)) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(whatsapp, "MESSAGE_LOG_PATH", str(log))
    assert whatsapp.read_message_log(limit=2) == ["linha 10", "linha 9 reunião"]
    assert whatsapp.read_message_log(query="REUNIAO", limit=3) == [
        "linha 9 reunião", "linha 7 reunião", "linha 5 reunião"]
    assert whatsapp.read_message_log(query="inexistente") == []


@pytest.mark.parametrize("chunk", [1, 7, 16, 1000])
def test_reverse_blocks_nao_corta_linhas(tmp_path, chunk):
    log = tmp_path / "x.txt"
    linhas = [f"mensagem numero {i} ção" for i in range(1, 30)]
    log.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    lidas = [ln for block in whatsapp._reverse_blocks(str(log), chunk) for ln in block]
    assert lidas == list(reversed(linhas))
