"""Sessão ao vivo: conversa com UM contato conduzida aqui no chat do OpenBot.

Diferente do modo conversacional (vigia + rotina, em segundo plano), na sessão ao vivo o
agente do chat acompanha a conversa em tempo real, mostra tudo ao dono e escolhe, a qualquer
momento, entre:
  - approve : cada resposta passa por um cartão de aprovação (Enviar / Editar / Descartar);
  - auto    : conversa sozinho, com travas;
  - paused  : só acompanha, não responde.
O dono pode trocar o modo e o contexto (assunto) a qualquer hora.

O bridge NÃO grava no banco as mensagens enviadas pela API, então esta sessão guarda o que
ela mesma envia (`sent`) para a visão ao vivo mostrar os dois lados.

Comandos (python live.py ...):
  start <contato> [--mode approve|auto|paused] [--context "assunto"] [--force]
  panel [--json]                 estado atual + últimas mensagens
  wait [--timeout 300] [--quiet 8]   espera mensagem nova (exit 0), timeout (3) ou sessão encerrada (4)
  send "<texto>" --how approved|auto envia (auto passa pelas travas) e registra
  mode approve|auto|paused       troca o modo
  context "<assunto>"            define o assunto (também grava no perfil do contato)
  state <nome> ["nota"]          registra o estado mostrado no painel
  resume                         zera a sequência automática (depois de uma pausa por segurança)
  stop                           encerra a sessão
"""
import json
import re
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import config
import contacts
import conversation
import identity
import jsonstate
import whatsapp

MODES = {"approve": "✋ aprovar cada mensagem", "auto": "🤖 conversar sozinho", "paused": "⏸ só acompanhar"}
STATES = {
    "waiting_reply": "⏳ aguardando resposta dele",
    "reading": "👀 lendo mensagens novas",
    "drafting": "✍️ redigindo resposta",
    "awaiting_approval": "🟡 aguardando sua aprovação",
    "sending": "📤 enviando",
    "needs_you": "🔴 precisa de você",
    "paused": "⏸ pausado",
    "stopped": "⏹ encerrado",
}
DEFAULTS: Dict[str, Any] = {
    "active": False, "contact": "", "phone": "", "chat_jid": "", "mode": "approve", "context": "",
    "state": "stopped", "state_note": "", "state_since": None, "cursor": None, "started_at": None,
    "sent": [], "auto_streak": 0, "max_auto_per_hour": 8, "max_auto_streak": 12, "max_auto_chars": 400,
}
# Assuntos que nunca saem sozinhos: exigem o cartão de aprovação mesmo em modo auto.
SENSITIVE = re.compile(
    r"\b(pix|senha|cpf|cnpj|rg|cart[aã]o|cvv|token|c[oó]digo de verifica|transfer|dep[oó]sit|boleto|"
    r"empr[eé]stimo|emprestar|r\$|\d+\s?(reais|real|mil))\b", re.IGNORECASE)


def _path() -> str:
    return config.LIVE_SESSION_PATH


def load() -> Dict[str, Any]:
    return jsonstate.read(_path(), DEFAULTS)


def update(**fields: Any) -> Dict[str, Any]:
    return jsonstate.update(_path(), DEFAULTS, **fields)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start(contact: str, mode: str = "approve", context: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"modo deve ser um de: {', '.join(MODES)}")
    found = contacts.find_contact(contact)
    if found is None:
        raise contacts.RecipientError(
            f"Contato '{contact}' não está na agenda. Salve-o antes (save_contact).")
    phone = found["phone"]
    background = conversation.load()["enabled"] and (found.get("profile") or {}).get("reply_mode") in ("draft", "auto")
    if background and not force:
        raise ValueError("O modo conversacional em segundo plano está ligado para esse contato: "
                         "responderia em dobro. Desligue-o (conversation.py off) ou use --force.")
    chat = whatsapp.get_direct_chat_by_contact(phone)
    jid = chat.jid if chat else f"{phone}@s.whatsapp.net"
    if context is not None:
        contacts.set_profile(found["name"], context=context)
        identity.invalidate()
    return update(active=True, contact=found["name"], phone=phone, chat_jid=jid, mode=mode,
                  context=context if context is not None else (found.get("profile") or {}).get("context", ""),
                  state="waiting_reply", state_note="", state_since=_now_iso(), cursor=conversation.current_top(),
                  started_at=_now_iso(), sent=[], auto_streak=0)


def _require_active(s: Dict[str, Any]) -> None:
    if not s["active"]:
        raise ValueError("Não há sessão ao vivo ativa. Use `start`.")


def set_mode(mode: str) -> Dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"modo deve ser um de: {', '.join(MODES)}")
    _require_active(load())
    return update(mode=mode, state="paused" if mode == "paused" else "waiting_reply",
                  state_since=_now_iso(), state_note="")


def set_context(text: str) -> Dict[str, Any]:
    s = load()
    _require_active(s)
    contacts.set_profile(s["contact"], context=text)
    identity.invalidate()
    return update(context=text.strip())


def set_state(name: str, note: str = "") -> Dict[str, Any]:
    if name not in STATES:
        raise ValueError(f"estado deve ser um de: {', '.join(STATES)}")
    _require_active(load())
    return update(state=name, state_note=note, state_since=_now_iso())


def resume() -> Dict[str, Any]:
    _require_active(load())
    return update(auto_streak=0, state="waiting_reply", state_since=_now_iso(), state_note="")


def stop() -> Dict[str, Any]:
    return update(active=False, state="stopped", state_since=_now_iso(), state_note="")


def sent_last_hour(s: Dict[str, Any], now: Optional[float] = None) -> int:
    limit = (now or time.time()) - 3600
    return sum(1 for m in s["sent"] if m["how"] == "auto" and m["t"] >= limit)


def guard(text: str, now: Optional[float] = None) -> Tuple[bool, List[str]]:
    """Pode enviar `text` sem cartão de aprovação? Devolve (ok, motivos para bloquear)."""
    s = load()
    reasons: List[str] = []
    if not s["active"]:
        reasons.append("sem sessão ativa")
    if s["mode"] != "auto":
        reasons.append(f"modo é '{s['mode']}': use o cartão de aprovação")
    if sent_last_hour(s, now) >= s["max_auto_per_hour"]:
        reasons.append(f"limite de {s['max_auto_per_hour']} respostas automáticas por hora")
    if s["auto_streak"] >= s["max_auto_streak"]:
        reasons.append(f"{s['auto_streak']} respostas seguidas sem você participar: confirme com o dono")
    if len(text) > s["max_auto_chars"]:
        reasons.append(f"mensagem longa demais para envio automático (> {s['max_auto_chars']} caracteres)")
    hit = SENSITIVE.search(text)
    if hit:
        reasons.append(f"assunto sensível ('{hit.group(0)}'): precisa de aprovação")
    return (not reasons, reasons)


def send(text: str, how: str, sender: Callable[[str, str], Tuple[bool, str]] = whatsapp.send_message,
         now: Optional[float] = None) -> Dict[str, Any]:
    """Envia ao contato da sessão e registra. `how`: "approved" (o dono aprovou no cartão)
    ou "auto" (passa pelas travas de `guard`)."""
    if how not in ("approved", "auto"):
        raise ValueError("how deve ser 'approved' ou 'auto'")
    text = text.strip()
    if not text:
        raise ValueError("texto vazio")
    s = load()
    _require_active(s)
    if how == "auto":
        ok, reasons = guard(text, now)
        if not ok:
            return {"success": False, "blocked": reasons}
    update(state="sending", state_since=_now_iso())
    ok, message = sender(s["phone"], text)
    if not ok:
        update(state="needs_you", state_note=f"falha no envio: {message}", state_since=_now_iso())
        return {"success": False, "blocked": [f"falha no envio: {message}"]}
    entry = {"t": now or time.time(), "text": text, "how": how}
    s = load()
    update(sent=(s["sent"] + [entry])[-200:], state="waiting_reply", state_note="", state_since=_now_iso(),
           auto_streak=s["auto_streak"] + 1 if how == "auto" else 0)
    return {"success": True, "message": message}


def _aware(value: Any) -> datetime:
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return dt if dt.tzinfo else dt.astimezone()


def recent_lines(s: Dict[str, Any], limit: int = 6) -> List[str]:
    """Últimas mensagens dos dois lados: banco (dele e as que você digita no celular) + o que a sessão enviou."""
    items: List[Tuple[datetime, str, str]] = []
    conn = whatsapp._connect()
    try:
        marks, vals = whatsapp._in_clause(identity.equivalent_jids(s["chat_jid"]) | {s["chat_jid"]})
        for ts, content, from_me, media in conn.execute(
                f"SELECT timestamp, content, is_from_me, media_type FROM messages WHERE chat_jid IN ({marks}) "
                f"ORDER BY timestamp DESC LIMIT ?", (*vals, limit)):
            text = content or (f"[{media}]" if media else "")
            items.append((_aware(ts), "Você (celular)" if from_me else s["contact"], text))
    finally:
        conn.close()
    for m in s["sent"]:
        label = {"auto": "Eu (automático)", "approved": "Eu (aprovado)"}[m["how"]]
        items.append((datetime.fromtimestamp(m["t"]).astimezone(), label, m["text"]))
    items.sort(key=lambda x: x[0])
    return [f"{ts:%H:%M} {who}: {text.replace(chr(10), ' ')[:140]}" for ts, who, text in items[-limit:]]


def panel(now: Optional[float] = None) -> Dict[str, Any]:
    s = load()
    return {
        "active": s["active"], "contact": s["contact"], "mode": s["mode"], "mode_label": MODES[s["mode"]],
        "state": s["state"], "state_label": STATES.get(s["state"], s["state"]), "state_note": s["state_note"],
        "context": s["context"] or "(nenhum: defina com `context`)",
        "auto_last_hour": f"{sent_last_hour(s, now)}/{s['max_auto_per_hour']}",
        "auto_streak": f"{s['auto_streak']}/{s['max_auto_streak']}",
        "recent": recent_lines(s) if s["active"] else [],
    }


def panel_text() -> str:
    p = panel()
    if not p["active"]:
        return "Sessão ao vivo: ENCERRADA (use `start <contato>`)."
    lines = [f"Sessão ao vivo com {p['contact']}",
             f"Estado: {p['state_label']}" + (f" ({p['state_note']})" if p["state_note"] else ""),
             f"Modo: {p['mode_label']}", f"Assunto: {p['context']}",
             f"Automáticas na última hora: {p['auto_last_hour']} | sequência sem você: {p['auto_streak']}",
             "Últimas mensagens:"]
    return "\n".join(lines + [f"  {ln}" for ln in p["recent"]] or ["  (nenhuma)"])


def wait(timeout: float = 300, quiet: float = 8, poll: float = 2) -> Tuple[int, str]:
    """Espera mensagem nova. Devolve (código, texto): 0 = há novidade, 3 = tempo esgotado,
    4 = sessão encerrada. Mensagem dele só é entregue depois de `quiet` s sem novas (ele
    costuma mandar em rajada); mensagem sua digitada no celular é entregue na hora."""
    end = time.time() + timeout
    held_since: Optional[float] = None
    collected: List[str] = []
    while time.time() < end:
        s = load()
        if not s["active"]:
            return 4, "Sessão encerrada."
        r = whatsapp.read_new_messages(cursor=s["cursor"], chat_jid=s["chat_jid"], include_from_me=True, limit=50)
        if r["count"]:
            # "Eu:" = você digitou no celular: conta como participação e zera a sequência automática.
            update(cursor=r["next_cursor"], auto_streak=0 if "Eu:" in r["messages"] else s["auto_streak"])
            collected.append(r["messages"])
            held_since = time.time()
            if not any("Contato:" in c for c in collected):
                return 0, "\n".join(collected)  # só você falou (pelo celular): entrega já
        elif held_since is not None and time.time() - held_since >= quiet:
            return 0, "\n".join(collected)
        time.sleep(poll)
    return (0, "\n".join(collected)) if collected else (3, "Sem novidades até o tempo esgotar.")


def cli(argv: List[str]) -> int:
    cmd, rest = (argv[0] if argv else ""), argv[1:]

    def opt(name: str, default: Optional[str] = None) -> Optional[str]:
        return rest[rest.index(name) + 1] if name in rest and rest.index(name) + 1 < len(rest) else default

    try:
        if cmd == "start" and rest:
            start(rest[0], opt("--mode", "approve"), opt("--context"), "--force" in rest)
            print(panel_text())
        elif cmd == "panel":
            print(json.dumps(panel(), ensure_ascii=False, indent=2) if "--json" in rest else panel_text())
        elif cmd == "wait":
            code, text = wait(float(opt("--timeout", "300")), float(opt("--quiet", "8")))
            print(text)
            return code
        elif cmd == "send" and rest:
            out = send(rest[0], opt("--how", "approved"))
            print(json.dumps(out, ensure_ascii=False))
            return 0 if out["success"] else 1
        elif cmd == "mode" and rest:
            set_mode(rest[0])
            print(panel_text())
        elif cmd == "context" and rest:
            set_context(" ".join(rest))
            print(panel_text())
        elif cmd == "state" and rest:
            set_state(rest[0], rest[1] if len(rest) > 1 else "")
            print(panel_text())
        elif cmd == "resume":
            resume()
            print(panel_text())
        elif cmd == "stop":
            stop()
            print(panel_text())
        else:
            print(__doc__)
            return 2
    except contacts.RecipientError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
