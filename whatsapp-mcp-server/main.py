from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

import contacts as saved_contacts
import conversation
import identity
import safety
from whatsapp import download_media as whatsapp_download_media
from whatsapp import format_chats_compact
from whatsapp import get_chat as whatsapp_get_chat
from whatsapp import get_contact_chats as whatsapp_get_contact_chats
from whatsapp import get_direct_chat_by_contact as whatsapp_get_direct_chat_by_contact
from whatsapp import get_last_interaction as whatsapp_get_last_interaction
from whatsapp import get_message_context as whatsapp_get_message_context
from whatsapp import list_chats as whatsapp_list_chats
from whatsapp import list_messages as whatsapp_list_messages
from whatsapp import read_message_log as whatsapp_read_message_log
from whatsapp import read_new_messages as whatsapp_read_new_messages
from whatsapp import search_contacts as whatsapp_search_contacts
from whatsapp import send_audio_message as whatsapp_audio_voice_message
from whatsapp import send_file as whatsapp_send_file
from whatsapp import send_message as whatsapp_send_message

# Initialize FastMCP server
mcp = FastMCP("whatsapp")


def _resolve_or_error(recipient: str) -> "str | Dict[str, Any]":
    """Telefone/JID do destinatário, ou um dict de erro pronto para devolver ao cliente
    quando o nome não existe ou é ambíguo (nada é enviado nesses casos)."""
    try:
        return saved_contacts.resolve(recipient)
    except saved_contacts.RecipientError as e:
        return {"success": False, "message": str(e), "candidates": e.candidates}

@mcp.tool()
def search_contacts(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search contacts by name or phone number.

    Looks in the local contact book first; only if nothing matches there
    does it fall back to searching WhatsApp chats.

    Args:
        query: Search term to match against contact names or phone numbers
        limit: Maximum number of results (default 10)
    """
    saved = [
        {"phone_number": c["phone"], "name": c["name"], "jid": f"{c['phone']}@s.whatsapp.net", "source": "agenda"}
        for c in saved_contacts.list_contacts(query)
    ]
    if saved:
        return saved[:limit]
    return whatsapp_search_contacts(query)[:limit]

@mcp.tool()
def list_saved_contacts(query: Optional[str] = None) -> List[Dict[str, str]]:
    """List the editable local contact book (takes priority over WhatsApp chat search).

    Args:
        query: Optional name or phone fragment to filter by
    """
    return saved_contacts.list_contacts(query)

@mcp.tool()
def save_contact(name: str, phone: str, notes: str = "") -> Dict[str, str]:
    """Add or update a contact in the local contact book (keyed by phone number).

    Args:
        name: Contact name
        phone: Phone number; Brazilian numbers without country code get 55 prepended
        notes: Optional free-text notes
    """
    saved = saved_contacts.save_contact(name, phone, notes)
    identity.invalidate()
    return saved

@mcp.tool()
def remove_saved_contact(identifier: str) -> Dict[str, int]:
    """Remove a contact from the local contact book by phone number or exact name."""
    removed = saved_contacts.remove_contact(identifier)
    identity.invalidate()
    return {"removed": removed}

@mcp.tool()
def import_contacts_vcf(path: str) -> Dict[str, int]:
    """Import contacts from a .vcf (vCard) file, e.g. exported from the phone's Contacts app.
    Existing entries are kept untouched.

    Args:
        path: Absolute path to the .vcf file
    """
    result = saved_contacts.import_vcf(path)
    identity.invalidate()
    return result

@mcp.tool()
def set_contact_profile(
    contact: str,
    tone: Optional[str] = None,
    address_as: Optional[str] = None,
    language: Optional[str] = None,
    signature: Optional[str] = None,
    avoid: Optional[str] = None,
    context: Optional[str] = None,
    reply_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Define COMO escrever para um contato salvo (o teor das mensagens). Só os campos
    informados mudam; string vazia apaga o campo.

    Args:
        contact: Nome exato ou telefone de um contato da agenda
        tone: Tom e estilo, ex.: "informal, curto, bem-humorado" ou "formal e objetivo"
        address_as: Como chamar a pessoa, ex.: "Dona Maria", "chefe", "mano"
        language: Idioma das mensagens, ex.: "pt-BR", "en"
        signature: Assinatura ou fecho opcional
        avoid: O que não fazer, ex.: "emoji, gírias, falar de preço"
        context: Assunto/objetivo da conversa em andamento, ex.: "combinar o churrasco de sábado"
        reply_mode: Resposta automática no modo conversacional: "off" (padrão), "draft" (só rascunho) ou "auto"
    """
    try:
        profile = saved_contacts.set_profile(
            contact, tone=tone, address_as=address_as, language=language,
            signature=signature, avoid=avoid, context=context, reply_mode=reply_mode)
    except (saved_contacts.RecipientError, ValueError) as e:
        return {"success": False, "message": str(e),
                "candidates": getattr(e, "candidates", [])}
    return {"success": True, "contact": contact, "profile": profile}


@mcp.tool()
def get_contact_profile(contact: str) -> Dict[str, Any]:
    """Mostra o perfil de comunicação (tom, tratamento, idioma, assinatura, o que evitar,
    modo de resposta) de um contato salvo.

    Args:
        contact: Nome exato ou telefone de um contato da agenda
    """
    try:
        profile = saved_contacts.get_profile(contact)
    except saved_contacts.RecipientError as e:
        return {"success": False, "message": str(e), "candidates": e.candidates}
    if profile is None:
        return {"success": False, "message": f"Contato '{contact}' não encontrado na agenda."}
    return {"success": True, "contact": contact, "profile": profile}


@mcp.tool()
def prepare_message(recipient: str, intent: str, recent: int = 8) -> Dict[str, Any]:
    """Passo obrigatório ANTES de escrever uma mensagem a uma pessoa: junta o perfil de
    tom do contato e as últimas mensagens da conversa para você redigir no estilo certo.
    Não envia nada. Depois de redigir, mostre o rascunho ao dono e só então use send_message.

    Args:
        recipient: Nome de contato salvo, telefone com DDI ou JID
        intent: O que a mensagem precisa comunicar (a ideia, não o texto final)
        recent: Quantas mensagens recentes da conversa incluir (0 para nenhuma)
    """
    target = _resolve_or_error(recipient)
    if isinstance(target, dict):
        return target
    phone = identity.phone_of(target) if "@" in target else target
    contact = None
    for ident in (phone, recipient):
        if ident:
            try:
                contact = saved_contacts.find_contact(ident)
            except saved_contacts.RecipientError:
                contact = None
            if contact:
                break
    profile = dict(contact.get("profile") or {}) if contact else {}
    history = ""
    if recent > 0:
        history = whatsapp_list_messages(chat_jid=target, limit=recent, include_context=False)
    return {
        "recipient": target,
        "contact": {"name": contact["name"], "phone": contact["phone"]} if contact else None,
        "intent": intent,
        "profile": profile,
        "has_profile": bool(profile),
        "recent_messages": history,
        "instructions": (
            "Escreva a mensagem seguindo o perfil (tom, tratamento, idioma, assinatura) e "
            "respeitando 'avoid'. Mostre o rascunho ao dono e só chame send_message depois "
            "da aprovação dele. Sem perfil, use um tom neutro e cordial e avise o dono."
        ),
    }


@mcp.tool()
def conversation_mode(
    enabled: Optional[bool] = None,
    debounce_seconds: Optional[int] = None,
    max_triggers_per_hour: Optional[int] = None,
    agent_id: Optional[str] = None,
    routine_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Liga, desliga ou consulta o modo conversacional (resposta contínua a mensagens novas).

    Sem argumentos só mostra o estado. Com o modo ligado, um vigia acorda uma rotina do
    OpenBot quando chegam mensagens de contatos da agenda cujo `reply_mode` (ver
    set_contact_profile) é "draft" (só rascunho para o dono) ou "auto" (envia). Nunca
    atua em grupos. Ligar não responde mensagens antigas.

    Args:
        enabled: True liga (exige agent_id e routine_id já configurados), False desliga
        debounce_seconds: Segundos de silêncio na conversa antes de acordar o agente (padrão 20)
        max_triggers_per_hour: Limite de acionamentos por conversa por hora (padrão 6)
        agent_id: Id do agente dono da rotina do OpenBot que será acordada
        routine_id: Id da rotina do OpenBot que será acordada
    """
    try:
        settings = {k: v for k, v in {
            "debounce_seconds": debounce_seconds, "max_triggers_per_hour": max_triggers_per_hour,
            "agent_id": agent_id, "routine_id": routine_id}.items() if v is not None}
        if settings:
            conversation.update(**settings)
        if enabled is not None:
            conversation.set_enabled(enabled)
    except ValueError as e:
        return {"success": False, "message": str(e)}
    state = conversation.load()
    people = conversation.eligible_contacts()
    return {
        "success": True,
        "enabled": state["enabled"],
        "enabled_at": state["enabled_at"],
        "debounce_seconds": state["debounce_seconds"],
        "max_triggers_per_hour": state["max_triggers_per_hour"],
        "routine_configured": bool(state["agent_id"] and state["routine_id"]),
        "eligible_contacts": people,
        "note": ("Nenhum contato tem reply_mode draft/auto: nada será respondido." if not people else
                 "O vigia (serviço wpp-conversation) precisa estar rodando para o modo funcionar."),
    }


@mcp.tool()
def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: Optional[bool] = None,
    context_before: int = 1,
    context_after: int = 1,
    compact: bool = True,
) -> str:
    """Get WhatsApp messages matching specified criteria (chronological, compact format).

    Compact output: one `# chat | jid` header per conversation, then `HH:MM Name: text`
    lines; media shows as `[type id=ID]` (use id + the header jid with download_media).
    To follow a conversation, prefer read_new_messages (only what is new).

    Args:
        after: Optional ISO-8601 formatted string to only return messages after this date
        before: Optional ISO-8601 formatted string to only return messages before this date
        sender_phone_number: Optional phone number to filter messages by sender
        chat_jid: Optional chat JID, phone number or LID to filter messages by chat
        query: Optional search term (all words, any order, ignores case and accents)
        limit: Maximum number of messages to return (default 20; page 0 = the most recent)
        page: Page number for pagination (default 0)
        include_context: Include neighbouring messages around each match. Default: only when query is given
        context_before: Number of messages to include before each match (default 1)
        context_after: Number of messages to include after each match (default 1)
        compact: Compact format (default). False returns the older verbose format
    """
    return whatsapp_list_messages(
        after=after,
        before=before,
        sender_phone_number=sender_phone_number,
        chat_jid=chat_jid,
        query=query,
        limit=limit,
        page=page,
        include_context=include_context,
        context_before=context_before,
        context_after=context_after,
        compact=compact,
    )

@mcp.tool()
def read_new_messages(
    cursor: Optional[int] = None,
    chat_jid: Optional[str] = None,
    include_from_me: bool = False,
    limit: int = 50,
) -> Dict[str, Any]:
    """Read only the messages that arrived since the last read, using a cursor.

    First call: pass no cursor to just get the current `next_cursor` (nothing is read).
    Later calls: pass the previous `next_cursor` to get what is new. cursor=0 reads from
    the very beginning. If `has_more` is true, call again with the new `next_cursor`.

    Args:
        cursor: The `next_cursor` from the previous call (omit to start from now)
        chat_jid: Optional chat JID, phone number or LID to watch a single conversation
        include_from_me: Also return messages sent by me (default false)
        limit: Maximum number of messages per call (default 50)
    """
    return whatsapp_read_new_messages(
        cursor=cursor, chat_jid=chat_jid, include_from_me=include_from_me, limit=limit)

@mcp.tool()
def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active",
    compact: bool = True,
) -> "str | List[Any]":
    """Get WhatsApp chats matching specified criteria.

    Compact output (default): one line per chat, `name | jid | last time | who: last message`.

    Args:
        query: Optional search term to filter chats by name or JID
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
        include_last_message: Whether to include the last message in each chat (default True)
        sort_by: Field to sort results by, either "last_active" or "name" (default "last_active")
        compact: One text line per chat (default). False returns full structured records
    """
    chats = whatsapp_list_chats(
        query=query,
        limit=limit,
        page=page,
        include_last_message=include_last_message,
        sort_by=sort_by
    )
    return format_chats_compact(chats) if compact else chats

@mcp.tool()
def get_chat(chat_jid: str, include_last_message: bool = True) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by JID.
    
    Args:
        chat_jid: The JID of the chat to retrieve
        include_last_message: Whether to include the last message (default True)
    """
    chat = whatsapp_get_chat(chat_jid, include_last_message)
    return chat

@mcp.tool()
def get_direct_chat_by_contact(sender_phone_number: str) -> Dict[str, Any]:
    """Get WhatsApp chat metadata by sender phone number.
    
    Args:
        sender_phone_number: The phone number to search for
    """
    chat = whatsapp_get_direct_chat_by_contact(sender_phone_number)
    return chat

@mcp.tool()
def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Dict[str, Any]]:
    """Get all WhatsApp chats involving the contact.
    
    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    chats = whatsapp_get_contact_chats(jid, limit, page)
    return chats

@mcp.tool()
def get_last_interaction(jid: str) -> str:
    """Get most recent WhatsApp message involving the contact.
    
    Args:
        jid: The JID of the contact to search for
    """
    message = whatsapp_get_last_interaction(jid)
    return message

@mcp.tool()
def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> Dict[str, Any]:
    """Get context around a specific WhatsApp message.
    
    Args:
        message_id: The ID of the message to get context for
        before: Number of messages to include before the target message (default 5)
        after: Number of messages to include after the target message (default 5)
    """
    context = whatsapp_get_message_context(message_id, before, after)
    return context

@mcp.tool()
def send_message(
    recipient: str,
    message: str
) -> Dict[str, Any]:
    """Send a WhatsApp message to a person or group. For group chats use the JID.

    Args:
        recipient: The recipient - a saved contact name, a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        message: The message text to send
    
    Returns:
        A dictionary containing success status and a status message
    """
    target = _resolve_or_error(recipient)
    if isinstance(target, dict):
        return target
    success, status_message = whatsapp_send_message(target, message)
    return {"success": success, "message": status_message}

@mcp.tool()
def send_file(recipient: str, media_path: str) -> Dict[str, Any]:
    """Send a file such as a picture, raw audio, video or document via WhatsApp to the specified recipient. For group messages use the JID.
    
    Args:
        recipient: The recipient - a saved contact name, a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the media file to send (image, video, document)
    
    Returns:
        A dictionary containing success status and a status message
    """
    target = _resolve_or_error(recipient)
    if isinstance(target, dict):
        return target
    try:
        media_path = safety.check_send_path(media_path)
    except safety.PathNotAllowed as e:
        return {"success": False, "message": str(e)}
    success, status_message = whatsapp_send_file(target, media_path)
    return {"success": success, "message": status_message}

@mcp.tool()
def send_audio_message(recipient: str, media_path: str) -> Dict[str, Any]:
    """Send any audio file as a WhatsApp audio message to the specified recipient. For group messages use the JID. If it errors due to ffmpeg not being installed, use send_file instead.
    
    Args:
        recipient: The recipient - a saved contact name, a phone number with country code but no + or other symbols,
                 or a JID (e.g., "123456789@s.whatsapp.net" or a group JID like "123456789@g.us")
        media_path: The absolute path to the audio file to send (will be converted to Opus .ogg if it's not a .ogg file)
    
    Returns:
        A dictionary containing success status and a status message
    """
    target = _resolve_or_error(recipient)
    if isinstance(target, dict):
        return target
    try:
        media_path = safety.check_send_path(media_path)
    except safety.PathNotAllowed as e:
        return {"success": False, "message": str(e)}
    success, status_message = whatsapp_audio_voice_message(target, media_path)
    return {"success": success, "message": status_message}

@mcp.tool()
def download_media(message_id: str, chat_jid: str) -> Dict[str, Any]:
    """Download media from a WhatsApp message and get the local file path.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        A dictionary containing success status, a status message, and the file path if successful
    """
    file_path = whatsapp_download_media(message_id, chat_jid)
    
    if file_path:
        return {
            "success": True,
            "message": "Media downloaded successfully",
            "file_path": file_path
        }
    else:
        return {
            "success": False,
            "message": "Failed to download media"
        }

@mcp.tool()
def read_message_log(query: Optional[str] = None, limit: int = 100) -> List[str]:
    """Read the bridge's plain-text message log, which records every message the instant
    it arrives (before it can be deleted/revoked). Use this to recover a message's original
    content after someone deletes it for everyone.

    Args:
        query: If provided, only lines containing this substring (case-insensitive) are returned
        limit: Maximum number of matching lines to return, most recent first
    """
    return whatsapp_read_message_log(query=query, limit=limit)

if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')