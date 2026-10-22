"""SQLite persistence layer for NPU-Assistant (LLM Wiki edition).

Design notes
------------
* No ORM: a thin helper layer over the stdlib ``sqlite3`` keeps the MVP
  dependency-free and easy to audit.
* One short-lived connection per operation. WAL mode is enabled for better
  concurrent reads from the bot gateway and the web UI.
* All timestamps are ISO-8601 strings in local server time.
* Knowledge is stored as **wiki pages** (compiled by an LLM) plus **source
  documents**.  The old RAG ``chunks`` table is removed by migration.

Knowledge model
---------------
* ``documents``    – uploaded source files (the ground truth).
* ``wiki_pages``   – LLM-compiled, human-readable knowledge entries.
* ``wiki_sources`` – provenance anchors: which document / section / quote a
  wiki page was compiled from.
* ``wiki_links``   – related-page links inside the wiki.
* ``wiki_builds``  – compilation audit log (status, model, page counters).
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
    page_count   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wiki_pages (
    id           TEXT PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    body_md      TEXT NOT NULL DEFAULT '',
    aliases      TEXT NOT NULL DEFAULT '[]',
    tags         TEXT NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'ready',     -- ready|needs_review
    version      INTEGER NOT NULL DEFAULT 1,
    source_count INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_title ON wiki_pages(title);

CREATE TABLE IF NOT EXISTS wiki_sources (
    id               TEXT PRIMARY KEY,
    page_id          TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    document_id      TEXT NOT NULL,
    document_title   TEXT NOT NULL,
    source_file      TEXT NOT NULL,
    section          TEXT,
    quote            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wiki_sources_page ON wiki_sources(page_id);
CREATE INDEX IF NOT EXISTS idx_wiki_sources_document ON wiki_sources(document_id);

CREATE TABLE IF NOT EXISTS wiki_links (
    id            TEXT PRIMARY KEY,
    from_page_id  TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    to_page_id    TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
    relation      TEXT NOT NULL DEFAULT 'related',
    UNIQUE(from_page_id, to_page_id)
);

CREATE TABLE IF NOT EXISTS wiki_builds (
    id             TEXT PRIMARY KEY,
    document_id    TEXT,
    status         TEXT NOT NULL DEFAULT 'running',  -- running|ready|failed
    model          TEXT,
    pages_created  INTEGER NOT NULL DEFAULT 0,
    pages_updated  INTEGER NOT NULL DEFAULT 0,
    pages_removed  INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT
);

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
# Bootstrap & migrations (RAG -> LLM Wiki)
# ---------------------------------------------------------------------------
def _column_names(table: str) -> set[str]:
    return {row["name"] for row in query(f"PRAGMA table_info({table})")}


def init_db() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()

    # 1) Remove the legacy RAG chunk table entirely.
    execute("DROP TABLE IF EXISTS chunks")

    # 2) Legacy documents schema had chunk_count instead of page_count.
    columns = _column_names("documents")
    if "page_count" not in columns:
        execute("ALTER TABLE documents ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0")
    if "chunk_count" in columns:
        try:
            execute("ALTER TABLE documents DROP COLUMN chunk_count")
        except sqlite3.OperationalError:  # pragma: no cover - older SQLite
            pass

    # 3) Drop settings that only existed for BM25 retrieval.
    for key in config.OBSOLETE_SETTINGS:
        execute("DELETE FROM settings WHERE key = ?", (key,))

    for key, value in config.DEFAULT_SETTINGS.items():
        if get_setting(key) is None:
            set_setting(key, value)

    # A zero-configuration demo model so the product is usable before the
    # administrator wires up a real LLM provider.  Note: compiling wiki pages
    # requires a real model; the mock only supports demo/fallback answers.
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
            (new_id(), provider_id, "local-wiki", "本地 Wiki 阅读（演示）", 0.2, 1200, now()),
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


# ---------------------------------------------------------------------------
# Wiki helpers (shared by routers/services; SQL-heavy logic lives in
# services/wiki_builder.py and services/wiki_nav.py)
# ---------------------------------------------------------------------------
def wiki_page_count() -> int:
    row = query_one("SELECT COUNT(*) AS n FROM wiki_pages")
    return int(row["n"]) if row else 0


def get_wiki_page(page_id: str) -> dict | None:
    return query_one("SELECT * FROM wiki_pages WHERE id = ?", (page_id,))


def get_wiki_page_by_slug(slug: str) -> dict | None:
    return query_one("SELECT * FROM wiki_pages WHERE slug = ?", (slug,))


def get_page_sources(page_id: str) -> list[dict]:
    return query("SELECT * FROM wiki_sources WHERE page_id = ? ORDER BY created_at", (page_id,))


def wiki_stats() -> dict:
    pages = wiki_page_count()
    sources = query_one("SELECT COUNT(*) AS n FROM wiki_sources")
    documents = query_one("SELECT COUNT(*) AS n FROM documents WHERE status = 'ready'")
    tags: dict[str, int] = {}
    for row in query("SELECT tags FROM wiki_pages"):
        for tag in from_json(row.get("tags"), []) or []:
            tags[tag] = tags.get(tag, 0) + 1
    return {
        "pages": pages,
        "sources": int(sources["n"]) if sources else 0,
        "documents": int(documents["n"]) if documents else 0,
        "by_tag": tags,
    }
