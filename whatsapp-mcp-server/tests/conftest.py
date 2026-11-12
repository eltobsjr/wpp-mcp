import json

import pytest

import contacts


@pytest.fixture
def agenda(tmp_path, monkeypatch):
    """Agenda isolada em diretório temporário; nunca toca no contacts.json real."""
    path = tmp_path / "contacts.json"
    monkeypatch.setattr(contacts, "CONTACTS_PATH", str(path))

    def write(items):
        path.write_text(json.dumps({"contacts": items}, ensure_ascii=False), encoding="utf-8")

    return write


_SCHEMA = """
CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP);
CREATE TABLE messages (
    id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TIMESTAMP, is_from_me BOOLEAN,
    media_type TEXT, filename TEXT, url TEXT, media_key BLOB, file_sha256 BLOB,
    file_enc_sha256 BLOB, file_length INTEGER,
    PRIMARY KEY (id, chat_jid)
);
"""


class FakeStore:
    """Banco de mensagens sintético; nunca encosta no banco real do bridge."""

    def __init__(self, directory):
        import sqlite3

        self.dir = directory
        self.db = str(directory / "messages.db")
        self.conn = sqlite3.connect(self.db)
        self.conn.executescript(_SCHEMA)
        self._n = 0

    def chat(self, jid, name):
        self.conn.execute("INSERT OR REPLACE INTO chats(jid, name) VALUES (?, ?)", (jid, name))
        self.conn.commit()

    def add(self, chat_jid, content, ts, from_me=False, sender="5511999990000", media=None, filename=None):
        self._n += 1
        self.conn.execute(
            "INSERT INTO messages(id, chat_jid, sender, content, timestamp, is_from_me, media_type, filename) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"M{self._n}", chat_jid, sender, content, ts, from_me, media, filename))
        self.conn.commit()
        return f"M{self._n}"


@pytest.fixture
def store(tmp_path, monkeypatch):
    import identity
    import searchindex
    import whatsapp

    fake = FakeStore(tmp_path)
    monkeypatch.setattr(whatsapp, "MESSAGES_DB_PATH", fake.db)
    monkeypatch.setattr(whatsapp, "MESSAGE_LOG_PATH", str(tmp_path / "mensagens_log.txt"))
    monkeypatch.setattr(identity, "MESSAGES_DB_PATH", str(tmp_path / "ausente-messages.db"))
    monkeypatch.setattr(identity, "WHATSAPP_DB_PATH", str(tmp_path / "ausente-whatsapp.db"))
    monkeypatch.setattr(searchindex, "INDEX_PATH", str(tmp_path / "search_index.db"))
    monkeypatch.setattr(searchindex, "_build_thread", None)
    monkeypatch.setattr(whatsapp, "get_sender_name", lambda s: s)
    identity.invalidate()
    yield fake
    fake.conn.close()
