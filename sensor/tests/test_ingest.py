"""The write path, asserted against a fixture corpus built from the real migration schema.

`test_fence.py` runs against the live store because its lesson is that isolated tests passed
while the real payload leaked. These cannot: the thing under test is *writing*, and the live
store is the one artefact in this project that must not be written to by a test run. So the
fixture is built from `neo_sensor.schema.SCHEMA` itself rather
than a hand-copied approximation — if the shipped schema and the schema these tests assume ever
diverge, they diverge here and the tests fail, instead of diverging in production.

What is pinned here is the set of properties that make ingest safe to run unattended:
redaction cannot be skipped, admission cannot be granted, and a re-run cannot duplicate.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from neo_sensor import telegram_fetch
from neo_sensor.graph_fetch import OPERATOR_EMAILS, sender_of
from neo_sensor.ingest import (
    IncomingEvent,
    Writer,
    corpus_high_water,
    ensure_schema,
    get_cursor,
    set_cursor,
    telegram_watermarks,
)


def _shipped_schema() -> str:
    """The corpus schema as the package actually writes it — imported, not retyped."""
    from neo_sensor.schema import SCHEMA

    return SCHEMA


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "fixture.db")
    c.executescript(_shipped_schema())
    c.commit()
    ensure_schema(c)
    yield c
    c.close()


def event(**over) -> IncomingEvent:
    base = {
        "channel": "telegram",
        "external_id": "-100123:5001",
        "chat_key": "-100123",
        "observed_at": "2026-08-15T09:00:00+00:00",
        "sender": "200000001",
        "direction": "outbound",
        "direction_source": "author",
        "body": "ordinary text",
        "chat_display": "A Chat",
    }
    return IncomingEvent(**{**base, **over})


def admit(conn: sqlite3.Connection, chat_key: str, disposition: str = "work") -> None:
    conn.execute(
        "INSERT INTO channels (chat_key, channel, disposition) VALUES (?, 'telegram', ?)",
        (chat_key, disposition),
    )
    conn.commit()


# --------------------------------------------------------------- redaction is structural


def test_a_body_cannot_enter_the_corpus_unredacted(conn: sqlite3.Connection) -> None:
    """The credential is gone from the readable column and present in the fenced one.

    This is the property the predecessor believed it had. There, redaction was a boolean the
    caller set and every call site hardcoded True; here the caller cannot supply
    `body_redacted` at all, so the only way a row exists is if `Writer.write` produced it.
    """
    writer = Writer(conn, commit=True)
    writer.write(event(body="the bitly login is neo@example.com/hunter2brown"))
    writer.flush()

    raw, redacted, stamped = conn.execute(
        "SELECT body, body_redacted, redacted_at FROM events"
    ).fetchone()

    assert "hunter2brown" in raw, "the raw column must keep what was observed"
    assert "hunter2brown" not in redacted
    assert "CREDENTIAL_PAIR" in redacted
    assert stamped is not None, "an unstamped row is not emittable and would vanish silently"


def test_every_written_row_is_stamped_redacted(conn: sqlite3.Connection) -> None:
    """`EMITTABLE` requires `redacted_at IS NOT NULL`, so an unstamped row is invisible.

    A pull that wrote rows the read path then withheld would look like a successful ingest and
    an empty corpus — the failure whose whole signature is that nothing looks wrong.
    """
    writer = Writer(conn, commit=True)
    for i in range(5):
        writer.write(event(external_id=f"-100123:{i}", body="" if i == 3 else f"message {i}"))
    writer.flush()

    unstamped = conn.execute("SELECT count(*) FROM events WHERE redacted_at IS NULL").fetchone()[0]
    assert unstamped == 0, "an empty body still needs a stamp — it is a row like any other"


# --------------------------------------------------------------- admission is not ingest's


def test_a_new_chat_arrives_undecided_and_therefore_withheld(conn: sqlite3.Connection) -> None:
    writer = Writer(conn, commit=True)
    assert writer.note_channel("-100999", "telegram", "Some New Group") is True
    writer.write(event(chat_key="-100999", external_id="-100999:1"))
    writer.flush()

    disposition, name = conn.execute(
        "SELECT disposition, display_name FROM channels WHERE chat_key='-100999'"
    ).fetchone()
    assert disposition == "undecided"
    assert name == "Some New Group"


def test_ingest_never_changes_a_disposition_the_operator_already_set(
    conn: sqlite3.Connection,
) -> None:
    """`neo-channels` is denied to the agent outright. A write path that could quietly widen
    what the agent may read would route around that denial without anyone typing a command."""
    admit(conn, "-100123", "personal")
    writer = Writer(conn, commit=True)

    assert writer.note_channel("-100123", "telegram", "Renamed") is False
    writer.flush()

    assert conn.execute(
        "SELECT disposition FROM channels WHERE chat_key='-100123'"
    ).fetchone()[0] == "personal"


def test_no_write_path_can_produce_an_admitted_channel() -> None:
    """A source-level check, because the guarantee is about queries nobody has written yet.

    `undecided` must be the only disposition literal any ingest module can insert. A behaviour
    test cannot prove the absence of a future call that admits a channel; reading the source
    can.
    """
    import re

    from neo_sensor import graph_fetch, ingest

    for module in (ingest, telegram_fetch, graph_fetch):
        source = Path(module.__file__).read_text()
        for statement in re.findall(r"INSERT INTO channels.*?\"\"\"", source, re.S):
            for disposition in ("'work'", "'mixed'", "'personal'", "'ignore'"):
                assert disposition not in statement, f"{module.__name__} can admit a channel"


def test_dialogs_the_operator_ruled_out_are_not_pulled_at_all() -> None:
    assert telegram_fetch.should_pull("work") is True
    assert telegram_fetch.should_pull("mixed") is True
    assert telegram_fetch.should_pull("undecided") is True
    # A new dialog has no row yet. If it were skipped he would never learn it exists.
    assert telegram_fetch.should_pull(None) is True
    assert telegram_fetch.should_pull("personal") is False
    assert telegram_fetch.should_pull("ignore") is False


def test_an_unruled_conversation_is_registered_without_being_stored(
    conn: sqlite3.Connection,
) -> None:
    """The consent rule, and the difference between it and the fence.

    `undecided` withholds a conversation from the agent. It was never meant to be the thing
    standing between the operator's family chat and a database — and the first live Telegram
    pull found 15 never-ruled dialogs carrying traffic, among them a thread with his wife and
    one about her visa. So an unruled conversation is counted and named, and its messages stay
    where they are until he rules.
    """
    writer = Writer(conn, commit=True)
    writer.register_pending("-100777", "telegram", "Family", 27, "2026-07-17T14:42:55+00")
    writer.flush()
    conn.commit()

    disposition, name, notes = conn.execute(
        "SELECT disposition, display_name, notes FROM channels WHERE chat_key='-100777'"
    ).fetchone()
    assert disposition == "undecided"
    assert name == "Family", "he needs the name to rule on it — that much `channels-pending` shows"
    assert "27 message(s) not stored" in notes
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_admitting_a_held_conversation_resumes_where_the_pull_declined(
    conn: sqlite3.Connection,
) -> None:
    """Otherwise ruling on a chat a week later silently loses that week.

    The chat has no watermark, so it falls back to the date floor — and the date floor is the
    cursor, which by then has moved past exactly the messages he just asked for. The per-chat
    `pending_from` is what makes the ruling retroactive rather than forward-only.
    """
    from neo_sensor.ingest import pending_floors

    writer = Writer(conn, commit=True)
    writer.register_pending("-100777", "telegram", "A Group", 27, "2026-07-17T14:42:55+00")
    writer.flush()
    conn.commit()

    assert pending_floors(conn) == {"-100777": "2026-07-17T14:42:55+00"}

    # And the resume point is consumed, not left lying around. Graph has no per-chat watermark
    # to overtake a stale one, so a note that survives its own backlog makes every future pull
    # rewind to the same date for ever.
    writer.note_channel("-100777", "telegram", "A Group")
    writer.write(event(chat_key="-100777", external_id="-100777:1"))
    writer.flush()
    conn.commit()

    assert pending_floors(conn) == {}


# --------------------------------------------------------------- catch-up


def test_a_second_pull_over_the_same_messages_writes_nothing(conn: sqlite3.Connection) -> None:
    """Idempotency is what makes the cursor advisory instead of load-bearing.

    If a re-pull duplicated, a run that died halfway would be unrepairable without knowing
    exactly where it stopped. With the dedup index doing the work, the answer to a failed pull
    is to run it again.
    """
    first = Writer(conn, commit=True)
    for i in range(10):
        first.write(event(external_id=f"-100123:{i}"))
    first.flush()

    second = Writer(conn, commit=True)
    for i in range(10):
        second.write(event(external_id=f"-100123:{i}"))
    second.flush()

    assert first.written == 10
    assert second.written == 0
    assert second.duplicates == 10
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 10


def test_the_watermark_is_per_chat_and_survives_a_negative_chat_id(
    conn: sqlite3.Connection,
) -> None:
    """Supergroup ids are negative and `external_id` is `<chat_id>:<message_id>`, so a split on
    the LAST colon or a naive int cast would silently produce the wrong resume point — and a
    wrong-but-plausible watermark skips messages rather than erroring."""
    writer = Writer(conn, commit=True)
    for chat, ids in {"-1001234567": [10, 400, 37], "200000001": [5, 9]}.items():
        for i in ids:
            writer.write(event(chat_key=chat, external_id=f"{chat}:{i}"))
    writer.flush()

    assert telegram_watermarks(conn) == {"-1001234567": 400, "200000001": 9}


def test_a_known_chat_resumes_by_id_and_a_new_one_by_date() -> None:
    floor = datetime(2026, 7, 17, tzinfo=UTC)

    known = telegram_fetch.resume_kwargs(is_new=False, watermark=400, floor_dt=floor)
    assert known == {"reverse": True, "min_id": 400}, "an exact mark beats a date"

    fresh = telegram_fetch.resume_kwargs(is_new=True, watermark=None, floor_dt=floor)
    assert fresh == {"reverse": True, "offset_date": floor}, "an unbounded new dialog is history"

    unbounded = telegram_fetch.resume_kwargs(is_new=True, watermark=None, floor_dt=None)
    assert unbounded == {"reverse": True}


def test_the_cursor_falls_back_to_what_the_corpus_already_holds(
    conn: sqlite3.Connection,
) -> None:
    """The first pull runs against a corpus of 86,182 rows and an empty `sync_state`. Deriving
    the resume point from the data means that first run does not need a value nobody set."""
    writer = Writer(conn, commit=True)
    writer.write(event(observed_at="2026-07-17T12:00:00+00:00"))
    writer.write(event(external_id="-100123:2", observed_at="2026-07-16T12:00:00+00:00"))
    writer.flush()

    assert get_cursor(conn, "telegram") is None
    assert corpus_high_water(conn, "telegram") == "2026-07-17T12:00:00+00"

    set_cursor(conn, "telegram", "2026-08-15T09:00:00+00:00", "pull")
    conn.commit()
    assert get_cursor(conn, "telegram") == "2026-08-15T09:00:00+00:00"


# --------------------------------------------------------------- what the row records


def test_a_forwarded_message_records_who_wrote_it() -> None:
    """The reason `forwarded_from` exists: without it a relayed message is recorded as his."""
    named = SimpleNamespace(forward=SimpleNamespace(from_name="Pat Forwarder", sender_id=None))
    assert telegram_fetch.forwarded_from(named) == "Pat Forwarder"

    by_id = SimpleNamespace(forward=SimpleNamespace(from_name=None, sender_id=200000002))
    assert telegram_fetch.forwarded_from(by_id) == "id:200000002"

    hidden = SimpleNamespace(
        forward=SimpleNamespace(from_name=None, sender_id=None, chat_id=None, channel_id=None)
    )
    assert telegram_fetch.forwarded_from(hidden) == "(hidden)", "a forward must never read as None"

    assert telegram_fetch.forwarded_from(SimpleNamespace(forward=None)) is None


def _message(**over):  # noqa: ANN202
    base = dict(
        id=7, chat_id=-100123, date=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
        sender_id=200000001, message="hello", out=True, media=None, forward=None,
    )
    return SimpleNamespace(**{**base, **over})


def test_direction_is_decided_by_the_author_not_by_a_flag() -> None:
    mine = telegram_fetch.to_event(_message(), "-100123", "A Chat")
    assert (mine.direction, mine.direction_source) == ("outbound", "author")

    theirs = telegram_fetch.to_event(_message(sender_id=200000002, out=True), "-100123", "A Chat")
    assert theirs.direction == "inbound", "a stale `out` flag must not make it his writing"

    anonymous = telegram_fetch.to_event(_message(sender_id=None, out=False), "-100123", None)
    assert (anonymous.direction, anonymous.direction_source) == ("inbound", "out-flag")


def test_outlook_direction_is_author_derived_too() -> None:
    assert sender_of({"from": {"emailAddress": {"address": "Operator@Example.com"}}}) in (
        OPERATOR_EMAILS
    ), "case from Graph must not decide whether a mail is his"
    assert sender_of({}) == ""


def test_a_media_only_message_is_not_a_blank_row() -> None:
    photo = _message(message=None, media=object(), photo=True, file=None)
    event_row = telegram_fetch.to_event(photo, "-100123", "A Chat")
    assert event_row.body == "[photo]"

    doc = _message(
        message=None, media=object(), photo=False, voice=False, video_note=False, video=False,
        gif=False, audio=False, sticker=False, document=True,
        file=SimpleNamespace(name="budget.xlsx"),
    )
    assert telegram_fetch.to_event(doc, "-100123", None).body == "[document: budget.xlsx]"


def test_external_id_keeps_the_shape_chat_key_is_derived_from() -> None:
    """`chat_key` is `external_id` split on the first colon, and 108 channel dispositions are
    keyed on the result. A new external_id shape would orphan every one of them."""
    row = telegram_fetch.to_event(_message(chat_id=-1001234567, id=98765), "-1001234567", None)
    assert row.external_id == "-1001234567:98765"
    assert row.external_id.split(":", 1)[0] == row.chat_key


def test_every_timestamp_shape_this_project_meets_parses() -> None:
    """Four shapes arrive: Telethon's `+00:00`, Graph's `Z`, the corpus's `+00`, a bare date.

    The corpus's own `+00` is the one that broke the first live dry run, and it broke it at the
    API rather than at the parse — a 400 naming a URL, for a date-format mistake.
    """
    from neo_sensor.ingest import as_utc, corpus_stamp, graph_stamp

    instant = "2026-07-17T13:29:50"
    for text in (f"{instant}+00", f"{instant}+00:00", f"{instant}Z"):
        assert as_utc(text) == datetime(2026, 7, 17, 13, 29, 50, tzinfo=UTC)
    assert as_utc("2026-07-17") == datetime(2026, 7, 17, tzinfo=UTC)

    assert corpus_stamp(f"{instant}Z") == f"{instant}+00", "one shape in the column"
    assert graph_stamp(f"{instant}+00") == f"{instant}Z", "a literal + is a space in a query"


def test_a_written_row_matches_the_timestamp_shape_the_column_already_holds() -> None:
    """A second rendering of the same instant would sort and window correctly by luck."""
    assert event(observed_at="2026-08-15T09:00:00Z").observed_at == "2026-08-15T09:00:00+00"
    assert event(observed_at=datetime(2026, 8, 15, 9, tzinfo=UTC)).observed_at == (
        "2026-08-15T09:00:00+00"
    )


def test_event_id_is_a_function_of_the_message_not_of_the_run() -> None:
    """Deterministic ids make a re-pull produce identical rows, so a diff answers 'did this run
    write what I think it wrote' — which a random uuid per run would make unanswerable."""
    assert event().event_id == event().event_id
    assert event().event_id != event(external_id="-100123:5002").event_id


# --------------------------------------------------------------- relevance


def test_noise_is_classified_on_the_way_in(conn: sqlite3.Connection) -> None:
    """Otherwise the same artefact is noise before a date and signal after it.

    The first live pull ran before these rules were ported, and 99 rows landed unsuppressed
    that the same corpus would have suppressed a day earlier — including 42 copies of a daily
    report whose 572 older copies are hidden. A `timeline` across that boundary would have
    shown a step change with nothing announcing it.
    """
    writer = Writer(conn, commit=True)
    writer.write(event(external_id="-100123:1", body="KM Daily Report 8/14\n== Traffic ==\n..."))
    writer.write(event(external_id="-100123:2", body="[ORG Stats Aug 14]\nDashboard: ..."))
    writer.write(event(external_id="-100123:3", body="========"))
    writer.write(event(external_id="-100123:4", body=""))
    writer.write(event(external_id="-100123:5", body="07/20 - My weekly report\n== Last week =="))
    writer.write(event(external_id="-100123:6", body="here's the daily report I mentioned"))
    writer.flush()

    got = dict(
        conn.execute(
            "SELECT external_id, suppressed_reason FROM events ORDER BY external_id"
        ).fetchall()
    )
    assert got["-100123:1"] == "daily_report"
    assert got["-100123:2"] == "stats_report"
    assert got["-100123:3"] == "separator"
    assert got["-100123:4"] == "service_msg"
    assert got["-100123:5"] is None, "weekly reports are his own writing and are kept"
    assert got["-100123:6"] is None, "a human MENTIONING the report is not the report"


def test_suppression_still_is_not_the_fence(conn: sqlite3.Connection) -> None:
    """A suppressed row is hidden by default and reachable on request. It is not withheld.

    Conflating a relevance judgement with a safety boundary is how a fence stops meaning
    anything, so this checks the classification writes `suppressed` and touches nothing that
    `EMITTABLE` reads.
    """
    writer = Writer(conn, commit=True)
    writer.write(event(body="KM Daily Report 8/14\n== Traffic =="))
    writer.flush()

    suppressed, redacted_at, chat_key = conn.execute(
        "SELECT suppressed, redacted_at, chat_key FROM events"
    ).fetchone()
    assert suppressed == 1
    assert redacted_at is not None, "noise is still redacted — it is a row like any other"
    assert chat_key == "-100123", "and it still belongs to its channel"


# --------------------------------------------------------------- dry run


def test_a_dry_run_writes_nothing(conn: sqlite3.Connection) -> None:
    writer = Writer(conn, commit=False)
    writer.note_channel("-100999", "telegram", "New Group")
    writer.write(event(chat_key="-100999", external_id="-100999:1"))
    writer.flush()

    assert writer.written == 1, "a dry run still reports what it would have written"
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM channels").fetchone()[0] == 0


def test_a_dry_run_needs_neither_the_migration_nor_write_access(tmp_path: Path) -> None:
    """The first dry run on any machine meets a corpus that has never been migrated.

    It must survive that read-only and un-migrated, or the safe verb becomes the one you cannot
    run first — and the operator would have to let a schema change land before he could see
    what a pull was going to do.
    """
    db = tmp_path / "unmigrated.db"
    setup = sqlite3.connect(db)
    setup.executescript(_shipped_schema())
    setup.execute(
        """INSERT INTO events (event_id, channel, external_id, chat_key, observed_at,
                               sender, direction, body)
           VALUES ('e1','telegram','-100123:1','-100123','2026-07-17T10:00:00+00:00',
                   '200000001','outbound','hi')"""
    )
    setup.commit()
    setup.close()

    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert get_cursor(ro, "telegram") is None, "no sync_state table yet — that is not an error"
    assert corpus_high_water(ro, "telegram") == "2026-07-17T10:00:00+00:00"

    writer = Writer(ro, commit=False)
    writer.note_channel("-100999", "telegram", "New Group")
    writer.write(event(chat_key="-100999", external_id="-100999:1"))
    writer.flush()

    assert (writer.written, len(writer.new_channels)) == (1, 1)
    ro.close()


def test_nothing_a_run_prints_comes_from_the_raw_column(conn: sqlite3.Connection) -> None:
    """The samples a pull prints are taken from redacted text, so a run is safe to watch."""
    writer = Writer(conn, commit=True)
    writer.write(event(body="aws key AKIAIOSFODNN7EXAMPLE and more"))
    writer.flush()

    assert writer.samples
    assert all("AKIAIOSFODNN7EXAMPLE" not in body for _, body in writer.samples)


# ------------------------------------------------------- spoilers are captured, not recognised


# The REAL Telethon type, not a stand-in. The fetcher matches on `type(e).__name__` because
# every telethon import in that module is deferred (~import cost, and the module is imported by
# tests that never touch Telegram), so a name match is what it can afford. Using the genuine
# class here is what makes that safe: if Telethon ever renames it, these tests fail loudly
# instead of the fetcher silently capturing nothing.
from telethon.tl.types import MessageEntitySpoiler as _Spoiler  # noqa: E402


def _spoiler(offset: int, length: int) -> _Spoiler:
    return _Spoiler(offset=offset, length=length)


def test_a_telegram_spoiler_becomes_a_span_on_the_event() -> None:
    """The information was being dropped: `to_event` never looked at `message.entities`.

    That is why no recogniser could ever have caught a spoiler — nothing in the stored text
    marks it. Same shape as the Outlook subject/recipient loss that `enrich` had to re-fetch the
    mailbox to repair, and the reason this is a capture fix rather than a filter fix.
    """
    text = "🙏 the code is hunter2 ok"
    offset = len(text[: text.index("hunter2")].encode("utf-16-le")) // 2
    message = SimpleNamespace(
        chat_id=-100123,
        id=7,
        sender_id=999,
        date=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        message=text,
        entities=[_spoiler(offset, len("hunter2"))],
        out=False,
        media=None,
    )
    ev = telegram_fetch.to_event(message, "-100123", "Some Chat")
    assert ev.spoiler_spans, "the spoiler entity must survive into the event"
    start, end = ev.spoiler_spans[0]
    assert text[start:end] == "hunter2"


def test_a_message_with_no_entities_carries_no_spans() -> None:
    message = SimpleNamespace(
        chat_id=-100123,
        id=8,
        sender_id=999,
        date=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        message="nothing hidden here",
        entities=None,
        out=False,
        media=None,
    )
    assert telegram_fetch.to_event(message, "-100123", None).spoiler_spans == []


def test_a_spoiler_is_gone_from_the_readable_column_and_kept_in_the_fenced_one(
    conn: sqlite3.Connection,
) -> None:
    """Same invariant as a credential: `body` keeps what was observed, `body_redacted` does not.

    This is why the mask is applied in `Writer.write` and NOT in `to_event` — masking at the
    fetcher would overwrite the only copy and make his own redaction irreversible, which is the
    one property that has let every recogniser change here be re-run with `--redo`.
    """
    body = "the staging password is s3cr3t-longish ok"
    start = body.index("s3cr3t-longish")
    writer = Writer(conn, commit=True)
    writer.write(
        event(body=body, spoiler_spans=[(start, start + len("s3cr3t-longish"))])
    )
    writer.flush()

    raw, redacted = conn.execute("SELECT body, body_redacted FROM events").fetchone()
    assert "s3cr3t-longish" in raw, "the raw column must keep what was observed"
    assert "s3cr3t-longish" not in redacted
    assert "<SPOILER>" in redacted
    assert writer.entities.get("SPOILER") == 1, "a masked spoiler must be reported by the run"


def test_a_spoiler_span_is_ignored_when_the_body_is_a_media_placeholder() -> None:
    """An empty-text message gets `media_placeholder()`, and an offset into that means nothing."""
    message = SimpleNamespace(
        chat_id=-100123,
        id=9,
        sender_id=999,
        date=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        message="",
        entities=[_spoiler(0, 5)],
        out=False,
        media=None,
    )
    assert telegram_fetch.to_event(message, "-100123", None).spoiler_spans == []


# --------------------------------------------------------------------- pending context
#
# Added 2026-08-24, after a pending channel reached the operator as `9000000001` and nothing
# else. His words: *"Don't give me a bare ID and ask me to decide. Give me context!!! username,
# last message (time and content)!!! ... Think about how you can fix this forever!!!"*
#
# "Forever" is what these tests are for. The old failure was not a wrong value — it was a
# *missing* one that read as a fact: `dialog.name` is title-or-nothing, so an account with no
# title produced `(no name)` while its `username` sat unread on the entity. The class of defect
# is the same as the Outlook `subject` loss and the Telegram spoiler loss: the sensor truthfully
# reported what it had, and what it never captured was invisible.


def test_a_user_with_no_display_name_is_still_identified_by_username() -> None:
    """The exact 2026-08-24 case. `dialog.name` is empty; the account is not anonymous.

    `entity_identity` must reach past the display name, because the display name is the one
    field Telegram lets a user leave blank.
    """

    class User:  # Telethon's type name is the discriminator, so the stub carries it
        username = "somehandle"
        bot = False
        deleted = False

    display, username, kind = telegram_fetch.entity_identity(User(), None)
    assert display is None
    assert username == "somehandle", "the username was there all along and nothing looked at it"
    assert kind == "user"


def test_entity_kind_distinguishes_a_bot_from_a_person() -> None:
    """A bot 1:1 and a person 1:1 look identical in the old output and are different rulings."""

    class User:
        username = None
        bot = True
        deleted = False

    _display, _username, kind = telegram_fetch.entity_identity(User(), "Some Bot")
    assert kind == "bot"

    class Chat:
        username = None

    assert telegram_fetch.entity_identity(Chat(), "A Group")[2] == "group"


def test_label_never_says_no_name_when_a_handle_exists() -> None:
    """The operator-facing string. `(no name)` is only honest when there is genuinely nothing."""
    from neo_sensor.ingest import PendingChannel

    assert PendingChannel("telegram", None, 1, username="h").label() == "@h"
    assert PendingChannel("telegram", "Pat", 1, username="h").label() == "Pat (@h)"
    assert PendingChannel("telegram", "Pat", 1).label() == "Pat"
    assert PendingChannel("telegram", None, 1).label() == "(no name)"


def test_the_preview_is_redacted_and_capped(conn: sqlite3.Connection) -> None:
    """The preview runs the same filter every stored body runs. There is no second, laxer path.

    Asserted with a credential shape rather than plain prose, because the whole reason a body
    may be shown at all is that it cannot carry a secret out.
    """
    writer = Writer(conn, commit=True)
    out = writer.preview_of("password: hunter2SuperSecret!! and then some")
    assert out is not None
    assert "hunter2SuperSecret" not in out, "the preview must not be a hole in the fence"

    long = writer.preview_of("x" * 500)
    assert len(long) <= 200 and long.endswith("…")

    assert writer.preview_of("   ") is None, "an empty body says nothing, not ''"


def test_register_pending_stores_the_context_and_ruling_takes_it_away(
    conn: sqlite3.Connection,
) -> None:
    """Both halves matter.

    Storing it is what lets a later session — one that did not run the pull — put a real
    question in front of him. Clearing it on the ruling is what keeps `register_pending`'s
    original argument intact: the corpus does not become the place a never-ruled chat's text
    lives. A preview left behind after admission is one redacted body in a column nothing reads.
    """
    writer = Writer(conn, commit=True)
    writer.register_pending(
        "9000000001", "telegram", None, 1, "2026-08-21T18:08:36+00",
        username="somehandle", kind="user",
        last_at=datetime(2026, 8, 23, 11, 4, tzinfo=UTC),
        last_body="Hi, are we still on for Saturday?",
    )
    writer.flush()
    conn.commit()

    username, kind, last_at, preview = conn.execute(
        """SELECT pending_username, pending_kind, pending_last_at, pending_preview
             FROM channels WHERE chat_key='9000000001'"""
    ).fetchone()
    assert username == "somehandle"
    assert kind == "user"
    assert last_at == "2026-08-23T11:04:00+00", "one timestamp shape in the column, as everywhere"
    assert preview == "Hi, are we still on for Saturday?"

    writer.note_channel("9000000001", "telegram", "Somebody")
    conn.commit()
    assert conn.execute(
        """SELECT pending_username, pending_kind, pending_last_at, pending_preview
             FROM channels WHERE chat_key='9000000001'"""
    ).fetchone() == (None, None, None, None)


def test_no_registration_is_unanswerable(conn: sqlite3.Connection) -> None:
    """The invariant the whole change exists to hold.

    A conversation put in front of him for a decision must carry at least one thing a human can
    recognise: a name, a handle, or the newest message. All three absent is the state that got
    this written, and it is now a test failure rather than a message to him.
    """
    writer = Writer(conn, commit=True)
    writer.register_pending(
        "9000000001", "telegram", None, 1, "2026-08-21T18:08:36+00",
        username=None, kind="user", last_at=None, last_body="wanna grab lunch?",
    )
    writer.flush()
    for pend in writer.pending.values():
        assert pend.display or pend.username or pend.preview, (
            f"nothing recognisable about {pend.channel} channel — he cannot rule on this"
        )


def test_a_second_pull_does_not_store_what_the_first_one_held_back(
    conn: sqlite3.Connection,
) -> None:
    """The 2026-08-24 defect, pinned. It cost 14 rows from four never-ruled conversations.

    `register_pending` writes the row as `undecided`. The held-back test was
    `disposition is None`, which is true on the FIRST pull and false on every one after it — so
    the rule applied exactly once per chat and then silently stopped. It survived for as long as
    it did because it needs two pulls with no ruling in between, and until that day every
    registration had been ruled on before the next pull ran.

    Asserted against the predicate rather than through a live Telethon walk, because the walk is
    the part that cannot be tested here and the predicate is the whole of the bug.
    """
    writer = Writer(conn, commit=True)
    writer.register_pending(
        "-9000000003", "telegram", "A Family Group", 9, "2026-08-21T18:08:36+00"
    )
    writer.flush()
    conn.commit()

    from neo_sensor.ingest import dispositions

    disposition = dispositions(conn)["-9000000003"]
    assert disposition == "undecided", "this is what register_pending leaves behind"

    # The two live call sites compute it this way. Both said `is None` before the fix.
    assert disposition in (None, "undecided"), (
        "a registered-but-unruled chat must still read as unruled on the next pull, or the "
        "second pull stores what the first one refused to"
    )
    assert telegram_fetch.should_pull(disposition), (
        "it is still WALKED — that is how the count and the newest message are obtained; "
        "what must not happen is that the walk writes"
    )


# --------------------------------------------------------------------------- 2026-09-07
# Four defects found in one reading of this write path, all of the same species: a column or a
# label asserting something the data does not support. The read-back assertions live here rather
# than with the other `corpus_cli` tests because a column written correctly and rendered wrongly
# is exactly the defect that was found — `pending_preview` was captured faithfully for two weeks
# and printed as the wrong person's words the whole time.


def test_a_renamed_chat_gets_its_new_name_on_the_next_pull(
    conn: sqlite3.Connection,
) -> None:
    """`display_name` follows a rename, and for eight months it could not.

    The `UPDATE` read `COALESCE(display_name, ?)` — fill a NULL, never overwrite — so a name was
    written once at registration and no pull could change it. Found when a pull listed a dialog
    as `Partner Members` while the corpus still said `Partner Assembly 2026 (UK)`, which made
    `neo-corpus thread` answer "no such chat, or it is not admitted" and made `neo-send` refuse
    the room permanently.
    """
    admit(conn, "-100123")
    conn.execute(
        "UPDATE channels SET display_name='Old Name' WHERE chat_key='-100123'"
    )
    writer = Writer(conn, commit=True)

    assert writer.note_channel("-100123", "telegram", "New Name") is False
    conn.commit()
    assert conn.execute(
        "SELECT display_name FROM channels WHERE chat_key='-100123'"
    ).fetchone()[0] == "New Name"

    # And the NULL-safety the old argument order was there for still holds: a dialog Telegram
    # reports with no title cannot erase a title the corpus already has.
    assert writer.note_channel("-100123", "telegram", None) is False
    conn.commit()
    assert conn.execute(
        "SELECT display_name FROM channels WHERE chat_key='-100123'"
    ).fetchone()[0] == "New Name"


def test_register_pending_refreshes_the_name_like_its_four_siblings(
    conn: sqlite3.Connection,
) -> None:
    """The same inverted `COALESCE`, in the same statement as four correct ones.

    `pending_username`, `pending_kind`, `pending_last_at` and `pending_preview` all read
    `COALESCE(excluded.X, channels.X)` — new value wins. `display_name` alone read
    `COALESCE(channels.display_name, excluded.display_name)`. Four against one in one `ON
    CONFLICT` clause is what says this was a slip rather than a decision.
    """
    writer = Writer(conn, commit=True)
    args = ("9000000002", "telegram", None, 1, "2026-09-01T00:00:00+00")
    writer.register_pending(*args[:2], "First Seen Name", *args[3:])
    writer.register_pending(*args[:2], "Renamed Since", *args[3:])
    conn.commit()

    assert conn.execute(
        "SELECT display_name FROM channels WHERE chat_key='9000000002'"
    ).fetchone()[0] == "Renamed Since"


def test_the_pending_preview_records_who_spoke(conn: sqlite3.Connection) -> None:
    """`F-007` asked for what the ACCOUNT said. The preview held whoever spoke last.

    The newest message in a 1:1 is the operator's own about half the time, so an unattributed
    preview hands him his own words as the stranger's — which is the one thing he is being asked
    to identify. It happened on `9000000002`: the preview read `👌 will be there in a minute`,
    his, while the account had written `Hey there! I have a window now…`.
    """
    writer = Writer(conn, commit=True)
    writer.register_pending(
        "9000000002", "telegram", "Somebody", 2, "2026-09-01T00:00:00+00",
        kind="user", last_at=datetime(2026, 9, 1, 19, 6, tzinfo=UTC),
        last_body="ok, on my way", last_out=True,
    )
    writer.register_pending(
        "9000000001", "telegram", "Someone Else", 1, "2026-09-01T00:00:00+00",
        kind="user", last_at=datetime(2026, 9, 1, 18, 41, tzinfo=UTC),
        last_body="Hi, are we still on for Saturday?", last_out=False,
    )
    writer.flush()
    conn.commit()

    stored = dict(
        conn.execute(
            "SELECT chat_key, pending_last_out FROM channels "
            "WHERE chat_key IN ('9000000002','9000000001')"
        ).fetchall()
    )
    assert stored == {"9000000002": 1, "9000000001": 0}

    # A caller that cannot tell must be able to say so. `None` is not `False`: one is "I don't
    # know who spoke", the other is a claim that it was the other party.
    writer.register_pending(
        "-100555", "telegram", "A Group", 1, "2026-09-01T00:00:00+00", last_body="hello"
    )
    conn.commit()
    assert conn.execute(
        "SELECT pending_last_out FROM channels WHERE chat_key='-100555'"
    ).fetchone()[0] is None

    # And it goes with the other four the moment the channel is ruled on.
    writer.note_channel("9000000002", "telegram", "Somebody")
    conn.commit()
    assert conn.execute(
        "SELECT pending_last_out FROM channels WHERE chat_key='9000000002'"
    ).fetchone()[0] is None


def test_channels_pending_attributes_the_preview_and_never_guesses(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """Written correctly and rendered wrongly is the defect that was actually found.

    So the assertion is on what the operator READS, not on the column. `None` must render as a
    bare quote — a corpus written before this column existed knows the text and genuinely does
    not know the speaker, and inventing a speaker there would be the same failure one layer
    down.
    """
    from neo_sensor import corpus_cli

    writer = Writer(conn, commit=True)
    writer.register_pending(
        "9000000002", "telegram", "Him Last", 2, "2026-09-01T00:00:00+00",
        kind="user", last_body="ok, on my way", last_out=True,
    )
    writer.register_pending(
        "9000000001", "telegram", "Them Last", 1, "2026-09-01T00:00:00+00",
        kind="user", last_body="still on for Saturday?", last_out=False,
    )
    writer.register_pending(
        "-100555", "telegram", "Unknown Who", 1, "2026-09-01T00:00:00+00",
        kind="group", last_body="no direction recorded",
    )
    conn.commit()

    corpus_cli.cmd_channels_pending(
        conn, argparse.Namespace(limit=50, json=False)
    )
    out = capsys.readouterr().out
    assert "> [he said] ok, on my way" in out
    assert "> [they said] still on for Saturday?" in out
    assert "> no direction recorded" in out
    assert "[he said] no direction recorded" not in out
    assert "[they said] no direction recorded" not in out


def test_channels_pending_degrades_on_a_corpus_without_the_direction_column(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """The verb's contract is to degrade rather than break, and it has two guards now.

    `neo-corpus` opens the store read-only and cannot migrate it, so a corpus written by the
    version that added the first four `pending_*` columns must still be readable. Guarding the
    fifth on the fourth's presence is the mistake `ensure_pending_columns` already documents.
    """
    from neo_sensor import corpus_cli

    writer = Writer(conn, commit=True)
    writer.register_pending(
        "9000000002", "telegram", "Somebody", 1, "2026-09-01T00:00:00+00",
        kind="user", last_body="ok, on my way", last_out=True,
    )
    conn.commit()

    # SQLite cannot DROP a column on older versions; rebuilding the table is more faithful to
    # "a corpus that predates this" than monkeypatching the query would be.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(channels)")
            if r[1] != "pending_last_out"]
    names = ", ".join(cols)
    conn.executescript(
        f"CREATE TABLE ch_old AS SELECT {names} FROM channels;"
        "DROP TABLE channels;"
        "ALTER TABLE ch_old RENAME TO channels;"
    )
    conn.commit()
    assert "pending_last_out" not in {
        r[1] for r in conn.execute("PRAGMA table_info(channels)")
    }

    assert corpus_cli.cmd_channels_pending(
        conn, argparse.Namespace(limit=50, json=False)
    ) == 0
    out = capsys.readouterr().out
    assert "> ok, on my way" in out, "the text is still shown"
    assert "he said" not in out and "they said" not in out, "and no speaker is invented"


def test_ensure_pending_columns_adds_the_fifth_to_a_four_column_corpus(
    conn: sqlite3.Connection,
) -> None:
    """The guard that would otherwise run once and never again on the machines that need it."""
    from neo_sensor.ingest import ensure_pending_columns

    cols = [r[1] for r in conn.execute("PRAGMA table_info(channels)")
            if r[1] != "pending_last_out"]
    names = ", ".join(cols)
    conn.executescript(
        f"CREATE TABLE ch_old AS SELECT {names} FROM channels;"
        "DROP TABLE channels;"
        "ALTER TABLE ch_old RENAME TO channels;"
    )
    conn.commit()

    ensure_pending_columns(conn)
    assert "pending_last_out" in {
        r[1] for r in conn.execute("PRAGMA table_info(channels)")
    }
    # Idempotent — a second pull must not try to add it again.
    ensure_pending_columns(conn)


def test_held_mail_keeps_the_newest_message_whichever_way_graph_pages() -> None:
    """`_Held` set its fields on FIRST sight "because Graph pages newest-first". It does not.

    The default sort for a message collection is `receivedDateTime desc`, but this puller asks
    for `$orderby=receivedDateTime`, which is ascending — so first sight was the OLDEST message
    and `channels-pending` printed it under the word `newest`. Only visible on a correspondent
    with two or more held messages, which is one row in a typical pull.

    Asserted in BOTH page orders on purpose: the fix is a running maximum, so it must not depend
    on the order at all, and a test that only feeds one order would pass on the old code too.
    """
    from neo_sensor.graph_fetch import _Held

    older = ("2026-09-01T12:00:00Z", "First subject", "older body", False)
    newer = ("2026-09-04T18:00:00Z", "Latest subject", "newer body", False)

    for order in ((older, newer), (newer, older)):
        held = _Held(sender="cs@vendor.example", count=0)
        for received, subject, body, out in order:
            held.see(received, subject, body, out)
        assert held.count == 2
        assert held.last_at == "2026-09-04T18:00:00Z", f"page order {order[0][0]} first"
        assert held.subject == "Latest subject"
        assert held.body == "newer body"


def test_held_mail_records_direction_from_the_author(conn: sqlite3.Connection) -> None:
    """Graph keys a chat by sender, so a held correspondent is inbound in practice — but the
    flag is derived from the author rather than assumed, which is the same rule the stored
    rows' `direction_source='author'` records."""
    from neo_sensor.graph_fetch import _Held

    theirs = _Held(sender="cs@vendor.example", count=0)
    theirs.see("2026-09-01T12:00:00Z", "Hello", "body", "cs@vendor.example" in OPERATOR_EMAILS)
    assert theirs.out is False

    his = _Held(sender=OPERATOR_EMAILS[0], count=0)
    his.see("2026-09-01T12:00:00Z", "Re: Hello", "body", OPERATOR_EMAILS[0] in OPERATOR_EMAILS)
    assert his.out is True


def test_mail_names_the_filter_that_emptied_it_not_enrichment(
    conn: sqlite3.Connection,
) -> None:
    """The `thread`-said-"not admitted" defect of 2026-08-15, in a second verb.

    `mail` printed `(no enriched Outlook conversations in range)` whatever had happened, which is
    the worse wording of the two: it does not merely say "nothing here", it says *why*, and the
    why names a twenty-two-minute mailbox re-fetch as the remedy for a result that was never
    missing. Three real causes, three different sentences.
    """
    from neo_sensor.corpus_cli import _why_no_mail

    admit(conn, "graph:vendor@example.com")
    conn.execute(
        "UPDATE channels SET channel='graph' WHERE chat_key='graph:vendor@example.com'"
    )
    args = argparse.Namespace(
        since=None, until=None, min_messages=2, include_suppressed=False, chat=None
    )

    # 1. nothing there at all
    assert "no visible Outlook mail in range" in _why_no_mail(conn, args, "", [])

    writer = Writer(conn, commit=True)
    for i in range(2):
        writer.write(event(
            channel="graph", chat_key="graph:vendor@example.com",
            external_id=f"graph:{i}", sender="vendor@example.com", direction="inbound",
            observed_at=f"2026-09-0{i + 1}T09:00:00+00:00", body=f"mail {i}",
        ))
    writer.flush()
    conn.commit()

    # 2. rows, but no conversation_id — the ONLY case that is really about enrichment
    said = _why_no_mail(conn, args, "", [])
    assert "conversation_id" in said and "neo-fetch enrich" in said

    conn.execute(
        "UPDATE events SET conversation_id = 'conv-' || external_id "
        "WHERE chat_key='graph:vendor@example.com'"
    )
    conn.commit()

    # 3. threaded, but every conversation is a singleton and the floor is 2
    said = _why_no_mail(conn, args, "", [])
    assert "--min-messages 1" in said
    assert "Nothing is missing from the corpus" in said
    assert "enrich" not in said, "this is the false diagnosis the whole fix is about"


# --------------------------------------------------------------------------- wiring
# The three probes that passed when they should have failed. Reverting the fetcher's capture and
# reverting `cmd_mail`'s call site both left the tests above green, because those tests exercised
# `register_pending` and `_why_no_mail` DIRECTLY and nothing asserted that anything calls them.
#
# That is this project's own documented failure shape — "context that was fetched into a struct
# and never rendered into the prompt" — reproduced in its test suite. A helper with a unit test
# and no caller is indistinguishable from a helper nobody wired up.


class _StubMessage:
    """Enough of a Telethon message for the unruled-dialog branch of `pull`."""

    def __init__(self, when: datetime, text: str, *, out: bool) -> None:
        self.date, self.message, self.out = when, text, out
        self.media = None
        self.id = int(when.timestamp())
        self.entities = None


class _StubDialog:
    def __init__(self, chat_id: int, name: str | None, messages: list[_StubMessage]) -> None:
        self.id, self.name, self._messages = chat_id, name, messages
        self.entity = SimpleNamespace(username=None, bot=False, deleted=False)


class _StubClient:
    """`_client()`'s return value, with only what `pull` touches."""

    def __init__(self, dialogs: list[_StubDialog]) -> None:
        self._dialogs = dialogs

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def is_user_authorized(self) -> bool:
        return True

    async def iter_dialogs(self):  # noqa: ANN202
        for d in self._dialogs:
            yield d

    async def iter_messages(self, entity, **_kwargs):  # noqa: ANN001, ANN202
        for d in self._dialogs:
            if d.entity is entity:
                for m in d._messages:  # oldest-first, as `reverse=True` gives
                    yield m


def test_the_pull_actually_hands_the_direction_to_register_pending(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through `pull`, because the unit tests above could not see this.

    Reverting `last_out = bool(message.out)` to `None` left both direction tests passing — they
    called `register_pending` themselves. So this one drives the fetcher and asserts on the
    column, which is the only place the capture, the plumbing and the store all have to agree.

    The dialog is oldest-first and the NEWEST message is his, which is the case that produced the
    defect: the account said the first thing, he said the last, and the preview offered his words
    as the account's.
    """
    import asyncio

    dialog = _StubDialog(
        9000000002,
        "Somebody",
        [
            _StubMessage(datetime(2026, 9, 1, 18, 41, tzinfo=UTC), "Hey, got a window now", out=False),
            _StubMessage(datetime(2026, 9, 1, 19, 6, tzinfo=UTC), "ok, on my way", out=True),
        ],
    )
    monkeypatch.setattr(telegram_fetch, "_client", lambda _args: _StubClient([dialog]))
    monkeypatch.setattr(telegram_fetch, "_secure_session", lambda: None)
    monkeypatch.setattr(telegram_fetch, "rebuild_index", lambda _conn: None)

    args = argparse.Namespace(
        commit=True, since="2026-09-01", chat=None, limit_per_chat=0, include_new=False
    )
    assert asyncio.run(telegram_fetch.pull(conn, args)) == 0

    preview, last_out = conn.execute(
        "SELECT pending_preview, pending_last_out FROM channels WHERE chat_key='9000000002'"
    ).fetchone()
    assert preview == "ok, on my way", "the newest message, as it always was"
    assert last_out == 1, "and now the corpus knows it was HIS"


def test_cmd_mail_uses_the_three_way_diagnosis_not_the_old_sentence(
    conn: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    """The call site, not the helper.

    Reverting `cmd_mail`'s `empty=` back to the hard-coded string left
    `test_mail_names_the_filter_that_emptied_it_not_enrichment` green, because that test calls
    `_why_no_mail` directly. This asserts on what the verb prints.
    """
    from neo_sensor import corpus_cli

    admit(conn, "graph:vendor@example.com")
    conn.execute(
        "UPDATE channels SET channel='graph' WHERE chat_key='graph:vendor@example.com'"
    )
    writer = Writer(conn, commit=True)
    for i in range(2):
        writer.write(event(
            channel="graph", chat_key="graph:vendor@example.com",
            external_id=f"graph:{i}", sender="vendor@example.com", direction="inbound",
            observed_at=f"2026-09-0{i + 1}T09:00:00+00:00", body=f"mail {i}",
        ))
    writer.flush()
    conn.execute(
        "UPDATE events SET conversation_id = 'conv-' || external_id "
        "WHERE chat_key='graph:vendor@example.com'"
    )
    conn.commit()

    args = argparse.Namespace(
        since=None, until=None, chat=None, conversation=None, min_messages=2, limit=60,
        json=False, include_suppressed=False, preview=None,
    )
    assert corpus_cli.cmd_mail(conn, args) == 0
    out = capsys.readouterr().out
    assert "--min-messages 1" in out
    assert "no enriched Outlook conversations in range" not in out, (
        "the sentence this whole fix exists to delete"
    )
