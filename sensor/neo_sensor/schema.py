"""The corpus schema, and the one way to create an empty corpus.

In the original deployment this lived in a one-time migration from the predecessor's Postgres
store. Here it is part of the package, because a new operator starts from nothing: `ensure_schema`
creates these tables when the file is empty, then layers the later migrations on top.
"""

SCHEMA = """
PRAGMA journal_mode = WAL;

-- The raw observation log.  Append-only.  Never read directly by the agent: every read goes
-- through `neo-corpus`, which redacts credentials and withholds non-admitted channels.
CREATE TABLE IF NOT EXISTS events (
    event_id          TEXT PRIMARY KEY,
    channel           TEXT NOT NULL CHECK (channel IN ('telegram', 'graph')),
    external_id       TEXT NOT NULL,
    chat_key          TEXT NOT NULL,
    observed_at       TEXT NOT NULL,
    sender            TEXT,
    direction         TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body              TEXT NOT NULL DEFAULT '',
    chat_display      TEXT,
    suppressed        INTEGER NOT NULL DEFAULT 0 CHECK (suppressed IN (0, 1)),
    suppressed_reason TEXT,
    ingested_at       TEXT
);

-- Dedup contract. (In the predecessor it could only be an index: its partition key
-- forbade a UNIQUE constraint.) Here it is a real constraint.
CREATE UNIQUE INDEX IF NOT EXISTS events_dedup      ON events (channel, external_id);
CREATE INDEX        IF NOT EXISTS events_chat_time  ON events (chat_key, observed_at);
CREATE INDEX        IF NOT EXISTS events_time       ON events (observed_at);
CREATE INDEX        IF NOT EXISTS events_outbound   ON events (direction, channel, observed_at);

-- Full-text search.  Replaces the predecessor's ILIKE scan, which was a
-- permanent performance ceiling.  External-content table: no duplicated bodies.
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
    USING fts5(body, content='events', content_rowid='rowid', tokenize='unicode61');

-- The scope half of the fence.  `disposition` decides what the agent may see at all.
CREATE TABLE IF NOT EXISTS channels (
    chat_key        TEXT PRIMARY KEY,
    channel         TEXT NOT NULL CHECK (channel IN ('telegram', 'graph')),
    display_name    TEXT,
    group_kind      TEXT,
    disposition     TEXT NOT NULL DEFAULT 'undecided'
                        CHECK (disposition IN ('work', 'mixed', 'personal', 'ignore', 'undecided')),
    seed_provenance TEXT,
    decided_at      TEXT,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS channels_disposition ON channels (disposition);
"""

# Dispositions whose message bodies may be emitted to the agent.
