import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Tuple, Iterable
import os.path
import requests
import json
import audio
import identity
from textutil import fold

# MCP fala JSON-RPC via stdio: qualquer print() em stdout corromperia o
# protocolo, então todo log daqui vai para stderr.
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
logger = logging.getLogger("whatsapp-mcp")

MESSAGES_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'whatsapp-bridge', 'store', 'messages.db')
MESSAGE_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'whatsapp-bridge', 'store', 'mensagens_log.txt')
WHATSAPP_BRIDGE_PORT = os.environ.get("WHATSAPP_BRIDGE_PORT", "9090")
WHATSAPP_API_BASE_URL = f"http://localhost:{WHATSAPP_BRIDGE_PORT}/api"
REQUEST_TIMEOUT_S = 10

@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None

@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None

    @property
    def is_group(self) -> bool:
        """Determine if chat is a group based on JID pattern."""
        return self.jid.endswith("@g.us")

@dataclass
class Contact:
    phone_number: str
    name: Optional[str]
    jid: str

@dataclass
class MessageContext:
    message: Message
    before: List[Message]
    after: List[Message]


def _connect() -> sqlite3.Connection:
    """Conexão de leitura com `fold()` registrada (busca sem acento nem maiúscula)."""
    conn = sqlite3.connect(MESSAGES_DB_PATH, timeout=5)
    conn.create_function("fold", 1, fold, deterministic=True)
    return conn


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ts_param(value: str, name: str) -> str:
    """ISO-8601 -> 'YYYY-MM-DD HH:MM:SS' no fuso local, o formato gravado pelo bridge."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Invalid date format for '{name}': {value}. Please use ISO-8601 format.")
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _in_clause(values: Iterable[str]) -> Tuple[str, List[str]]:
    vals = sorted(set(values))
    return ",".join("?" * len(vals)), vals


def _parse_time(value) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _message_from_row(row) -> Message:
    """row = (timestamp, sender, chat_name, content, is_from_me, chat_jid, id, media_type)"""
    return Message(
        timestamp=datetime.fromisoformat(row[0]),
        sender=row[1],
        chat_name=row[2],
        content=row[3],
        is_from_me=row[4],
        chat_jid=row[5],
        id=row[6],
        media_type=row[7],
    )


def _chat_with_last_message(conn: sqlite3.Connection, jid: str, name: Optional[str],
                            last_time, include_last_message: bool = True) -> Chat:
    last = None
    if include_last_message:
        last = conn.execute(
            "SELECT content, sender, is_from_me, media_type FROM messages "
            "WHERE chat_jid = ? ORDER BY timestamp DESC LIMIT 1", (jid,)
        ).fetchone()
    content = None
    if last:
        content = last[0] or (f"[{last[3]}]" if last[3] else last[0])
    return Chat(
        jid=jid,
        name=identity.display_name(jid, name),
        last_message_time=_parse_time(last_time),
        last_message=content,
        last_sender=last[1] if last else None,
        last_is_from_me=last[2] if last else None,
    )


def get_sender_name(sender_jid: str) -> str:
    """Nome de quem enviou. Em grupos o remetente vem como LID ou telefone: resolve
    pela agenda, pelos chats e pelos contatos do WhatsApp."""
    try:
        return identity.lookup_name(sender_jid) or sender_jid
    except Exception as e:
        logger.error(f"Error resolving sender name: {e}")
        return sender_jid

def format_message(message: Message, show_chat_info: bool = True) -> str:
    """Print a single message with consistent formatting."""
    output = ""
    chat_name = identity.display_name(message.chat_jid, message.chat_name) if message.chat_jid else message.chat_name

    if show_chat_info and chat_name:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] Chat: {chat_name} "
    else:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] "
        
    content_prefix = ""
    if hasattr(message, 'media_type') and message.media_type:
        content_prefix = f"[{message.media_type} - Message ID: {message.id} - Chat JID: {message.chat_jid}] "
    
    try:
        sender_name = get_sender_name(message.sender) if not message.is_from_me else "Me"
        output += f"From: {sender_name}: {content_prefix}{message.content}\n"
    except Exception as e:
        logger.error(f"Error formatting message: {e}")
    return output

def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> str:
    output = ""
    if not messages:
        output += "No messages to display."
        return output
    
    for message in messages:
        output += format_message(message, show_chat_info)
    return output

def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: bool = True,
    context_before: int = 1,
    context_after: int = 1
) -> List[Message]:
    """Get messages matching the specified criteria with optional context.

    `chat_jid` e `sender_phone_number` aceitam telefone, JID ou LID e cobrem as duas
    formas do mesmo contato. `query` ignora acento/maiúscula, exige todas as palavras
    (em qualquer ordem) e procura também no nome dos arquivos de mídia.
    """
    conn = None
    try:
        conn = _connect()
        cursor = conn.cursor()

        query_parts = ["SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type FROM messages"]
        query_parts.append("JOIN chats ON messages.chat_jid = chats.jid")
        where_clauses = []
        params = []

        if after:
            where_clauses.append("messages.timestamp > ?")
            params.append(_ts_param(after, "after"))

        if before:
            where_clauses.append("messages.timestamp < ?")
            params.append(_ts_param(before, "before"))

        if sender_phone_number:
            marks, vals = _in_clause(identity.equivalent_users(sender_phone_number))
            where_clauses.append(f"messages.sender IN ({marks})")
            params.extend(vals)

        if chat_jid:
            marks, vals = _in_clause(identity.equivalent_jids(chat_jid) | {chat_jid})
            where_clauses.append(f"messages.chat_jid IN ({marks})")
            params.extend(vals)

        for word in fold(query).split() if query else []:
            pattern = f"%{_like_escape(word)}%"
            where_clauses.append("(fold(messages.content) LIKE ? ESCAPE '\\' OR fold(messages.filename) LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])

        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))

        offset = page * limit
        query_parts.append("ORDER BY messages.timestamp DESC")
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        cursor.execute(" ".join(query_parts), tuple(params))
        result = [_message_from_row(row) for row in cursor.fetchall()]

        if include_context and result:
            # O contexto de resultados vizinhos se sobrepõe: não repete a mesma mensagem.
            seen = set()
            messages_with_context = []
            for msg in result:
                context = get_message_context(msg.id, context_before, context_after)
                for m in [*context.before, context.message, *context.after]:
                    if (m.chat_jid, m.id) not in seen:
                        seen.add((m.chat_jid, m.id))
                        messages_with_context.append(m)

            return format_messages_list(messages_with_context, show_chat_info=True)

        return format_messages_list(result, show_chat_info=True)

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> MessageContext:
    """Get context around a specific message."""
    conn = None
    try:
        conn = _connect()
        cursor = conn.cursor()
        cols = ("messages.timestamp, messages.sender, chats.name, messages.content, "
                "messages.is_from_me, chats.jid, messages.id, messages.media_type")

        cursor.execute(f"""
            SELECT {cols}
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.id = ?
        """, (message_id,))
        msg_data = cursor.fetchone()

        if not msg_data:
            raise ValueError(f"Message with ID {message_id} not found")

        target_message = _message_from_row(msg_data)

        cursor.execute(f"""
            SELECT {cols}
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.chat_jid = ? AND messages.timestamp < ?
            ORDER BY messages.timestamp DESC
            LIMIT ?
        """, (msg_data[5], msg_data[0], before))
        before_messages = [_message_from_row(m) for m in cursor.fetchall()]

        cursor.execute(f"""
            SELECT {cols}
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.chat_jid = ? AND messages.timestamp > ?
            ORDER BY messages.timestamp ASC
            LIMIT ?
        """, (msg_data[5], msg_data[0], after))
        after_messages = [_message_from_row(m) for m in cursor.fetchall()]

        # `before` vem em ordem decrescente da consulta; devolve em ordem cronológica.
        return MessageContext(
            message=target_message,
            before=list(reversed(before_messages)),
            after=after_messages
        )

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        raise
    finally:
        if conn:
            conn.close()


def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Chat]:
    """Get chats matching the specified criteria.

    `query` casa com o nome (inclusive o resolvido pela agenda/contatos quando o chat só
    tem número gravado), sem acento nem maiúscula, ou com o telefone do contato.
    """
    conn = None
    try:
        conn = _connect()
        rows = conn.execute("SELECT jid, name, last_message_time FROM chats").fetchall()

        if query:
            words = fold(query).split()
            qd = re.sub(r"\D", "", query)
            matched = []
            for jid, name, ts in rows:
                hay = fold(identity.display_name(jid, name)) + " " + fold(name)
                if (words and all(w in hay for w in words)) or \
                        (len(qd) >= 4 and identity.phone_matches(identity.phone_of(jid), qd)):
                    matched.append((jid, name, ts))
            rows = matched

        if sort_by == "last_active":
            rows.sort(key=lambda r: r[2] or "", reverse=True)
        else:
            rows.sort(key=lambda r: fold(identity.display_name(r[0], r[1])))

        rows = rows[page * limit:(page + 1) * limit]
        return [_chat_with_last_message(conn, jid, name, ts, include_last_message)
                for jid, name, ts in rows]

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return []
    finally:
        if conn:
            conn.close()


def search_contacts(query: str) -> List[Contact]:
    """Search contacts by name or phone number.

    Cobre chats @lid (resolvendo o telefone pelo mapa LID->telefone), a agenda local e os
    contatos sincronizados do WhatsApp mesmo sem conversa. Sem acento nem maiúscula; vários
    termos têm de aparecer todos no nome. Telefone casa com ou sem o 9º dígito.
    """
    conn = None
    try:
        conn = _connect()
        words = fold(query).split()
        qd = re.sub(r"\D", "", query)
        rows = conn.execute(
            "SELECT jid, name, last_message_time FROM chats "
            "WHERE jid LIKE '%@lid' OR jid LIKE '%@s.whatsapp.net'"
        ).fetchall()

        hits = []
        covered = set()  # telefones que já têm chat
        for jid, name, ts in rows:
            phone = identity.phone_of(jid)
            covered |= identity.phone_keys(phone)
            display = identity.display_name(jid, name) or ""
            hay = fold(display) + " " + fold(name)
            if (words and all(w in hay for w in words)) or \
                    (len(qd) >= 4 and identity.phone_matches(phone, qd)):
                nm = None if identity.is_placeholder(display) else display
                hits.append((ts or "", Contact(phone_number=phone or identity.user_of(jid), name=nm, jid=jid)))

        # recentes primeiro; quem começa com o termo sobe (sort estável)
        hits.sort(key=lambda h: h[0], reverse=True)
        first = fold(query).strip()
        hits.sort(key=lambda h: not fold(h[1].name).startswith(first))
        result = [c for _, c in hits]

        # contatos do livro de endereços sem conversa
        for phone, nm in identity.all_wa_contacts().items():
            keys = identity.phone_keys(phone)
            if keys & covered:
                continue
            covered |= keys
            if (words and all(w in fold(nm) for w in words)) or \
                    (len(qd) >= 4 and identity.phone_matches(phone, qd)):
                result.append(Contact(phone_number=phone, name=nm, jid=f"{phone}@s.whatsapp.net"))

        return result[:50]

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Chat]:
    """Get all chats involving the contact.

    Args:
        jid: Telefone, JID ou LID do contato (cobre as duas formas do mesmo contato)
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    conn = None
    try:
        conn = _connect()
        u_marks, users = _in_clause(identity.equivalent_users(jid))
        j_marks, jids = _in_clause(identity.equivalent_jids(jid) | {jid})

        rows = conn.execute(f"""
            SELECT c.jid, c.name, c.last_message_time
            FROM chats c
            WHERE c.jid IN ({j_marks})
               OR c.jid IN (SELECT DISTINCT chat_jid FROM messages WHERE sender IN ({u_marks}))
            ORDER BY c.last_message_time DESC
            LIMIT ? OFFSET ?
        """, (*jids, *users, limit, page * limit)).fetchall()

        return [_chat_with_last_message(conn, j, n, t) for j, n, t in rows]

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_last_interaction(jid: str) -> str:
    """Get most recent message involving the contact."""
    conn = None
    try:
        conn = _connect()
        u_marks, users = _in_clause(identity.equivalent_users(jid))
        j_marks, jids = _in_clause(identity.equivalent_jids(jid) | {jid})

        msg_data = conn.execute(f"""
            SELECT m.timestamp, m.sender, c.name, m.content, m.is_from_me, c.jid, m.id, m.media_type
            FROM messages m
            JOIN chats c ON m.chat_jid = c.jid
            WHERE m.sender IN ({u_marks}) OR m.chat_jid IN ({j_marks})
            ORDER BY m.timestamp DESC
            LIMIT 1
        """, (*users, *jids)).fetchone()

        if not msg_data:
            return None

        return format_message(_message_from_row(msg_data))

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return None
    finally:
        if conn:
            conn.close()


def get_chat(chat_jid: str, include_last_message: bool = True) -> Optional[Chat]:
    """Get chat metadata by JID. Para contato individual, também tenta a outra forma
    (telefone <-> LID) quando o JID exato não existe."""
    conn = None
    try:
        conn = _connect()
        candidates = [chat_jid] + sorted(identity.equivalent_jids(chat_jid) - {chat_jid})
        for jid in candidates:
            row = conn.execute("SELECT jid, name, last_message_time FROM chats WHERE jid = ?", (jid,)).fetchone()
            if row:
                return _chat_with_last_message(conn, *row, include_last_message=include_last_message)
        return None

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return None
    finally:
        if conn:
            conn.close()


def get_direct_chat_by_contact(sender_phone_number: str) -> Optional[Chat]:
    """Get chat metadata by sender phone number (acha chats por telefone e por LID)."""
    conn = None
    try:
        conn = _connect()
        marks, jids = _in_clause(identity.equivalent_jids(sender_phone_number))
        row = conn.execute(f"""
            SELECT jid, name, last_message_time FROM chats
            WHERE jid IN ({marks}) ORDER BY last_message_time DESC LIMIT 1
        """, tuple(jids)).fetchone()
        if not row:
            row = conn.execute("""
                SELECT jid, name, last_message_time FROM chats
                WHERE jid LIKE ? AND jid NOT LIKE '%@g.us'
                ORDER BY last_message_time DESC LIMIT 1
            """, (f"%{sender_phone_number}%",)).fetchone()
        if not row:
            return None
        return _chat_with_last_message(conn, *row)

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def send_message(recipient: str, message: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "message": message,
        }
        
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_file(recipient: str, media_path: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        if not media_path:
            return False, "Media path must be provided"
        
        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "media_path": media_path
        }
        
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def send_audio_message(recipient: str, media_path: str) -> Tuple[bool, str]:
    try:
        # Validate input
        if not recipient:
            return False, "Recipient must be provided"
        
        if not media_path:
            return False, "Media path must be provided"
        
        if not os.path.isfile(media_path):
            return False, f"Media file not found: {media_path}"

        if not media_path.endswith(".ogg"):
            try:
                media_path = audio.convert_to_opus_ogg_temp(media_path)
            except Exception as e:
                return False, f"Error converting file to opus ogg. You likely need to install ffmpeg: {str(e)}"
        
        url = f"{WHATSAPP_API_BASE_URL}/send"
        payload = {
            "recipient": recipient,
            "media_path": media_path
        }
        
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)
        
        # Check if the request was successful
        if response.status_code == 200:
            result = response.json()
            return result.get("success", False), result.get("message", "Unknown response")
        else:
            return False, f"Error: HTTP {response.status_code} - {response.text}"
            
    except requests.RequestException as e:
        return False, f"Request error: {str(e)}"
    except json.JSONDecodeError:
        return False, f"Error parsing response: {response.text}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

def download_media(message_id: str, chat_jid: str) -> Optional[str]:
    """Download media from a message and return the local file path.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        The local file path if download was successful, None otherwise
    """
    try:
        url = f"{WHATSAPP_API_BASE_URL}/download"
        payload = {
            "message_id": message_id,
            "chat_jid": chat_jid
        }
        
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_S)
        
        if response.status_code == 200:
            result = response.json()
            if result.get("success", False):
                path = result.get("path")
                logger.info(f"Media downloaded successfully: {path}")
                return path
            else:
                logger.error(f"Download failed: {result.get('message', 'Unknown error')}")
                return None
        else:
            logger.error(f"Error: HTTP {response.status_code} - {response.text}")
            return None
            
    except requests.RequestException as e:
        logger.error(f"Request error: {str(e)}")
        return None
    except json.JSONDecodeError:
        logger.error(f"Error parsing response: {response.text}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return None

def read_message_log(query: Optional[str] = None, limit: int = 100) -> List[str]:
    """Read raw lines from the bridge's plain-text message log.

    The bridge (main.go) appends every message to this file the instant it arrives,
    before it can be revoked/deleted, so it's the durable record for messages that
    were later deleted for everyone.

    Args:
        query: If provided, only lines containing all these words (any order, ignoring case and accents) are returned
        limit: Maximum number of matching lines to return, most recent first
    """
    if not os.path.exists(MESSAGE_LOG_PATH):
        return []

    words = fold(query).split() if query else []
    matches: List[str] = []
    with open(MESSAGE_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not words or all(w in fold(line) for w in words):
                matches.append(line)

    return matches[-limit:][::-1]
