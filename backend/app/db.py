"""SQLite persistence layer for NPU-Assistant.

Design notes
------------
* No ORM: a thin helper layer over the stdlib ``sqlite3`` keeps the MVP
  dependency-free and easy to audit.
* One short-lived connection per operation (``check_same_thread=False`` is not
  needed because every call opens its own connection). WAL mode is enabled for
  better concurrent reads from the bot gateway and the web UI.
* All timestamps are ISO-8601 strings in local server time.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    filename     TEXT NOT NULL,
    stored_path  TEXT NOT NULL,
    file_ext     TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending|processing|ready|failed
    error        TEXT,
    tags         TEXT NOT NULL DEFAULT '[]',
    char_count   INTEGER NOT NULL DEFAULT 0,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    section      TEXT,
    content      TEXT NOT NULL,
    tokens       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);

CREATE TABLE IF NOT EXISTS providers (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    type             TEXT NOT NULL,                 -- openai|ollama|mock
    base_url         TEXT NOT NULL DEFAULT '',
    api_key          TEXT NOT NULL DEFAULT '',
    embedding_model  TEXT NOT NULL DEFAULT '',
    extra            TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models_registry (
    id            TEXT PRIMARY KEY,
    provider_id   TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    model_name    TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    temperature   REAL NOT NULL DEFAULT 0.3,
    max_tokens    INTEGER NOT NULL DEFAULT 1200,
    is_default    INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id           TEXT PRIMARY KEY,
    channel      TEXT NOT NULL DEFAULT 'web',       -- web|onebot11|wechat_mp|console
    channel_id   TEXT,
    session_key  TEXT NOT NULL,
    user_label   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_key);

CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL,                 -- user|assistant
    content          TEXT NOT NULL,
    citations        TEXT NOT NULL DEFAULT '[]',
    model            TEXT,
    latency_ms       INTEGER,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS bot_channels (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,                    -- onebot11|wechat_mp|console
    name          TEXT NOT NULL,
    config        TEXT NOT NULL DEFAULT '{}',
    enabled       INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'offline',
    last_seen_at  TEXT,
    created_at    TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Low level helpers
# ---------------------------------------------------------------------------
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Run a write statement and return lastrowid."""
    with connect() as conn:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return int(cur.lastrowid or 0)


def executemany(sql: str, seq: Iterable[Iterable[Any]]) -> None:
    with connect() as conn:
        conn.executemany(sql, [tuple(p) for p in seq])
        conn.commit()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_setting(key: str, default: str | None = None) -> str | None:
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def get_bool_setting(key: str, default: bool = False) -> bool:
    value = get_setting(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def all_settings() -> dict[str, str]:
    return {row["key"]: row["value"] for row in query("SELECT key, value FROM settings")}


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def init_db() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()

    for key, value in config.DEFAULT_SETTINGS.items():
        if get_setting(key) is None:
            set_setting(key, value)

    # A zero-configuration demo model so the product is usable before the
    # administrator wires up a real LLM provider.
    if not query_one("SELECT id FROM providers LIMIT 1"):

        provider_id = new_id()
        execute(
            "INSERT INTO providers(id, name, type, base_url, api_key, embedding_model, extra, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (provider_id, "内置演示模型（无需 API Key）", "mock", "", "", "", "{}", now()),
        )
        execute(
            "INSERT INTO models_registry(id, provider_id, model_name, display_name, temperature, max_tokens, "
            "is_default, enabled, created_at) VALUES(?, ?, ?, ?, ?, ?, 1, 1, ?)",
            (new_id(), provider_id, "local-rag", "本地知识库问答（演示）", 0.2, 1200, now()),
        )

    # A console channel is always available for local debugging of the bot
    # message pipeline without configuring QQ / WeChat.
    if not query_one("SELECT id FROM bot_channels WHERE type = 'console' LIMIT 1"):
        execute(
            "INSERT INTO bot_channels(id, type, name, config, enabled, status, created_at) "
            "VALUES(?, 'console', '控制台调试通道', '{}', 1, 'online', ?)",
            (new_id(), now()),
        )


# ---------------------------------------------------------------------------
# Small shared lookups used by routers/services
# ---------------------------------------------------------------------------
def get_document(document_id: str) -> dict | None:
    return query_one("SELECT * FROM documents WHERE id = ?", (document_id,))


def get_default_model() -> dict | None:
    """Return default model row joined with its provider."""
    row = query_one(
        "SELECT m.*, p.name AS provider_name, p.type AS provider_type, p.base_url, p.api_key, "
        "p.embedding_model, p.extra AS provider_extra "
        "FROM models_registry m JOIN providers p ON p.id = m.provider_id "
        "WHERE m.is_default = 1 AND m.enabled = 1 LIMIT 1"
    )
    if row:
        return row
    return query_one(
        "SELECT m.*, p.name AS provider_name, p.type AS provider_type, p.base_url, p.api_key, "
        "p.embedding_model, p.extra AS provider_extra "
        "FROM models_registry m JOIN providers p ON p.id = m.provider_id "
        "WHERE m.enabled = 1 ORDER BY m.created_at LIMIT 1"
    )


def get_conversation(conversation_id: str) -> dict | None:
    return query_one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))


def find_or_create_conversation(channel: str, session_key: str, channel_id: str | None = None,
                                user_label: str | None = None) -> dict:
    row = query_one(
        "SELECT * FROM conversations WHERE session_key = ? AND channel = ? "
        "ORDER BY updated_at DESC LIMIT 1",
        (session_key, channel),
    )
    if row:
        execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), row["id"]))
        return row
    conversation_id = new_id()
    timestamp = now()
    execute(
        "INSERT INTO conversations(id, channel, channel_id, session_key, user_label, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, channel, channel_id, session_key, user_label, timestamp, timestamp),
    )
    return get_conversation(conversation_id)  # type: ignore[return-value]


def add_message(conversation_id: str, role: str, content: str, citations: list | None = None,
                model: str | None = None, latency_ms: int | None = None) -> dict:
    message_id = new_id()
    execute(
        "INSERT INTO messages(id, conversation_id, role, content, citations, model, latency_ms, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (message_id, conversation_id, role, content, to_json(citations or []), model, latency_ms, now()),
    )
    execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), conversation_id))
    return query_one("SELECT * FROM messages WHERE id = ?", (message_id,))  # type: ignore[return-value]
