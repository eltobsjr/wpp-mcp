"""Caminhos e configurações do servidor MCP, num lugar só.

Tudo pode ser sobrescrito por variável de ambiente, o que permite rodar testes
contra uma cópia dos dados sem encostar no banco real.
"""
import os

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STORE_DIR = os.path.abspath(
    os.environ.get("WHATSAPP_STORE_DIR")
    or os.path.join(_BASE_DIR, "..", "whatsapp-bridge", "store")
)
MESSAGES_DB_PATH = os.path.join(STORE_DIR, "messages.db")
WHATSAPP_DB_PATH = os.path.join(STORE_DIR, "whatsapp.db")
MESSAGE_LOG_PATH = os.path.join(STORE_DIR, "mensagens_log.txt")
SEARCH_INDEX_PATH = os.environ.get("WHATSAPP_SEARCH_INDEX") or os.path.join(STORE_DIR, "search_index.db")
CONVERSATION_STATE_PATH = os.environ.get("WHATSAPP_CONVERSATION_STATE") or os.path.join(STORE_DIR, "conversation_mode.json")
LIVE_SESSION_PATH = os.environ.get("WHATSAPP_LIVE_SESSION") or os.path.join(STORE_DIR, "live_session.json")
AUTOMATION_DIR = os.environ.get("OPENBOT_AUTOMATION_DIR") or os.path.join(
    os.path.expanduser("~"), ".config", "OpenBot", "automation")
CONTACTS_PATH = os.environ.get("WHATSAPP_CONTACTS_FILE") or os.path.join(STORE_DIR, "contacts.json")

BRIDGE_PORT = os.environ.get("WHATSAPP_BRIDGE_PORT", "9090")
BRIDGE_API_BASE_URL = f"http://localhost:{BRIDGE_PORT}/api"
REQUEST_TIMEOUT_S = 10
