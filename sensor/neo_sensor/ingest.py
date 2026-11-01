"""The write path into the fenced corpus — one door, both channels.

Until now nothing in `sensor/` could create an event row. `enrich` only `UPDATE`s, and the
only thing that ever inserted was the one-time migration from the predecessor's store. This
module is the insert path, and it exists as one module rather than two so that the properties
the fence depends on are structural instead of remembered twice.

Three of those properties:

**Redaction is not a parameter.** `Writer.write()` takes a raw body and redacts it itself.
There is no way for a caller to supply `body_redacted`, so there is no call site that can
forget to, and no flag that can be hardcoded `True` the way `credentials_redacted` was in the
system this replaces. The row is written with `redacted_at` stamped in the same statement that
writes the body, so "the filter ran" stays a schema property.

**A channel nobody has ruled on is `undecided`, and `undecided` is withheld.** A new chat
arrives with its bodies stored and unreadable. Admission is the operator's act — `neo-channels`
is denied to the agent outright — so ingest may create a channel row but may never choose a
disposition for it, and must never touch the disposition of a channel that already has one.

**Re-running is safe.** `(channel, external_id)` is a UNIQUE index and every insert is
`INSERT OR IGNORE`, so a pull that dies halfway costs nothing but the time to redo it. That is
what lets the cursor be advisory rather than load-bearing: it decides where to *start*, not
what is allowed to be written.

The raw body is stored in `events.body` exactly as it arrives, as it always was. It sits behind
the fence — `neo-corpus` cannot name that column and `tests/test_fence.py` fails if a future
edit tries. Nothing this module prints is ever taken from it.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from neo_sensor.config import STATE_DIR

# Pure regex, no model load — unlike `redaction`, which stays deferred.
from neo_sensor.relevance import classify

DEFAULT_DB = STATE_DIR / "corpus.db"

# A stable namespace so `event_id` is a pure function of the message it describes. Deterministic
# rather than random because it makes a re-pull produce byte-identical rows, which is what turns
# "did this run write what I think it wrote" into a question a diff can answer.
NAMESPACE = uuid.UUID("6f1e0f8c-6d3a-5c1a-9f2b-1d0c7a4e5b93")

MIGRATION = """
-- Telegram's `forward_from`: who WROTE a message, when that is not who sent it. The corpus has
-- no such column for its 86,182 historical rows and cannot get one — the predecessor's ingest
-- never kept it — so at minimum 44 rows record the operator greeting himself, and a voice model
-- over raw `outbound` trains partly on other people's prose. Operator's ruling 2026-08-15
-- (Q-031): capture it going forward. NULL means "not a forward", not "unknown".
ALTER TABLE events ADD COLUMN forwarded_from TEXT;

-- HOW `direction` was decided, per row. Historical Graph rows derived it from the folder the
-- message sat in, which reads a mail he wrote and also received as inbound (24 rows) and a
-- send-as copy from someone else as outbound (1 row). New rows derive it from the author.
-- Both are called `direction`; only this column says which question was actually answered.
ALTER TABLE events ADD COLUMN direction_source TEXT;

-- Where each pull should resume. Advisory: the dedup index is what guarantees correctness.
CREATE TABLE IF NOT EXISTS sync_state (
    scope       TEXT PRIMARY KEY,
    cursor      TEXT,
    last_run_at TEXT,
    note        TEXT
);
"""

# The four columns that make a pending channel recognisable. A registration row used to hold a
# name, a volume and a resume point and nothing else — which is enough to *rule* on a chat whose
# title reads "Alex & Pat & Sam" and nothing at all for one that reads `9000000001`. Telegram
# gives a bare numeric id whenever the account has no title, and `dialog.name` is title-or-nothing,
# so the one case that most needs context was the one guaranteed not to get any.
#
# `pending_username`, `pending_kind` and `pending_last_at` are pure metadata and were always
# inside the metadata-only rule; the bug was that nothing captured them. `pending_preview` is
# NOT — it is the redacted first ~200 characters of the newest message, and it exists because
# the operator ruled on 2026-08-24 that a bare id handed to him for a decision is unanswerable:
# *"Don't give me a bare ID and ask me to decide. Give me context!!! username, last message
# (time and content)!!!"*
#
# All of them — these four and the `pending_last_out` added below — are cleared the moment the
# channel stops being pending, in `note_channel`, which is the only place that does it. (This
# comment named a `clear_pending` alongside it until 2026-09-07; no such function has ever
# existed.) Their whole life is the undecided window they exist to close, which is what keeps
# `register_pending`'s original argument intact: the corpus still never becomes the thing
# standing between his family chat and a database.
PENDING_MIGRATION = """
ALTER TABLE channels ADD COLUMN pending_username TEXT;
ALTER TABLE channels ADD COLUMN pending_kind     TEXT;
ALTER TABLE channels ADD COLUMN pending_last_at  TEXT;
ALTER TABLE channels ADD COLUMN pending_preview  TEXT;
"""

# The fifth column, added 2026-09-07, and it exists because the four above half-defeated their
# own purpose. `pending_preview` holds the newest message in the dialog **whichever way it was
# going**, and roughly half of all newest messages in a 1:1 are the operator's own — so the
# context offered to him as "what this account said" was as often as not what *he* said,
# attributed to the stranger. It happened the day it was found: `9000000002` was put to him as a
# 1:1 whose message read `👌 will be there in a minute`, which was his; the account's message,
# readable only after he ruled it `work`, was `Hey there! I have a window now to review the
# hard drive…`.
#
# The cost that time was zero, because `F-007` records that his own message is often the thing he
# recognises a thread by — *"if not, I need a complete message, or context"*. The mechanism is
# still wrong: it was LABELLED as the account's, and "this person said X" is a fact about a
# person, which `F-012` says needs a message where the person and the claim appear together. A
# preview with no direction on it cannot supply one.
#
# Its own guard, for the reason `ensure_pending_columns` already gives: a corpus migrated when
# there were four columns passes a guard that asks about the fourth.
PENDING_DIRECTION_MIGRATION = """
ALTER TABLE channels ADD COLUMN pending_last_out INTEGER;
"""


# What the historical rows actually did, stamped once so the two derivations are told apart in
# the data rather than in a comment. Telegram's came from Telethon's `out` flag, which is
# author-derived already; Graph's came from the folder, which is not.
BACKFILL_SOURCE = """
UPDATE events SET direction_source='folder'   WHERE channel='graph'    AND direction_source IS NULL;
UPDATE events SET direction_source='out-flag' WHERE channel='telegram' AND direction_source IS NULL;
"""


# How the 86,182 existing rows render a UTC timestamp: Postgres' `+00`, not `Z` and not
# `+00:00`. `observed_at` is TEXT and every window query compares it as text, so a second
# rendering of the same instant would sort and filter correctly by luck rather than by design.
# One shape in the column, decided here, applied on the way in.
CORPUS_TIME = "%Y-%m-%dT%H:%M:%S+00"

# `fromisoformat` wants a two-part offset; Postgres writes a one-part one.
_SHORT_OFFSET = re.compile(r"([+-]\d{2})$")


def as_utc(value: str | datetime) -> datetime:
    """Any of the timestamp shapes this project meets, as an aware UTC datetime.

    Four arrive in practice: Telethon's `+00:00`, Graph's `Z`, the corpus's `+00`, and a bare
    `YYYY-MM-DD` from `--since`. A naive parse of the third raised, which is how the first live
    dry run failed — with an error that named a URL rather than a date format.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = value.strip()
    if "T" not in text:
        text += "T00:00:00+00:00"
    text = text.replace("Z", "+00:00")
    text = _SHORT_OFFSET.sub(r"\1:00", text)
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def corpus_stamp(value: str | datetime) -> str:
    """A timestamp in the shape the column already holds."""
    return as_utc(value).strftime(CORPUS_TIME)


def graph_stamp(value: str | datetime) -> str:
    """A timestamp Graph will accept inside `$filter`.

    `Z` rather than an offset, and deliberately so: a literal `+` in a query string is a
    space once decoded, so `…+00` reached the API as `… 00` and came back 400.
    """
    return as_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class IncomingEvent:
    """One observed message, before redaction. Raw — never printed, never returned to a reader."""

    channel: str
    external_id: str
    chat_key: str
    observed_at: str
    sender: str | None
    direction: str
    direction_source: str
    body: str
    chat_display: str | None = None
    forwarded_from: str | None = None
    subject: str | None = None
    recipients: list[str] = field(default_factory=list)
    conversation_id: str | None = None
    body_source: str | None = None
    # Char spans (NOT Telegram's UTF-16 offsets — converted at the fetcher) that the sender
    # marked as a spoiler. Empty for Outlook, which has no equivalent. Carried on the event
    # rather than applied at the fetcher because `body` is the RAW column and must stay raw:
    # masking there would destroy the only copy and make the redaction irreversible.
    spoiler_spans: list[tuple[int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Normalised here rather than at each call site, so neither channel can introduce a
        # second timestamp shape by forgetting to.
        self.observed_at = corpus_stamp(self.observed_at)

    @property
    def event_id(self) -> str:
        return str(uuid.uuid5(NAMESPACE, f"neo:{self.channel}:{self.external_id}"))


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Bring an existing corpus up to what the write path needs. Idempotent.

    Composes the two migrations that already exist rather than restating their columns: the
    redaction columns come from `redact_corpus`, the enrichment columns from `graph_fetch`. A
    third copy of an `ALTER TABLE` list is a third place to forget one.
    """
    from neo_sensor.graph_fetch import ensure_columns as ensure_enrichment
    from neo_sensor.redact_corpus import ensure_columns as ensure_redaction
    from neo_sensor.schema import SCHEMA

    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone():
        conn.executescript(SCHEMA)  # a brand-new, empty corpus
        conn.commit()

    ensure_redaction(conn)
    ensure_enrichment(conn)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    if "forwarded_from" not in cols:
        conn.executescript(MIGRATION)
        conn.executescript(BACKFILL_SOURCE)
        conn.commit()
        print("migrated: forwarded_from / direction_source columns, sync_state table")
    ensure_pending_columns(conn)


def ensure_pending_columns(conn: sqlite3.Connection) -> None:
    """Add the pending-context columns to `channels` if they are not there yet.

    Guarded on its own column rather than folded into `MIGRATION`, whose guard is
    `events.forwarded_from` — a corpus migrated before this existed passes that guard and would
    skip these four silently. A migration whose guard belongs to a different table is a
    migration that runs once and then never again on the machines that need it.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(channels)")}
    if "pending_preview" not in cols:
        conn.executescript(PENDING_MIGRATION)
        conn.commit()
        print("migrated: pending_username / pending_kind / pending_last_at / pending_preview")
        cols.add("pending_preview")
    if "pending_last_out" not in cols:
        conn.executescript(PENDING_DIRECTION_MIGRATION)
        conn.commit()
        print("migrated: pending_last_out")


# --------------------------------------------------------------------------- cursor


def get_cursor(conn: sqlite3.Connection, scope: str) -> str | None:
    """The stored resume point, or None.

    Tolerates the table being absent rather than requiring it: a dry run opens the corpus
    read-only and deliberately does not migrate it, so the first `--dry-run` on any machine
    asks for a table that will not exist until the first `--commit`. Failing there would make
    the safe verb the one that cannot be run first.
    """
    try:
        row = conn.execute("SELECT cursor FROM sync_state WHERE scope=?", (scope,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def set_cursor(conn: sqlite3.Connection, scope: str, cursor: str, note: str = "") -> None:
    conn.execute(
        """INSERT INTO sync_state (scope, cursor, last_run_at, note)
                VALUES (?, ?, datetime('now'), ?)
           ON CONFLICT(scope) DO UPDATE SET
                cursor=excluded.cursor, last_run_at=excluded.last_run_at, note=excluded.note""",
        (scope, cursor, note),
    )


def corpus_high_water(conn: sqlite3.Connection, channel: str) -> str | None:
    """The newest message already stored for a channel.

    The fallback when `sync_state` has no cursor — which is every first run, since the table
    arrives with this module and the corpus predates it by 86,182 rows. Derived from the data
    rather than assumed, so a corpus restored from a backup resumes from where it actually is.
    """
    row = conn.execute(
        "SELECT max(observed_at) FROM events WHERE channel=?", (channel,)
    ).fetchone()
    return row[0] if row and row[0] else None


def telegram_watermarks(conn: sqlite3.Connection) -> dict[str, int]:
    """Per-chat highest Telegram message id, as `{chat_key: message_id}`.

    Telegram message ids are per-chat and monotonic, so this is an exact resume point — better
    than a timestamp, which has to guess an overlap window to be safe. `external_id` is
    `<chat_id>:<message_id>` and chat ids contain no colon, so the split is unambiguous.
    """
    rows = conn.execute(
        """SELECT chat_key,
                  max(CAST(substr(external_id, instr(external_id, ':') + 1) AS INTEGER))
             FROM events
            WHERE channel='telegram' AND instr(external_id, ':') > 0
            GROUP BY chat_key"""
    ).fetchall()
    return {chat_key: high for chat_key, high in rows if high is not None}


def dispositions(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT chat_key, disposition FROM channels").fetchall())


_PENDING_FROM = re.compile(r"pending_from=([0-9T:+\-]+)")


def pending_floors(conn: sqlite3.Connection) -> dict[str, str]:
    """Per-chat resume points left behind by a pull that declined to store an unruled dialog.

    Without this, admitting a channel a week after it was first seen would silently lose that
    week: the chat has no watermark, so it falls back to the date floor — and the date floor is
    the cursor, which has moved on past exactly the messages the operator just said he wanted.
    """
    rows = conn.execute(
        "SELECT chat_key, notes FROM channels WHERE notes LIKE 'pending_from=%'"
    ).fetchall()
    out = {}
    for chat_key, notes in rows:
        match = _PENDING_FROM.search(notes or "")
        if match:
            out[chat_key] = match.group(1)
    return out


@dataclass
class PendingChannel:
    """One conversation registered-but-not-stored, with enough context to be ruled on.

    Everything here except `preview` is metadata. `preview` is the redacted newest message,
    capped — see `PENDING_MIGRATION` for the ruling that put it there and the lifecycle that
    takes it away again.
    """

    channel: str
    display: str | None
    count: int
    username: str | None = None
    kind: str | None = None
    last_at: str | None = None
    preview: str | None = None
    last_out: bool | None = None

    def label(self) -> str:
        """The most human-readable identity available, in falling order of usefulness.

        Never `(no name)` when a username exists, which is the whole point: `9000000001` and
        `@somehandle` are the same account, and only one of them can be recognised.
        """
        if self.display and self.username:
            return f"{self.display} (@{self.username})"
        if self.display:
            return self.display
        if self.username:
            return f"@{self.username}"
        return "(no name)"


# --------------------------------------------------------------------------- writer


class Writer:
    """The only way a row enters the corpus. Redacts, then inserts, in one statement.

    Counters are kept per run so a caller can report what happened without re-reading the store
    — and so that everything a pull prints comes from this side of the write rather than from a
    second query that might see something else.
    """

    def __init__(self, conn: sqlite3.Connection, *, commit: bool) -> None:
        from neo_sensor.redaction import redact  # deferred: ~2s spaCy load

        self._conn = conn
        self._redact = redact
        self._commit = commit
        self.written = 0
        self.duplicates = 0
        self.new_channels: dict[str, tuple[str, str | None]] = {}
        self.pending: dict[str, PendingChannel] = {}
        self.entities: dict[str, int] = {}
        self.suppressed: dict[str, int] = {}
        self.samples: list[tuple[str, str]] = []

    def preview_of(self, text: str | None, limit: int = 200) -> str | None:
        """The redacted, whitespace-collapsed, truncated head of one message.

        Runs the same filter every stored body runs — there is no second, laxer path — and then
        caps the length, because the job is recognition and not reading. Returns None for an
        empty body so a `[photo]`-only chat says nothing rather than saying `""`.
        """
        if not text or not text.strip():
            return None
        redacted = self._redact(text).text
        collapsed = " ".join(redacted.split())
        return collapsed[: limit - 1] + "…" if len(collapsed) > limit else collapsed

    def register_pending(
        self,
        chat_key: str,
        channel: str,
        display: str | None,
        count: int,
        since: str,
        *,
        username: str | None = None,
        kind: str | None = None,
        last_at: str | None = None,
        last_body: str | None = None,
        last_out: bool | None = None,
    ) -> None:
        """Record that a conversation exists and is awaiting a ruling — WITHOUT storing it.

        The difference between this and `note_channel` is the whole of the consent argument.
        A dialog the operator has never ruled on is not `work` traffic that happens to be
        unlabelled; it is a conversation he has not decided to hand over. Telegram carries his
        personal life, and the first live pull found 15 unruled dialogs with traffic in the
        window — a thread with his wife, one about her visa, several bots. Storing those and
        relying on `undecided` to withhold them would be true and would still be the wrong
        shape: the fence decides what an agent may READ, and it was never meant to be the thing
        standing between his family chat and a database.

        So the row records the name, the volume and the window, which is exactly what
        `channels-pending` is allowed to show him, and nothing else. If he rules it `work`, the
        next pull fetches the messages — `pending_from` is kept so that pull starts where this
        one declined to, rather than from a cursor that has since moved past it.

        `last_out` says which way the previewed message was going, and it is not optional
        garnish: without it the preview attributes the operator's own words to the account he is
        being asked to identify. See `PENDING_DIRECTION_MIGRATION`. It is `bool | None` rather
        than `bool` because a caller that genuinely cannot tell must be able to say so — `None`
        renders as an unattributed quote, which is honest, and `False` renders as "they said",
        which would be a claim.
        """
        preview = self.preview_of(last_body)
        self._conn.execute(
            """INSERT INTO channels (chat_key, channel, display_name, disposition,
                                     seed_provenance, notes, pending_username, pending_kind,
                                     pending_last_at, pending_preview, pending_last_out)
                    VALUES (?, ?, ?, 'undecided', 'live-ingest', ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_key) DO UPDATE SET
                    display_name    = COALESCE(excluded.display_name, channels.display_name),
                    notes           = excluded.notes,
                    pending_username = COALESCE(excluded.pending_username,
                                                channels.pending_username),
                    pending_kind     = COALESCE(excluded.pending_kind, channels.pending_kind),
                    pending_last_at  = COALESCE(excluded.pending_last_at,
                                                channels.pending_last_at),
                    pending_preview  = COALESCE(excluded.pending_preview,
                                                channels.pending_preview),
                    pending_last_out = COALESCE(excluded.pending_last_out,
                                                channels.pending_last_out)""",
            (
                chat_key,
                channel,
                display,
                f"pending_from={since}; {count} message(s) not stored",
                username,
                kind,
                corpus_stamp(last_at) if last_at else None,
                preview,
                None if last_out is None else int(last_out),
            ),
        )
        self.pending[chat_key] = PendingChannel(
            channel=channel,
            display=display,
            count=count,
            username=username,
            kind=kind,
            last_at=corpus_stamp(last_at) if last_at else None,
            preview=preview,
            last_out=last_out,
        )

    def note_channel(self, chat_key: str, channel: str, display: str | None) -> bool:
        """Create a channel row if this chat has never been seen. Returns True if it is new.

        `undecided` is the only disposition this may ever write, and an existing row's
        disposition is never touched. Admission is the operator's act; the agent proposes and he
        runs it.

        **The name is refreshed to the live one on every pull, and for eight months it was not.**
        The `UPDATE` said `display_name=COALESCE(display_name, ?)`, which fills a NULL and never
        overwrites — so a name was written once, at registration, and no later pull could change
        it however many times the chat was renamed in Telegram. The docstring here claimed the
        opposite (*"the `ON CONFLICT` clause updates the display name"* — there is no `ON
        CONFLICT` in this method either), which is how it survived; and the four `pending_*`
        columns in `register_pending` had the arguments the right way round in the same
        statement, which is what says this was a slip rather than a decision.

        Found 2026-09-07: a pull listed a dialog as `Partner Members` while the stored name was
        `Partner Assembly 2026 (UK)`, so `neo-corpus thread 'Partner Members'` answered *"no such chat, or it
        is not admitted"* — both truthful, since the pull prints Telegram's live titles and
        `thread` matches the stored ones. One divergence in 30 dialogs, and the expensive
        consequence was `neo-send`, which compares the live title against this column and refuses
        on a mismatch: that group had become permanently unsendable.

        Dispositions are keyed on `chat_key`, never on the name, so refreshing it orphans
        nothing. `COALESCE(?, display_name)` keeps the original NULL-safety — a pull that reports
        no title cannot erase a title the corpus already has.
        """
        row = self._conn.execute(
            "SELECT disposition FROM channels WHERE chat_key=?", (chat_key,)
        ).fetchone()
        if row is not None:
            if self._commit:
                # Clearing the pending note is not tidying — it is what stops the resume point
                # firing again. This runs immediately before the chat's first stored message,
                # so by the time the note goes the backlog it was holding is being written.
                # Left in place, a Graph pull would rewind to that date on every future run,
                # because Graph has no per-chat watermark to overtake it.
                # The four pending-context columns go with the note, and for the same reason:
                # they exist to answer "who is this", and the moment the chat is being stored
                # that question is answered by the messages themselves. Leaving the preview
                # behind would keep one redacted body in a column nothing reads, forever.
                self._conn.execute(
                    "UPDATE channels SET display_name=COALESCE(?, display_name), "
                    "notes=CASE WHEN notes LIKE 'pending_from=%' THEN NULL ELSE notes END, "
                    "pending_username=NULL, pending_kind=NULL, pending_last_at=NULL, "
                    "pending_preview=NULL, pending_last_out=NULL "
                    "WHERE chat_key=?",
                    (display, chat_key),
                )
            return False
        if self._commit:
            self._conn.execute(
                """INSERT INTO channels (chat_key, channel, display_name, disposition,
                                         seed_provenance)
                        VALUES (?, ?, ?, 'undecided', 'live-ingest')""",
                (chat_key, channel, display),
            )
        self.new_channels[chat_key] = (channel, display)
        return True

    def write(self, event: IncomingEvent) -> bool:
        """Redact and insert one event. Returns True if it was new.

        The body is redacted here and only here. `INSERT OR IGNORE` against the
        `(channel, external_id)` unique index makes a re-pull a no-op rather than a duplicate,
        which is what lets a failed run simply be run again.
        """
        # Classified on the way in, for the same reason redaction is: the alternative is a
        # corpus that treats the same artefact as noise before a date and as signal after it.
        # Relevance only — `suppressed` is not part of `EMITTABLE` and never gates safety.
        reason = classify(event.body)
        if reason:
            self.suppressed[reason] = self.suppressed.get(reason, 0) + 1

        # Spoilers are masked BEFORE Presidio runs, and the order is load-bearing. The spans
        # are offsets into the raw body; `redact()` rewrites the text, so applying them after it
        # would slice a string the offsets no longer describe. Masking first also means Presidio
        # never sees the hidden text, which is the point.
        body = event.body
        if body and event.spoiler_spans:
            from neo_sensor.redaction import mask_spoilers

            body = mask_spoilers(body, event.spoiler_spans)

        result = self._redact(body) if body else None
        body_redacted = result.text if result else ""
        subject_redacted = self._redact(event.subject).text if event.subject else None
        if result:
            metadata = list(result.redaction_metadata)
            if event.spoiler_spans:
                from neo_sensor.redaction import spoiler_items

                metadata.extend(spoiler_items(body_redacted))
                result = result.model_copy(update={"redaction_metadata": metadata})
            for item in metadata:
                self.entities[item.entity_type] = self.entities.get(item.entity_type, 0) + 1

        import json

        meta = json.dumps([m.model_dump() for m in result.redaction_metadata]) if result else "[]"

        if not self._commit:
            already = self._conn.execute(
                "SELECT 1 FROM events WHERE channel=? AND external_id=?",
                (event.channel, event.external_id),
            ).fetchone()
            if already:
                self.duplicates += 1
                return False
            self.written += 1
            self._sample(subject_redacted, body_redacted)
            return True

        cur = self._conn.execute(
            """INSERT OR IGNORE INTO events
                 (event_id, channel, external_id, chat_key, observed_at, sender, direction,
                  direction_source, body, body_redacted, redaction_meta, redacted_at,
                  chat_display, forwarded_from, subject, subject_redacted, recipients,
                  conversation_id, body_source, suppressed, suppressed_reason, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (
                event.event_id,
                event.channel,
                event.external_id,
                event.chat_key,
                event.observed_at,
                event.sender,
                event.direction,
                event.direction_source,
                event.body,
                body_redacted,
                meta,
                event.chat_display,
                event.forwarded_from,
                event.subject,
                subject_redacted,
                json.dumps(event.recipients, ensure_ascii=False) if event.recipients else None,
                event.conversation_id,
                event.body_source,
                1 if reason else 0,
                reason,
            ),
        )
        if cur.rowcount == 0:
            self.duplicates += 1
            return False
        self.written += 1
        self._sample(subject_redacted, body_redacted)
        return True

    def _sample(self, subject: str | None, body: str) -> None:
        """Keep a few REDACTED excerpts so a run can be eyeballed without reading the mailbox."""
        if len(self.samples) < 8:
            self.samples.append((subject or "", body[:160]))

    def flush(self) -> None:
        if self._commit:
            self._conn.commit()


def reclassify(conn: sqlite3.Connection, *, commit: bool) -> int:
    """Apply the relevance rules to rows written before ingest classified them.

    Two halves, and the read-only half is the interesting one.

    **Rows this ingest wrote** are updated: the first live pull ran before the rules were
    ported, so 99 rows landed unsuppressed that the same corpus would have suppressed a day
    earlier.

    **Historical rows are read, compared, and left alone.** If the port is faithful, it agrees
    with the predecessor's classification on all 86,182 of them; any disagreement means the
    rules are not the rules the corpus was built with, and that is worth discovering here rather
    than by noticing months later that a chat looks different either side of a date. Reporting a
    disagreement is right; silently rewriting an operator-set judgement on historical data is
    not.
    """
    rows = conn.execute(
        "SELECT rowid, body, suppressed, suppressed_reason, ingested_at FROM events"
    ).fetchall()

    updates: list[tuple[int, str | None]] = []
    disagreements: dict[str, int] = {}
    for rowid, body, suppressed, reason, _ingested in rows:
        want = classify(body or "")
        have = reason if suppressed else None
        if want == have:
            continue
        # A row whose stored classification is NULL/0 and which the rules now catch is one this
        # ingest wrote. A row the predecessor classified differently is a genuine disagreement.
        if have is None and suppressed == 0 and reason is None:
            updates.append((rowid, want))
        else:
            key = f"{have or 'signal'} -> {want or 'signal'}"
            disagreements[key] = disagreements.get(key, 0) + 1

    print(f"{len(updates):,} row(s) to classify" + ("" if commit else "  [DRY RUN]"))
    counts: dict[str, int] = {}
    for _rowid, want in updates:
        counts[want or "signal"] = counts.get(want or "signal", 0) + 1
    for reason, n in sorted(counts.items()):
        print(f"  {reason:<14} {n:>5}")

    if disagreements:
        print("\nDISAGREEMENTS with the inherited classification — NOT changed:")
        for change, n in sorted(disagreements.items()):
            print(f"  {change:<28} {n:>5}")
        print("  the ported rules are not the rules this corpus was built with. Investigate")
        print("  before trusting either — a rule that nearly matches is worse than one that")
        print("  obviously does not.")
    else:
        print("\nno disagreements: the ported rules reproduce the inherited classification")
        print("on every historical row.")

    if commit and updates:
        conn.executemany(
            "UPDATE events SET suppressed=?, suppressed_reason=? WHERE rowid=?",
            [(1 if want else 0, want, rowid) for rowid, want in updates],
        )
        conn.commit()
        print(f"\nclassified {len(updates):,} row(s)")
    elif not commit:
        print("\nDRY RUN — nothing written. Re-run with --commit.")
    return 0


def withdraw_pending(
    conn: sqlite3.Connection, *, commit: bool
) -> list[tuple[str, str | None, int, str]]:
    """Restore the invariant: **a channel he has never ruled on has no stored messages.**

    Written 2026-08-24 as the repair for the defect described in `telegram_fetch.pull` — the
    never-ruled test read `disposition is None`, so the second pull stored what the first one
    held back. Fifteen rows from four never-ruled conversations, one a family chat, ended up in
    the store. `undecided` still withheld them from being read, but withheld-in-the-database is
    precisely the shape `register_pending` exists to refuse.

    A verb rather than a one-off script on purpose. Ad-hoc `sqlite3` against the fenced store is
    denied to the agent, and rightly — the repair for a write-path defect belongs in the write
    path, where it is reviewable, tested, and available the next time rather than retyped from
    memory under pressure.

    Two things it does, per affected channel:

    - **Deletes the stored events.** Not `suppressed`, not re-dispositioned. They should never
      have been written and there is no reading of the design under which they stay.
    - **Restores a `pending_from` floor**, one second before the earliest deleted message.
      Telegram's `offset_date` with `reverse=True` is exclusive, so a floor set exactly at that
      message's timestamp would drop the message it is meant to recover — the whole point is
      that the next pull re-registers the chat with the same volume it should have had.

    Returns `[(chat_key, display_name, rows, floor)]`. Dry by default; nothing is written
    without `commit`.
    """
    rows = conn.execute(
        """SELECT c.chat_key, c.display_name, count(e.rowid), min(e.observed_at)
             FROM channels c JOIN events e ON e.chat_key = c.chat_key
            WHERE c.disposition = 'undecided'
            GROUP BY c.chat_key ORDER BY count(e.rowid) DESC"""
    ).fetchall()
    out: list[tuple[str, str | None, int, str]] = []
    for chat_key, display, n, earliest in rows:
        floor = corpus_stamp(as_utc(earliest) - timedelta(seconds=1))
        out.append((chat_key, display, n, floor))
        if not commit:
            continue
        conn.execute("DELETE FROM events WHERE chat_key=?", (chat_key,))
        conn.execute(
            """UPDATE channels
                  SET notes=?, pending_username=NULL, pending_kind=NULL,
                      pending_last_at=NULL, pending_preview=NULL
                WHERE chat_key=?""",
            (f"pending_from={floor}; {n} message(s) not stored", chat_key),
        )
    if commit and out:
        conn.commit()
    return out


def rebuild_index(conn: sqlite3.Connection) -> None:
    """Re-point FTS5 at `body_redacted` after an ingest.

    Not optional and not deferrable. Since 2026-08-15 `search` routes CJK queries through a
    substring scan that needs no index at all, so a forgotten rebuild leaves English search
    stale while Chinese search stays current — a divergence whose symptom is that nothing looks
    wrong. Rebuilding is seconds; the ambiguity it prevents is permanent.
    """
    from neo_sensor.redact_corpus import rebuild_fts_over_redacted

    rebuild_fts_over_redacted(conn)


def report(writer: Writer, *, commit: bool) -> None:
    print(f"\nwritten {writer.written:,} · already present {writer.duplicates:,}")
    if writer.entities:
        print(
            "redaction removed: "
            + ", ".join(f"{k} x{v}" for k, v in sorted(writer.entities.items()))
        )
    else:
        print("redaction removed: nothing")
    if writer.pending:
        total = sum(p.count for p in writer.pending.values())
        print(
            f"\n{len(writer.pending)} conversation(s) you have never ruled on, "
            f"holding {total:,} message(s) — REGISTERED, NOT STORED:"
        )
        # One block per channel rather than one line, because a line cannot carry the newest
        # message and the newest message is what makes an unnamed account recognisable. The
        # operator, 2026-08-24, on being handed `9000000001` with nothing else: *"Don't give me
        # a bare ID and ask me to decide. Give me context!!!"*
        for chat_key, pend in sorted(writer.pending.items(), key=lambda kv: -kv[1].count):
            kind = f" · {pend.kind}" if pend.kind else ""
            print(f"\n  {pend.label()}")
            print(f"    {pend.channel} · {chat_key} · {pend.count} message(s){kind}")
            if pend.last_at:
                print(f"    newest {pend.last_at} (UTC)")
            if pend.preview:
                print(f"    > {pend.preview}")
        print("\n  rule on them with:  neo-channels set <chat_key> work|personal|ignore")
        print("  then re-run the pull — it resumes each from where this run stopped.")
    if writer.new_channels:
        print(f"\n{len(writer.new_channels)} NEW channel(s) — stored as `undecided`, withheld:")
        for chat_key, (channel, display) in writer.new_channels.items():
            print(f"  {channel:<9} {chat_key:<20} {display or '(no name)'}")
        print("  decide with:  neo-channels set <chat_key> work|personal|ignore")
    if writer.samples:
        print("\nsample (REDACTED — post-filter text, not raw message bodies):")
        for subject, body in writer.samples[:4]:
            if subject:
                print(f"  subject: {subject}")
            print(f"     body: {body}\n")
    if not commit:
        print("DRY RUN — nothing written. Re-run with --commit.")
