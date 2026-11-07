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
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Iterable

from . import config

logger = logging.getLogger("npu-assistant")

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
    source_type  TEXT NOT NULL DEFAULT 'file',      -- file|url|text
    source_url   TEXT NOT NULL DEFAULT '',
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
    channel      TEXT NOT NULL DEFAULT 'web',       -- web|qq_official|wechat_mp|console
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
    type          TEXT NOT NULL,                    -- qq_official|wechat_mp|console
    name          TEXT NOT NULL,
    config        TEXT NOT NULL DEFAULT '{}',
    enabled       INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'offline',
    last_seen_at  TEXT,
    created_at    TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- v0.2.0 RSI: feedback + knowledge gaps
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS answer_feedback (
    id              TEXT PRIMARY KEY,
    message_id      TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    channel         TEXT NOT NULL DEFAULT 'web',
    rating          INTEGER NOT NULL,               -- 1=helpful, -1=unhelpful
    reason_code     TEXT NOT NULL DEFAULT '',       -- not_found|incomplete|expired|bad_citation|other
    comment         TEXT NOT NULL DEFAULT '',
    voter_key       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'open',   -- open|triaged|resolved|ignored
    handled_by      TEXT,
    handled_at      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(message_id, voter_key)
);
CREATE INDEX IF NOT EXISTS idx_feedback_status ON answer_feedback(status, created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_message ON answer_feedback(message_id);

CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id                 TEXT PRIMARY KEY,
    question           TEXT NOT NULL,
    normalized_key     TEXT NOT NULL,
    sample_message_id  TEXT,
    channel            TEXT NOT NULL DEFAULT 'web',
    count              INTEGER NOT NULL DEFAULT 1,
    reasons            TEXT NOT NULL DEFAULT '[]',
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'open',-- open|linked|resolved|ignored
    priority           INTEGER NOT NULL DEFAULT 0,  -- 0 normal, 1 high
    linked_page_id     TEXT,
    linked_document_id TEXT,
    assignee_id        TEXT,
    note               TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gaps_status ON knowledge_gaps(status, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_gaps_key ON knowledge_gaps(normalized_key);

-- ---------------------------------------------------------------------------
-- v0.2.0 handoff + crisis
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS handoff_requests (
    id                TEXT PRIMARY KEY,
    ticket_no         TEXT NOT NULL UNIQUE,
    ticket_token      TEXT NOT NULL,
    conversation_id   TEXT NOT NULL,
    message_id        TEXT,
    channel           TEXT NOT NULL DEFAULT 'web',
    session_key       TEXT NOT NULL DEFAULT '',
    user_label        TEXT,
    contact           TEXT NOT NULL DEFAULT '',
    reason            TEXT NOT NULL,
    priority          TEXT NOT NULL DEFAULT 'normal',  -- normal|urgent
    status            TEXT NOT NULL DEFAULT 'pending', -- pending|claimed|replied|resolved|closed
    summary           TEXT NOT NULL DEFAULT '',
    assignee_id       TEXT,
    resolution        TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT 'student', -- student|system|crisis
    created_at        TEXT NOT NULL,
    claimed_at        TEXT,
    first_response_at TEXT,
    resolved_at       TEXT,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_handoff_status ON handoff_requests(status, priority, created_at);

CREATE TABLE IF NOT EXISTS crisis_events (
    id               TEXT PRIMARY KEY,
    handoff_id       TEXT,
    conversation_id  TEXT NOT NULL,
    message_id       TEXT,
    category         TEXT NOT NULL,
    matched_terms    TEXT NOT NULL DEFAULT '[]',
    severity         TEXT NOT NULL DEFAULT 'high',
    status           TEXT NOT NULL DEFAULT 'open',     -- open|acknowledged|resolved|false_positive
    notified_at      TEXT,
    notify_result    TEXT NOT NULL DEFAULT '',
    handled_by       TEXT,
    handled_at       TEXT,
    note             TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crisis_status ON crisis_events(status, created_at);

CREATE TABLE IF NOT EXISTS notification_log (
    id           TEXT PRIMARY KEY,
    channel      TEXT NOT NULL,
    target       TEXT NOT NULL DEFAULT '',
    event_type   TEXT NOT NULL,
    resource_id  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',      -- pending|sent|failed
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    sent_at      TEXT
);

CREATE TABLE IF NOT EXISTS admin_users (
    id                   TEXT PRIMARY KEY,
    username             TEXT NOT NULL UNIQUE,
    display_name         TEXT NOT NULL DEFAULT '',
    role                 TEXT NOT NULL DEFAULT 'viewer',  -- super_admin|admin|counselor|viewer
    password_hash        TEXT NOT NULL,
    active               INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_by           TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    password_updated_at  TEXT NOT NULL DEFAULT '',
    last_login_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_admin_users_role ON admin_users(role, active);

CREATE TABLE IF NOT EXISTS admin_sessions (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL UNIQUE,
    csrf_token   TEXT NOT NULL,
    ip           TEXT NOT NULL DEFAULT '',
    user_agent   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_user ON admin_sessions(user_id, expires_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id   TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    ip          TEXT NOT NULL DEFAULT '',
    status_code INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL,                    -- ingest_upload|reindex|rebuild
    status           TEXT NOT NULL DEFAULT 'queued',   -- queued|running|succeeded|failed|cancelled
    payload          TEXT NOT NULL DEFAULT '{}',
    result           TEXT NOT NULL DEFAULT '{}',
    progress         INTEGER NOT NULL DEFAULT 0,
    message          TEXT NOT NULL DEFAULT '',
    error            TEXT NOT NULL DEFAULT '',
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 2,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_by       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON jobs(type, created_at);

CREATE TABLE IF NOT EXISTS system_alerts (
    id          TEXT PRIMARY KEY,
    level       TEXT NOT NULL DEFAULT 'warning',       -- info|warning|error|critical
    category    TEXT NOT NULL DEFAULT 'system',
    title       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',          -- open|acknowledged|resolved
    source      TEXT NOT NULL DEFAULT '',
    resource_id TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON system_alerts(status, created_at);
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
    # Mobile quick-ingest: track where a document came from (file/url/text).
    if "source_type" not in columns:
        execute("ALTER TABLE documents ADD COLUMN source_type TEXT NOT NULL DEFAULT 'file'")
    if "source_url" not in columns:
        execute("ALTER TABLE documents ADD COLUMN source_url TEXT NOT NULL DEFAULT ''")
    # v0.2.0 freshness: publication / effective / expiry / verification dates.
    for column in (
        "published_at",
        "effective_at",
        "expires_at",
        "verified_at",
        "academic_year",
        "doc_type",
    ):
        if column not in columns:
            default = "'general'" if column == "doc_type" else "''"
            execute(
                f"ALTER TABLE documents ADD COLUMN {column} TEXT NOT NULL DEFAULT {default}"
            )
    execute("CREATE INDEX IF NOT EXISTS idx_documents_source_url ON documents(source_url)")
    # URL imports are deduplicated by source_url; enforce it at the DB level too
    # so concurrent imports cannot create duplicate documents.
    try:
        execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_source_url "
            "ON documents(source_url) WHERE source_url <> ''"
        )
    except sqlite3.IntegrityError:  # pragma: no cover - legacy duplicate data
        # Clean historical duplicates (keep the oldest document per URL) and
        # retry once; silently skipping the index would break concurrency safety.
        logger.warning("documents.source_url 存在历史重复，正在清理后重建唯一索引")
        execute(
            "DELETE FROM documents WHERE source_url <> '' AND rowid NOT IN "
            "(SELECT MIN(rowid) FROM documents WHERE source_url <> '' GROUP BY source_url)"
        )
        execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_source_url "
            "ON documents(source_url) WHERE source_url <> ''"
        )

    # 3) v0.2.0 RSI: persist retrieval signals on messages.
    message_columns = _column_names("messages")
    if "retrieval" not in message_columns:
        execute("ALTER TABLE messages ADD COLUMN retrieval TEXT NOT NULL DEFAULT '{}'")
    if "fallback" not in message_columns:
        execute("ALTER TABLE messages ADD COLUMN fallback INTEGER NOT NULL DEFAULT 0")
    if "navigation_method" not in message_columns:
        execute(
            "ALTER TABLE messages ADD COLUMN navigation_method TEXT NOT NULL DEFAULT ''"
        )

    # 4) v0.2.0 freshness on wiki pages (documents handled above).
    page_columns = _column_names("wiki_pages")
    for column in (
        "published_at",
        "effective_at",
        "expires_at",
        "verified_at",
        "academic_year",
        "doc_type",
        "verified_by",
    ):
        if column not in page_columns:
            default = "'general'" if column == "doc_type" else "''"
            execute(
                f"ALTER TABLE wiki_pages ADD COLUMN {column} TEXT NOT NULL DEFAULT {default}"
            )

    # 5) Drop settings that only existed for BM25 retrieval.
    for key in config.OBSOLETE_SETTINGS:
        execute("DELETE FROM settings WHERE key = ?", (key,))

    for key, value in config.DEFAULT_SETTINGS.items():
        if get_setting(key) is None:
            set_setting(key, value)
    if not (get_setting("academic_year") or "").strip():
        set_setting("academic_year", config.current_academic_year())

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


def get_documents_meta(document_ids: Iterable[str]) -> dict[str, dict]:
    """批量取源文件来源信息（含时效字段），用于引用补链接与新鲜度展示。"""
    ids = [str(item) for item in document_ids if item]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = query(
        "SELECT id, source_type, source_url, filename, published_at, effective_at, "
        "expires_at, verified_at, academic_year, doc_type "
        f"FROM documents WHERE id IN ({placeholders})",
        ids,
    )
    return {str(row["id"]): row for row in rows}


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
                model: str | None = None, latency_ms: int | None = None,
                retrieval: dict | None = None, fallback: bool = False,
                navigation_method: str = "") -> dict:
    message_id = new_id()
    execute(
        "INSERT INTO messages(id, conversation_id, role, content, citations, model, latency_ms, "
        "retrieval, fallback, navigation_method, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            conversation_id,
            role,
            content,
            to_json(citations or []),
            model,
            latency_ms,
            to_json(retrieval or {}),
            1 if fallback else 0,
            str(navigation_method or ""),
            now(),
        ),
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
