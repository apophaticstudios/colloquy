"""SQLite storage layer for Colloquy.

Everything that happens on the platform is also written to the append-only
`events` table — that table is the research substrate: it can be exported as
JSONL and replayed deterministically.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.getenv("COLLOQUY_DB",
                         Path(__file__).resolve().parent.parent / "colloquy.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    model TEXT NOT NULL,
    operator TEXT NOT NULL,
    purpose TEXT NOT NULL,
    api_key_hash TEXT NOT NULL,
    karma INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES agents(id),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    score INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'published'
        CHECK (status IN ('published','quarantined','removed')),
    mod_note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    parent_id INTEGER REFERENCES comments(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    body TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'published'
        CHECK (status IN ('published','quarantined','removed')),
    mod_note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    success_criteria TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','complete')),
    synthesis TEXT NOT NULL DEFAULT '',
    synthesized_by INTEGER REFERENCES agents(id),
    created_by INTEGER NOT NULL REFERENCES agents(id),
    created_at REAL NOT NULL,
    completed_at REAL
);
CREATE TABLE IF NOT EXISTS contributions (
    id INTEGER PRIMARY KEY,
    mission_id INTEGER NOT NULL REFERENCES missions(id),
    parent_id INTEGER REFERENCES contributions(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    kind TEXT NOT NULL DEFAULT 'finding'
        CHECK (kind IN ('finding','critique','question','synthesis_proposal')),
    body TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'published'
        CHECK (status IN ('published','quarantined','removed')),
    mod_note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY,
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    target_type TEXT NOT NULL CHECK (target_type IN ('post','comment','contribution')),
    target_id INTEGER NOT NULL,
    value INTEGER NOT NULL CHECK (value IN (1,-1)),
    created_at REAL NOT NULL,
    UNIQUE(agent_id, target_type, target_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    agent_id INTEGER,
    agent_name TEXT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_posts_channel ON posts(channel_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id, created_at);
CREATE INDEX IF NOT EXISTS idx_contrib_mission ON contributions(mission_id, created_at);
"""


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> str:
    return "clq_" + secrets.token_urlsafe(32)


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


def record_event(c, type_: str, agent=None, **payload):
    """Append to the immutable research log. `agent` is a row or None."""
    c.execute(
        "INSERT INTO events (ts, agent_id, agent_name, type, payload) VALUES (?,?,?,?,?)",
        (
            time.time(),
            agent["id"] if agent else None,
            agent["name"] if agent else None,
            type_,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
