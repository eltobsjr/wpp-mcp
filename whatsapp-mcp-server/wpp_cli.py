"""CLI fina sobre o mesmo código do servidor MCP (contatos, resolução de nomes, travas de envio).
Existe para agentes usarem por shell em vez de carregar o MCP inteiro em todo turno.
Saída curta de propósito (economiza tokens). Uso: wpp --help"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

import config
import contacts as saved_contacts
import conversation
import groups
import identity
import jsonstate
import safety
import whatsapp
from whatsapp import (
    download_media,
    edit_message,
    format_chats_compact,
    get_chat,
    get_direct_chat_by_contact,
    get_last_interaction,
    get_message_context,
    list_chats,
    list_messages,
    mark_read,
    react_message,
    read_message_log,
    read_new_messages,
    revoke_message,
    search_contacts,
    send_audio_message,
    send_file,
    send_message,
)

# ---- o bridge NÃO grava no banco o que é enviado pela API: guardamos aqui para o agente ver os dois lados ----
_SENT_LOG = os.environ.get("WHATSAPP_SENT_LOG") or os.path.join(config.STORE_DIR, "sent_log.json")


def _chat_key(chat: str) -> str:
    """Chave única da conversa = telefone. JID (@lid ou @s.whatsapp.net), nome salvo e número viram o mesmo valor."""
    chat = (chat or "").strip()
    try:
        if "@" not in chat and not chat.isdigit():
            chat = saved_contacts.resolve(chat)  # nome de contato -> telefone/JID
        return (identity.phone_of(chat) if "@" in chat else chat) or chat
    except Exception:
        return chat


def log_sent(chat: str, text: str, how: str, now=None):
    with jsonstate.locked(_SENT_LOG):
        st = jsonstate.read(_SENT_LOG, {"sent": []})
        keep = (st["sent"] + [{"t": now or time.time(), "chat": _chat_key(chat), "text": text, "how": how}])[-300:]
    jsonstate.update(_SENT_LOG, {"sent": []}, sent=keep)


def sent_for(chat: str, limit: int = 8):
    key = _chat_key(chat)
    st = jsonstate.read(_SENT_LOG, {"sent": []})
    return [m for m in st["sent"] if m["chat"] == key][-limit:]


# ---- resposta automática com trava em CÓDIGO (não depende do modelo obedecer ao texto da skill) ----
AUTO_MAX_PER_HOUR = 8  # respostas automáticas por conversa por hora
AUTO_MAX_CHARS = 400
AUTO_FILE_MAX_BYTES = 5 * 1024 * 1024
AUTO_FILE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".txt", ".ogg"}
AUTO_FILE_DIR = os.path.expanduser("~/OpenBot/Shared/Envio")  # única pasta de onde o modo auto envia arquivo
_URL = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|net|org|br|io|me|ly)\b)", re.IGNORECASE)
_LONG_DIGITS = re.compile(r"\d{9,}")
_AUTO_STATE = os.environ.get("WHATSAPP_AUTO_REPLIES") or os.path.join(config.STORE_DIR, "auto_replies.json")


def check_auto_reply(chat: str, text: str, now=None, sensitive=None):
    """(ok, motivos). Só deixa passar se TUDO valer: modo ligado, chat 1:1, contato da agenda
    com reply_mode=auto, texto curto, sem assunto sensível, sem link nem número longo e dentro do limite por hora."""
    import live  # SENSITIVE (pix, senha, cpf, valores...) é a mesma lista da sessão ao vivo

    now = now or time.time()
    text = (text or "").strip()
    why = []
    if not conversation.load().get("enabled"):
        why.append("modo conversacional desligado")
    if not chat or "@g.us" in chat or "@newsletter" in chat or chat.startswith("status@"):
        why.append("só conversa 1:1 (grupo, canal e status nunca)")
    phone = identity.phone_of(chat) if "@" in chat else chat
    contact = None
    for ident in (phone, chat):
        try:
            contact = saved_contacts.find_contact(ident) if ident else None
        except saved_contacts.RecipientError:
            contact = None
        if contact:
            break
    mode = ((contact or {}).get("profile") or {}).get("reply_mode", "off") if contact else None
    if contact is None:
        why.append("contato fora da agenda")
    elif mode != "auto":
        why.append(f"reply_mode do contato é '{mode}', não 'auto'")
    if not text:
        why.append("texto vazio")
    if len(text) > AUTO_MAX_CHARS:
        why.append(f"texto com mais de {AUTO_MAX_CHARS} caracteres")
    hit = (sensitive or live.SENSITIVE).search(text)
    if hit:
        why.append(f"assunto sensível ('{hit.group(0)}')")
    if _URL.search(text):
        why.append("contém link")
    if _LONG_DIGITS.search(text):
        why.append("contém número longo (telefone, documento, conta)")
    st = jsonstate.read(_AUTO_STATE, {"sent": []})
    if sum(1 for m in st["sent"] if m.get("chat") == chat and m["t"] >= now - 3600) >= AUTO_MAX_PER_HOUR:
        why.append(f"limite de {AUTO_MAX_PER_HOUR} respostas automáticas por hora nesta conversa")
    return (not why, why)


def auto_reply(chat: str, text: str, sender=None, now=None):
    """Envia só se check_auto_reply passar; registra para o limite por hora."""
    sender = sender or send_message
    ok, why = check_auto_reply(chat, text, now)
    if not ok:
        return {"success": False, "blocked": why, "hint": "não enviado: relate ao dono como rascunho"}
    sent, msg = sender(chat, text.strip())
    if sent:
        log_sent(chat, text.strip(), "auto", now)
        with jsonstate.locked(_AUTO_STATE):
            st = jsonstate.read(_AUTO_STATE, {"sent": []})
            keep = [m for m in st["sent"] if m["t"] >= (now or time.time()) - 3600]
            keep.append({"t": now or time.time(), "chat": chat})
        jsonstate.update(_AUTO_STATE, {"sent": []}, sent=keep)
    return {"success": bool(sent), "message": msg}


def check_auto_file(chat: str, path: str, caption: str = "", now=None):
    """(ok, motivos) para enviar ARQUIVO sozinho: tudo de check_auto_reply, mais allow_files=on no contato,
    arquivo dentro de ~/OpenBot/Shared/Envio, extensão da lista segura e até 5 MB."""
    ok, why = check_auto_reply(chat, caption or "arquivo", now)
    why = list(why)
    phone = identity.phone_of(chat) if "@" in chat else chat
    try:
        contact = saved_contacts.find_contact(phone) or saved_contacts.find_contact(chat)
    except saved_contacts.RecipientError:
        contact = None
    if not contact or (contact.get("profile") or {}).get("allow_files") != "on":
        why.append("contato sem permissão para arquivos (wpp contact <nome> --allow-files on --yes)")
    real = os.path.realpath(os.path.expanduser(path or ""))
    base = os.path.realpath(AUTO_FILE_DIR)
    if not (real == base or real.startswith(base + os.sep)):
        why.append(f"arquivo fora de {AUTO_FILE_DIR}")
    elif not os.path.isfile(real):
        why.append("arquivo não existe")
    else:
        if os.path.splitext(real)[1].lower() not in AUTO_FILE_EXTS:
            why.append("tipo de arquivo não permitido (só imagem, pdf, txt, ogg)")
        if os.path.getsize(real) > AUTO_FILE_MAX_BYTES:
            why.append("arquivo maior que 5 MB")
    return (not why, [w for w in why if w != "texto vazio"])


def auto_reply_file(chat: str, path: str, caption: str = "", sender=None, now=None):
    sender = sender or send_file
    ok, why = check_auto_file(chat, path, caption, now)
    if not ok:
        return {"success": False, "blocked": why, "hint": "não enviado: relate ao dono como rascunho"}
    sent, msg = sender(chat, os.path.realpath(os.path.expanduser(path)))
    if sent:
        log_sent(chat, f"[arquivo] {os.path.basename(path)}", "auto", now)
        with jsonstate.locked(_AUTO_STATE):
            st = jsonstate.read(_AUTO_STATE, {"sent": []})
            keep = [m for m in st["sent"] if m["t"] >= (now or time.time()) - 3600]
            keep.append({"t": now or time.time(), "chat": chat})
        jsonstate.update(_AUTO_STATE, {"sent": []}, sent=keep)
    return {"success": bool(sent), "message": msg}


def _resolve(r):
    try:
        return saved_contacts.resolve(r)
    except saved_contacts.RecipientError as e:
        print(json.dumps({"success": False, "message": str(e), "candidates": e.candidates}, ensure_ascii=False))
        sys.exit(2)


def timeline(chat: str, limit: int = 12, db_path=None):
    """Linha do tempo única da conversa: mensagens do histórico (recebidas e digitadas no celular) +
    o que foi enviado pela API (wpp send/reply), ordenadas por horário com segundos."""
    import sqlite3
    from datetime import datetime

    items = []
    path = db_path or config.MESSAGES_DB_PATH
    jids = (identity.equivalent_jids(chat) | {chat}) if "@" in chat else {chat}
    if "@" not in chat:
        try:
            jids |= identity.equivalent_jids(chat)
        except Exception:
            pass
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        marks = ",".join("?" for _ in jids)
        rows = con.execute(
            f"SELECT timestamp, is_from_me, content, media_type FROM messages "
            f"WHERE chat_jid IN ({marks}) ORDER BY timestamp DESC LIMIT ?",
            (*jids, limit),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        rows = []
    for ts, from_me, content, media in rows:
        try:
            t = datetime.fromisoformat(str(ts)).timestamp()
        except ValueError:
            continue
        who = "EU (celular)" if from_me else "Contato"
        items.append((t, who, content or f"[{media or 'mídia'}]"))
    for m in sent_for(chat, limit):
        # o bridge também guarda o que enviou: a linha do registro (com "manual"/"auto") vale mais
        items = [i for i in items if not (i[1] == "EU (celular)" and i[2] == m["text"] and abs(i[0] - m["t"]) < 120)]
        items.append((m["t"], f"EU ({m['how']})", m["text"]))
    items.sort(key=lambda x: x[0])
    return items[-limit:]


def profile_of(chat: str):
    """Perfil do contato resolvido por telefone, nome ou JID (inclusive @lid)."""
    key = _chat_key(chat)
    contact = None
    for ident in (key, chat):
        try:
            contact = saved_contacts.find_contact(ident) if ident else None
        except saved_contacts.RecipientError:
            contact = None
        if contact:
            break
    if not contact:
        return {"found": False, "chat": chat}
    prof = dict(contact.get("profile") or {})
    return {
        "found": True,
        "name": contact["name"],
        "phone": contact["phone"],
        "reply_mode": prof.get("reply_mode", "off"),
        "profile": prof,
    }


def _http_ok(url, timeout=3):
    """True se alguém responde nesse endereço (qualquer status HTTP conta; erro de conexão não)."""
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def _service_active(name):
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", name], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def doctor():
    """Lista de (ok, rótulo, detalhe, crítico) com tudo que o modo conversacional precisa para funcionar.
    Não ter contato ligado (draft/auto) é um estado legítimo: aparece como AVISO, não como falha.
    O detalhe é a informação quando está OK e a dica de como corrigir quando falha."""
    out = []

    def check(ok, label, info, hint, critical=True):
        out.append((bool(ok), label, info if ok else hint, critical))

    port = os.environ.get("WHATSAPP_BRIDGE_PORT", "9090")
    check(
        _http_ok(f"http://127.0.0.1:{port}/api/status"),
        "bridge do WhatsApp responde",
        f"porta {port}",
        "ligue o bridge (systemctl --user start whatsapp-bridge)",
    )
    check(
        _service_active("wpp-conversation.service"),
        "vigia (wpp-conversation) rodando",
        "ativo",
        "systemctl --user start wpp-conversation",
    )
    st = conversation.load()
    check(st.get("enabled"), "modo conversacional ligado", "ligado", "wpp auto on")
    check(
        st.get("agent_id") and st.get("routine_id"),
        "rotina do OpenBot configurada no vigia",
        "configurada",
        "conversation_mode(agent_id, routine_id)",
    )
    url = ""
    try:
        url = open(os.path.join(config.AUTOMATION_DIR, "url"), encoding="utf-8").read().strip()
    except OSError:
        pass
    check(
        url and _http_ok(url),
        "OpenBot aberto e alcançável",
        "respondendo",
        "abra o OpenBot (sem ele nada é respondido)",
    )
    people = conversation.eligible_contacts()
    desc = ", ".join(f"{p['name']}={p['reply_mode']}" for p in people)
    check(people, "contato em draft/auto", desc, 'nenhum ainda: wpp contact "<nome>" --mode draft', critical=False)
    sent_dir = os.path.dirname(os.path.abspath(_SENT_LOG))
    check(
        os.access(sent_dir, os.W_OK),
        "registro de enviados gravável",
        sent_dir,
        f"sem permissão de escrita em {sent_dir}",
    )
    return out


def set_auto(on):
    """Liga/desliga o modo. Ligar exige a rotina configurada; ligar não responde mensagens antigas."""
    st = conversation.load()
    if on and not (st.get("agent_id") and st.get("routine_id")):
        return {"success": False, "message": "rotina não configurada no vigia (conversation_mode agent_id/routine_id)"}
    conversation.set_enabled(on)
    return {"success": True, "enabled": conversation.load()["enabled"]}


def edit_contact(
    name, mode=None, context=None, tone=None, avoid=None, address_as=None, yes=False, language=None, signature=None,
    allow_files=None,
):
    """Mostra ou altera o perfil de UM contato. 'auto' só com yes=True (confirmação explícita do dono)."""
    try:
        contact = saved_contacts.find_contact(name)
    except saved_contacts.RecipientError as e:
        return {"success": False, "message": str(e), "candidates": getattr(e, "candidates", [])}
    if not contact:
        return {"success": False, "message": f"contato '{name}' não está na agenda"}
    fields = {
        "reply_mode": mode,
        "context": context,
        "tone": tone,
        "avoid": avoid,
        "address_as": address_as,
        "language": language,
        "signature": signature,
        "allow_files": allow_files,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields.get("allow_files") == "on" and not yes:
        return {"success": False, "message": "liberar arquivos no modo auto: repita com --yes para confirmar"}
    if fields.get("reply_mode") == "auto" and not yes:
        return {"success": False, "message": "modo auto envia mensagens de verdade: repita com --yes para confirmar"}
    if fields:
        saved_contacts.set_profile(contact["name"], **fields)
    prof = saved_contacts.get_profile(contact["name"]) or {}
    return {"success": True, "name": contact["name"], "phone": contact["phone"], "profile": prof}


def _phone_key(phone):
    """Chave do número para achar duplicatas: sem 55, sem 0 de tronco e sem o 9º dígito de celular BR."""
    d = re.sub(r"\D", "", phone or "")
    if d.startswith("55") and len(d) >= 12:
        d = d[2:]
    elif d.startswith("0") and len(d) == 12:
        d = d[1:]
    if len(d) == 11 and d[2] == "9":
        d = d[:2] + d[3:]
    return d


_MODOS = {"off": 0, "draft": 1, "auto": 2}


def dedupe_plan(agenda):
    """Junta contatos com o MESMO número (com/sem 9º dígito, com/sem 55, 0 de tronco). Nome igual com número
    diferente NÃO junta (pode ser outra pessoa). Devolve (nova_agenda, grupos_juntados, avisos_nome_igual)."""
    grupos = {}
    for c in agenda:
        grupos.setdefault(_phone_key(c["phone"]), []).append(c)
    nova, juntados = [], []
    for chave, itens in grupos.items():
        if len(itens) == 1 or not chave:
            nova.extend(itens)
            continue
        # número canônico: formato internacional BR celular (55 + DDD + 9 + 8 dígitos); senão o mais longo
        canon = max(
            itens,
            key=lambda x: (
                re.sub(r"\D", "", x["phone"]).startswith("55") and len(re.sub(r"\D", "", x["phone"])) == 13,
                len(x["phone"]),
            ),
        )
        com_perfil = [x for x in itens if x.get("profile")]
        primario = com_perfil[0] if com_perfil else canon
        outros = [x for x in itens if x is not primario]
        perfil = dict(primario.get("profile") or {})
        modos = []
        for x in itens:
            pf = x.get("profile") or {}
            for k, v in pf.items():
                if k != "reply_mode" and v and not perfil.get(k):
                    perfil[k] = v
            if pf.get("reply_mode"):
                modos.append(pf["reply_mode"])
        if modos:  # em conflito, vale o modo mais restritivo (off < draft < auto)
            perfil["reply_mode"] = min(modos, key=lambda m: _MODOS.get(m, 0))
        apelidos = [x["name"] for x in itens if x["name"] != primario["name"]]
        notas = [n for n in [primario.get("notes", "")] + [x.get("notes", "") for x in outros] if n]
        if apelidos:
            notas.append("também salvo como: " + "; ".join(dict.fromkeys(apelidos)))
        unido = {"name": primario["name"], "phone": canon["phone"], "notes": " | ".join(dict.fromkeys(notas))}
        if perfil:
            unido["profile"] = perfil
        nova.append(unido)
        juntados.append(
            {"mantido": unido["name"], "phone": unido["phone"], "juntou": [(x["name"], x["phone"]) for x in itens]}
        )
    por_nome = {}
    for c in nova:
        por_nome.setdefault(re.sub(r"[^a-z0-9]", "", c["name"].lower()), []).append(c)
    avisos = [[(x["name"], x["phone"]) for x in v] for v in por_nome.values() if len(v) > 1]
    return nova, juntados, avisos


def dedupe(apply=False):
    """Simula (padrão) ou aplica a junção de contatos repetidos. Aplicar guarda uma cópia ao lado do arquivo."""
    import shutil

    with saved_contacts._locked():
        atual = saved_contacts._load()
        nova, juntados, avisos = dedupe_plan(atual)
        if apply and juntados:
            shutil.copy2(config.CONTACTS_PATH, config.CONTACTS_PATH + ".antes-do-dedupe")
            saved_contacts._save(nova)
    return {
        "success": True,
        "applied": bool(apply and juntados),
        "antes": len(atual),
        "depois": len(nova),
        "juntados": juntados,
        "mesmo_nome_numero_diferente": avisos,
    }


VERSION = "1.0.0"


def _plain(obj):
    """Dataclass/datetime -> estruturas simples (JSON)."""
    import dataclasses

    if dataclasses.is_dataclass(obj):
        obj = dataclasses.asdict(obj)
    return json.loads(json.dumps(obj, default=lambda x: x.isoformat() if hasattr(x, "isoformat") else str(x)))


def find(query, limit=10):
    """Busca contato por nome ou telefone: agenda primeiro; só se nada casar, procura nos chats do WhatsApp."""
    saved = [
        {"name": c["name"], "phone": c["phone"], "jid": f"{c['phone']}@s.whatsapp.net", "source": "agenda"}
        for c in saved_contacts.list_contacts(query)
    ]
    if saved:
        return saved[:limit]
    groups = [
        {"name": g["name"], "phone": None, "jid": g["phone"], "source": "grupo"}
        for g in saved_contacts.group_chats(query)
    ]
    if groups:
        return groups[:limit]
    found = [_plain(c) for c in search_contacts(query)[:limit]]
    return [
        {"name": c.get("name"), "phone": c.get("phone_number"), "jid": c.get("jid"), "source": "whatsapp"}
        for c in found
    ]


def configure_group(grupo, mode=None, context=None, keywords=None):
    """Liga/desliga o acompanhamento de um grupo (só off/draft) e guarda assunto e palavras-chave."""
    if grupo.endswith("@g.us"):
        jid, name = grupo, (chat_info(grupo) or {}).get("name") or grupo
    else:
        found = saved_contacts.group_chats(grupo)
        exact = [g for g in found if saved_contacts.fold(g["name"]) == saved_contacts.fold(grupo)]
        pick = exact if len(exact) == 1 else found
        if len(pick) != 1:
            return {"success": False, "message": "grupo não encontrado ou ambíguo: use o JID (wpp chats \"<nome>\")",
                    "candidates": [{"name": g["name"], "jid": g["phone"]} for g in found[:8]]}
        jid, name = pick[0]["phone"], pick[0]["name"]
    try:
        g = groups.set_group(jid, name, mode, context, keywords)
    except ValueError as e:
        return {"success": False, "message": str(e)}
    return {"success": True, "group": g, "gatilho": groups.mention_patterns(g)}


def recent_ids(chat, limit=8, db_path=None):
    """[(id, instante, quem, texto)] das últimas mensagens da conversa, para citar/reagir/editar/apagar."""
    import sqlite3

    path = db_path or config.MESSAGES_DB_PATH
    jids = identity.equivalent_jids(chat) | {chat} if "@" not in chat else {chat}
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        q = ",".join("?" * len(jids))
        rows = conn.execute(
            f"SELECT id, timestamp, is_from_me, sender, content, media_type FROM messages "
            f"WHERE chat_jid IN ({q}) ORDER BY timestamp DESC LIMIT ?", (*jids, limit)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if conn is not None:
            conn.close()
    out = []
    for mid, ts, mine, sender, content, media in reversed(rows):
        who = "eu" if mine else (identity.lookup_name(sender) or sender)
        text = (content or "").replace("\n", " ")[:70] or (f"[{media}]" if media else "")
        out.append((mid, str(ts)[:16], who, text))
    return out


def chat_info(chat):
    """Dados de uma conversa por JID (@g.us, @lid, @s.whatsapp.net) ou telefone."""
    if not chat.isdigit() and "@" not in chat:  # nome de contato ou de grupo
        try:
            chat = saved_contacts.resolve(chat)
        except saved_contacts.RecipientError:
            return None
    info = get_direct_chat_by_contact(chat) if chat.isdigit() else get_chat(chat)
    return _plain(info) if info else None


def message_context(message_id, before=5, after=5):
    """Mensagem mais as vizinhas, em ordem, marcando a pedida."""
    ctx = _plain(get_message_context(message_id, before, after))
    rows = [(m, False) for m in ctx["before"]] + [(ctx["message"], True)] + [(m, False) for m in ctx["after"]]
    return [
        f"{'>>' if hit else '  '} {m['timestamp']} {'EU' if m['is_from_me'] else m['sender']}: {m['content']}"
        for m, hit in rows
    ]


def send_path(dest, path, audio=False, file_sender=None, audio_sender=None):
    """Envia arquivo ou áudio. O caminho passa pela mesma trava do MCP (sem chaves SSH nem bancos do bridge)."""
    try:
        path = safety.check_send_path(path)
    except safety.PathNotAllowed as e:
        return {"success": False, "message": str(e)}
    sender = (audio_sender or send_audio_message) if audio else (file_sender or send_file)
    ok, msg = sender(dest, path)
    if ok:
        log_sent(dest, f"[{'áudio' if audio else 'arquivo'}] {os.path.basename(path)}", "manual")
    return {"success": bool(ok), "message": msg}


def save_contact(name, phone, notes="", also=None):
    return {"success": True, "contact": saved_contacts.save_contact(name, phone, notes, also)}


def forget_contact(name, yes=False):
    if not yes:
        return {"success": False, "message": "apagar contato da agenda: repita com --yes para confirmar"}
    n = saved_contacts.remove_contact(name)
    return {"success": n > 0, "removed": n} if n else {"success": False, "message": f"'{name}' não está na agenda"}


def configure_auto(debounce=None, max_per_hour=None, agent_id=None, routine_id=None):
    fields = {
        "debounce_seconds": debounce,
        "max_triggers_per_hour": max_per_hour,
        "agent_id": agent_id,
        "routine_id": routine_id,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields:
        conversation.update(**fields)
    st = conversation.load()
    return {
        "success": True,
        "enabled": st["enabled"],
        "debounce_seconds": st["debounce_seconds"],
        "max_triggers_per_hour": st["max_triggers_per_hour"],
        "routine_configured": bool(st.get("agent_id") and st.get("routine_id")),
    }


EPILOG = """exemplos:
  wpp thread "Mãe" -n 10            conversa com os dois lados, em ordem
  wpp find joao                     achar contato por nome ou telefone
  wpp send "Ana" "oi, tudo bem?"    enviar (só com OK do dono)
  wpp reply <chat> "texto" --check  simular a trava do envio automático
  wpp contact "Ana" --mode draft --context "combinar o churrasco"
  wpp auto on | off | status        chave geral do modo conversacional
  wpp doctor                        o que está de pé e o que falta

saída: leitura em texto curto; ação em uma linha JSON (success/message/blocked).
código de saída: 0 ok, 1 recusado ou falhou, 2 uso errado ou destinatário ambíguo."""


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, default=str))


def build_parser():
    p = argparse.ArgumentParser(
        prog="wpp",
        description="WhatsApp pelo bridge local. Só os comandos send, send-file, audio e reply enviam mensagens.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"wpp {VERSION}")
    s = p.add_subparsers(dest="cmd", required=True, metavar="comando")

    def cmd(name, help, *aliases):
        return s.add_parser(name, help=help, aliases=list(aliases))

    cmd("status", "o bridge está conectado e logado?")
    c = cmd("chats", "lista conversas (1 linha cada)")
    c.add_argument("query", nargs="?", help="filtra por nome ou JID")
    c.add_argument("-n", type=int, default=15)
    m = cmd("msgs", "mensagens de uma conversa ou busca por texto")
    m.add_argument("chat", nargs="?", help="nome salvo, telefone ou JID")
    m.add_argument("-q", "--query")
    m.add_argument("-n", type=int, default=20)
    m.add_argument("--after")
    m.add_argument("--before")
    w = cmd("new", "só o que chegou desde o cursor")
    w.add_argument("--cursor", type=int)
    w.add_argument("--chat")
    w.add_argument("--mine", action="store_true", help="inclui as mensagens do próprio dono")
    w.add_argument("-n", type=int, default=50)
    th = cmd("thread", "conversa com OS DOIS LADOS (recebidas + enviadas pela API), em ordem, com segundos")
    th.add_argument("chat")
    th.add_argument("-n", type=int, default=10)
    gp = cmd("group", "configura um GRUPO no vigia: off ou draft (rascunho; grupo nunca é respondido sozinho)")
    gp.add_argument("grupo", help="nome do grupo ou JID @g.us")
    gp.add_argument("--mode", choices=["off", "draft"])
    gp.add_argument("--context")
    gp.add_argument("--keyword", action="append", help="palavra que também aciona (além da menção ao dono); repita para várias")
    cmd("groups", "lista os grupos configurados no vigia")
    ids = cmd("ids", "ids das últimas mensagens de uma conversa (para citar, reagir, editar, apagar)")
    ids.add_argument("chat")
    ids.add_argument("-n", type=int, default=8)
    rc = cmd("react", "reage a uma mensagem com um emoji (--remove tira a reação)")
    rc.add_argument("chat")
    rc.add_argument("message_id")
    rc.add_argument("emoji", nargs="?", default="")
    rc.add_argument("--remove", action="store_true")
    ed = cmd("edit", "edita uma mensagem SUA (o WhatsApp só deixa por ~15 minutos)")
    ed.add_argument("chat")
    ed.add_argument("message_id")
    ed.add_argument("text")
    rv = cmd("revoke", "apaga para todos uma mensagem SUA")
    rv.add_argument("chat")
    rv.add_argument("message_id")
    rv.add_argument("--yes", action="store_true", help="confirma (apagar não tem volta)")
    rd = cmd("read", "marca como lidas as últimas mensagens recebidas (a outra pessoa vê o 'lido')")
    rd.add_argument("chat")
    rd.add_argument("message_ids", nargs="*")
    f = cmd("find", "acha contato por nome ou telefone")
    f.add_argument("query")
    f.add_argument("-n", type=int, default=10)
    ch = cmd("chat", "dados de uma conversa (JID ou telefone)")
    ch.add_argument("chat")
    la = cmd("last", "última interação com um contato")
    la.add_argument("chat")
    cx = cmd("context", "uma mensagem com as vizinhas, pelo id")
    cx.add_argument("message_id")
    cx.add_argument("--before", type=int, default=5)
    cx.add_argument("--after", type=int, default=5)
    lg = cmd("log", "log em texto puro do bridge (recupera mensagem apagada)")
    lg.add_argument("-q", "--query")
    lg.add_argument("-n", type=int, default=100)
    md = cmd("media", "baixa a mídia de uma mensagem")
    md.add_argument("chat")
    md.add_argument("message_id")
    pf = cmd("profile", "perfil do contato (modo, tom, contexto) por nome, telefone ou JID, inclusive @lid")
    pf.add_argument("chat")

    e = cmd("send", "ENVIA texto (só com OK do dono)")
    e.add_argument("to")
    e.add_argument("text")
    e.add_argument("--reply-to", metavar="ID", help="cita a mensagem com esse id (veja `wpp ids`)")
    sf = cmd("send-file", "ENVIA arquivo (só com OK do dono)")
    sf.add_argument("to")
    sf.add_argument("path")
    au = cmd("audio", "ENVIA áudio como mensagem de voz (só com OK do dono)")
    au.add_argument("to")
    au.add_argument("path")
    r = cmd("reply", "resposta AUTOMÁTICA, com travas em código (modo ligado, 1:1, contato em auto)")
    r.add_argument("chat_jid")
    r.add_argument("text")
    r.add_argument("--file", metavar="CAMINHO", help="envia este arquivo (o texto vira legenda); exige allow_files=on no contato")
    r.add_argument("--check", action="store_true", help="só mostra se passaria nas travas; não envia")

    ag = cmd("agenda", "lista os contatos salvos", "contacts")
    ag.add_argument("query", nargs="?")
    ag.add_argument("--full", action="store_true", help="inclui o perfil (contexto, tom, modo)")
    sv = cmd("save", "salva um contato na agenda")
    sv.add_argument("name")
    sv.add_argument("phone")
    sv.add_argument("--notes", default="")
    sv.add_argument("--also", action="append", default=[], metavar="TEL", help="outro número da mesma pessoa (repita para vários)")
    fg = cmd("forget", "remove um contato da agenda")
    fg.add_argument("name")
    fg.add_argument("--yes", action="store_true", help="confirma a remoção")
    dd = cmd("dedupe", "junta contatos repetidos (mesmo número); simula, e só grava com --apply")
    dd.add_argument("--apply", action="store_true")
    iv = cmd("import-vcf", "importa contatos de um arquivo vCard")
    iv.add_argument("path")
    ct = cmd("contact", "mostra ou altera o perfil de UM contato (modo, contexto, tom)")
    ct.add_argument("name")
    ct.add_argument("--mode", choices=["off", "draft", "auto"])
    ct.add_argument("--context")
    ct.add_argument("--tone")
    ct.add_argument("--avoid")
    ct.add_argument("--address-as", dest="address_as")
    ct.add_argument("--language")
    ct.add_argument("--signature")
    ct.add_argument("--allow-files", choices=["on", "off"], help="deixa o modo auto enviar arquivo (só de ~/OpenBot/Shared/Envio, até 5 MB)")
    ct.add_argument("--yes", action="store_true", help="confirma o modo auto (envia de verdade)")

    am = cmd("auto", "chave geral do modo conversacional")
    am.add_argument("action", choices=["status", "on", "off", "config"])
    am.add_argument("--debounce", type=int, help="config: segundos de silêncio antes de acordar o agente")
    am.add_argument("--max-per-hour", type=int, dest="max_per_hour", help="config: acionamentos por conversa por hora")
    am.add_argument("--agent-id", dest="agent_id")
    am.add_argument("--routine-id", dest="routine_id")
    cmd("doctor", "verifica se TUDO do modo conversacional está de pé")
    return p


def _recipient(r):
    return _resolve(r)


def run(a):
    """Executa o comando já interpretado. Devolve o código de saída."""
    c = {"contacts": "agenda"}.get(a.cmd, a.cmd)
    if c == "status":
        import urllib.request

        port = os.environ.get("WHATSAPP_BRIDGE_PORT", "9090")
        print(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5).read().decode())
    elif c == "chats":
        print(format_chats_compact(list_chats(query=a.query, limit=a.n, include_last_message=True)))
    elif c == "msgs":
        chat = _recipient(a.chat) if a.chat else None
        print(list_messages(chat_jid=chat, query=a.query, limit=a.n, after=a.after, before=a.before, compact=True))
    elif c == "new":
        chat = _recipient(a.chat) if a.chat else None
        _out(read_new_messages(cursor=a.cursor, chat_jid=chat, include_from_me=a.mine, limit=a.n))
    elif c == "thread":
        lines = timeline(_recipient(a.chat), a.n)
        print("# Conversa (horário local, mais antigo primeiro; inclui o que foi enviado pela API):")
        for t, who, text in lines:
            print(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), f"{who}:", text)
        if not lines:
            print("(vazia)")
    elif c == "find":
        rows = find(a.query, a.n)
        for r in rows:
            print(f"{r['name']} | {r['phone']} | {r['jid']} | {r['source']}")
        if not rows:
            print("(nenhum contato)")
    elif c == "chat":
        info = chat_info(a.chat)
        _out(info if info else {"found": False, "chat": a.chat})
        return 0 if info else 1
    elif c == "last":
        print(get_last_interaction(a.chat))
    elif c == "context":
        print("\n".join(message_context(a.message_id, a.before, a.after)))
    elif c == "log":
        for line in read_message_log(query=a.query, limit=a.n):
            print(line)
    elif c == "media":
        import contextlib

        with contextlib.redirect_stdout(sys.stderr):  # whatsapp.py imprime o resultado; a saída do wpp é só o JSON
            path = download_media(a.message_id, _recipient(a.chat))
        _out({"success": bool(path), "file_path": path} if path else {"success": False, "message": "falha ao baixar"})
        return 0 if path else 1
    elif c == "profile":
        _out(profile_of(a.chat))
    elif c == "send":
        dest = _recipient(a.to)
        ok, msg = send_message(dest, a.text, a.reply_to)
        if ok:
            log_sent(dest, a.text, "manual")
        res = {"success": ok, "message": msg}
        if ok and whatsapp.last_sent_id:
            res["id"] = whatsapp.last_sent_id
        _out(res)
        return 0 if ok else 1
    elif c == "groups":
        _out(groups.list_groups())
    elif c == "group":
        res = configure_group(a.grupo, a.mode, a.context, a.keyword)
        _out(res)
        return 0 if res["success"] else 1
    elif c == "ids":
        rows = recent_ids(_recipient(a.chat), a.n)
        for mid, when, who, text in rows:
            print(f"{mid} | {when} | {who}: {text}")
        if not rows:
            print("(sem mensagens)")
    elif c in ("react", "edit", "revoke", "read"):
        chat = _recipient(a.chat)
        if c == "react":
            ok, msg, mid = react_message(chat, a.message_id, "" if a.remove else a.emoji)
        elif c == "edit":
            ok, msg, mid = edit_message(chat, a.message_id, a.text)
        elif c == "revoke":
            if not a.yes:
                _out({"success": False, "message": "apagar não tem volta: repita com --yes para confirmar"})
                return 1
            ok, msg, mid = revoke_message(chat, a.message_id)
        else:
            ok, msg, mid = mark_read(chat, a.message_ids)
        _out({"success": ok, "message": msg, **({"id": mid} if mid else {})})
        return 0 if ok else 1
    elif c in ("send-file", "audio"):
        res = send_path(_recipient(a.to), a.path, audio=(c == "audio"))
        _out(res)
        return 0 if res["success"] else 1
    elif c == "reply":
        if a.check:
            ok, why = check_auto_file(a.chat_jid, a.file, a.text) if a.file else check_auto_reply(a.chat_jid, a.text)
            res = {"success": ok, "would_send": ok, "blocked": why}
        elif a.file:
            res = auto_reply_file(a.chat_jid, a.file, a.text)
        else:
            res = auto_reply(a.chat_jid, a.text)
        _out(res)
        return 0 if res["success"] else 1
    elif c == "agenda":
        rows = []
        for x in saved_contacts.list_contacts(a.query):
            pf = x.get("profile") or {}
            row = {"name": x["name"], "phone": x["phone"], "reply_mode": pf.get("reply_mode", "off")}
            if x.get("alt_phones"):
                row["alt_phones"] = x["alt_phones"]
            if a.full:
                row["context"] = pf.get("context", "")
                row["tone"] = pf.get("tone", "")
            rows.append(row)
        _out(rows)
    elif c == "save":
        res = save_contact(a.name, a.phone, a.notes, a.also)
        _out(res)
    elif c == "forget":
        res = forget_contact(a.name, a.yes)
        _out(res)
        return 0 if res["success"] else 1
    elif c == "dedupe":
        _out(dedupe(a.apply))
    elif c == "import-vcf":
        _out(saved_contacts.import_vcf(a.path))
    elif c == "contact":
        res = edit_contact(a.name, a.mode, a.context, a.tone, a.avoid, a.address_as, a.yes, a.language, a.signature, a.allow_files)
        _out(res)
        return 0 if res["success"] else 1
    elif c == "auto":
        if a.action == "status":
            st = conversation.load()
            _out(
                {
                    "enabled": st["enabled"],
                    "debounce_seconds": st["debounce_seconds"],
                    "max_triggers_per_hour": st["max_triggers_per_hour"],
                    "eligible": conversation.eligible_contacts(),
                }
            )
        elif a.action == "config":
            _out(configure_auto(a.debounce, a.max_per_hour, a.agent_id, a.routine_id))
        else:
            res = set_auto(a.action == "on")
            _out(res)
            return 0 if res["success"] else 1
    elif c == "doctor":
        checks = doctor()
        for ok, label, detail, critical in checks:
            marca = "OK   " if ok else ("FALHA" if critical else "AVISO")
            print(marca, label, f"({detail})")
        falhas = [x for x in checks if not x[0] and x[3]]
        avisos = [x for x in checks if not x[0] and not x[3]]
        if falhas:
            print(f"\n{len(falhas)} problema(s). Corrija os marcados FALHA.")
        else:
            print("\nTUDO CERTO." + (f" ({len(avisos)} aviso(s): estado, não defeito.)" if avisos else ""))
        return 1 if falhas else 0
    return 0


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        code = run(a)
    except Exception as e:  # erro esperado (id inexistente, bridge fora do ar...) vira recusa limpa, sem traceback
        _out({"success": False, "message": f"{type(e).__name__}: {e}"})
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
