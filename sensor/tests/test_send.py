"""The send path, asserted where it can be — everything up to the network call.

The send itself needs Telegram and a live session, so it is not tested here; what IS tested is
every gate that decides *whether* a send happens and *what bytes* it would carry. That split is
deliberate: the untestable part is one `client.send_message(entity, text, parse_mode=None)`, and
the interesting part is all the refusing.

Built 2026-08-24, with the module. Until that day the project had no egress at all, so nothing in
this file could have existed and none of these properties needed defending.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from neo_sensor.telegram_send import (
    FOOTNOTE,
    MAX_CHARS,
    Refused,
    compose,
    record,
    resolve_target,
)


@pytest.fixture
def channels(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "ch.db")
    c.execute(
        "CREATE TABLE channels (chat_key TEXT PRIMARY KEY, channel TEXT, display_name TEXT, "
        "disposition TEXT)"
    )
    c.executemany(
        "INSERT INTO channels VALUES (?,?,?,?)",
        [
            ("-1001", "telegram", "IT Desk", "work"),
            ("-1002", "telegram", "IT Desk Administrators", "work"),
            ("-1003", "telegram", "Family", "personal"),
            ("-1004", "telegram", "Newsletter", "ignore"),
            ("-1005", "telegram", "Never ruled", "undecided"),
            ("-1006", "telegram", "Twins", "work"),
            ("-1007", "telegram", "Twins", "work"),
            ("graph:x@y.com", "graph", "x@y.com", "work"),
        ],
    )
    c.commit()
    yield c
    c.close()


# ------------------------------------------------------------------ the footnote


def test_the_footnote_is_appended_and_is_not_a_parameter(tmp_path: Path) -> None:
    """His standing requirement, 2026-08-24: *always* append it.

    `compose` takes a path and nothing else — there is no argument by which a caller could omit
    the footnote, choose different wording, or pass it in. That is the same reasoning that makes
    `Writer.write` redact rather than accept a `body_redacted`: the predecessor gated egress on a
    flag every call site hardcoded, and a flag is a thing a call site can get wrong.
    """
    path = tmp_path / "m.txt"
    path.write_text("Hi team, one line.\n", encoding="utf-8")
    text = compose(path)
    assert text.endswith(FOOTNOTE)
    assert text.startswith("Hi team, one line.")
    assert "\n\n" + FOOTNOTE in text, "separated by a blank line, not glued to the last bullet"

    import inspect

    assert list(inspect.signature(compose).parameters) == ["path"], (
        "compose must take nothing but the path — any second parameter is a way to skip the "
        "footnote"
    )


def test_a_draft_that_already_carries_the_footnote_is_refused(tmp_path: Path) -> None:
    """Otherwise it arrives twice and nothing notices — the failure is invisible on the sender's
    side and only visible to the twenty people in the room."""
    path = tmp_path / "m.txt"
    path.write_text(f"Body.\n\n{FOOTNOTE}\n", encoding="utf-8")
    with pytest.raises(Refused, match="already contains the footnote"):
        compose(path)


def test_trailing_newlines_do_not_become_a_gap(tmp_path: Path) -> None:
    """A trailing newline is invisible in an editor and arrives in Telegram as a blank line."""
    path = tmp_path / "m.txt"
    path.write_text("Body.\n\n\n\n", encoding="utf-8")
    assert compose(path) == f"Body.\n\n{FOOTNOTE}"


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    """A blank message plus a footnote is a message announcing that a machine wrote nothing."""
    path = tmp_path / "m.txt"
    path.write_text("   \n\n", encoding="utf-8")
    with pytest.raises(Refused, match="empty"):
        compose(path)


def test_over_length_is_refused_rather_than_split(tmp_path: Path) -> None:
    """Telethon splits silently. For a formatted weekly report a silent split is a mangled one —
    the `=====` header lands alone and the bullets arrive as a separate message."""
    path = tmp_path / "m.txt"
    path.write_text("x" * MAX_CHARS, encoding="utf-8")
    with pytest.raises(Refused, match="over Telegram's"):
        compose(path)


# ------------------------------------------------------------------ the target


def test_only_a_work_channel_can_be_written_to(channels: sqlite3.Connection) -> None:
    """The read fence decides what may be read; this is that shape applied to egress.

    `undecided` is the sharp one — he has not decided whether an agent may even READ it, so it is
    certainly not somewhere an agent may post.
    """
    assert resolve_target(channels, "IT Desk") == ("-1001", "IT Desk")
    assert resolve_target(channels, "-1001") == ("-1001", "IT Desk")

    for name in ("Family", "Newsletter", "Never ruled"):
        with pytest.raises(Refused, match="Refusing to post there"):
            resolve_target(channels, name)


def test_a_shared_display_name_is_refused_not_guessed(channels: sqlite3.Connection) -> None:
    """This corpus really does hold chats sharing a display name — unioning by name once made two
    chats look like one and put a manufactured claim on two knowledge files. Guessing the bigger
    one would be the same mistake with a message in the wrong room as the consequence."""
    with pytest.raises(Refused, match="ambiguous"):
        resolve_target(channels, "Twins")


def test_a_near_miss_name_does_not_match(channels: sqlite3.Connection) -> None:
    """`IT Desk` and `IT Desk Administrators` are different rooms with different memberships. Exact
    match only — no prefix, no `LIKE`, no fuzzy resolution."""
    assert resolve_target(channels, "IT Desk Administrators") == ("-1002", "IT Desk Administrators")
    with pytest.raises(Refused, match="no Telegram channel named"):
        resolve_target(channels, "CP I")


def test_a_graph_channel_is_not_a_telegram_target(channels: sqlite3.Connection) -> None:
    """Outlook is explicitly out of scope — his instruction, 2026-08-24: *"For now, just Telegram,
    not Graph / Outlook"*. The query is scoped to `channel='telegram'`, so a Graph correspondent
    that happens to be ruled `work` cannot be addressed by accident."""
    with pytest.raises(Refused, match="no Telegram channel named"):
        resolve_target(channels, "x@y.com")


def test_an_unknown_chat_cannot_be_addressed(channels: sqlite3.Connection) -> None:
    """It must already be in the corpus with a disposition. There is no path from a name the
    sensor has never seen to a message."""
    with pytest.raises(Refused, match="no Telegram channel named"):
        resolve_target(channels, "Some Group He Never Ruled On")


# ------------------------------------------------------------------ the record


def test_a_send_is_recorded_beside_the_draft(tmp_path: Path) -> None:
    """`drafts/_TEMPLATE` has carried a `sent.md` slot since it was written and nothing ever
    filled it, because there was no send path. This closes the transparency substrate: the
    directory holds the request, the context, the candidates and now what actually went out."""
    path = tmp_path / "msg-1.txt"
    path.write_text("Body.\n", encoding="utf-8")
    text = compose(path)
    log = record(path, "-1001", "IT Desk", text, "2026-08-24T18:00:00")
    assert log == tmp_path / "sent.md"
    written = log.read_text(encoding="utf-8")
    assert "IT Desk" in written and "-1001" in written and "msg-1.txt" in written
    assert FOOTNOTE in written, "the record must hold what was SENT, footnote included"

    # Two sends into one directory append rather than overwrite.
    record(path, "-1001", "IT Desk", text, "2026-08-24T18:01:00")
    assert log.read_text(encoding="utf-8").count("msg-1.txt") == 2


# ------------------------------------------------------------------ the approval gate
#
# His ruling, 2026-08-24: *"the sends should also become yours but there must be an
# in-conversation approval gate."* Every test below is a way an unapproved or drifted message
# could have gone out. The one thing NOT tested — because it is not testable and not true — is
# that a human approved anything: the agent runs both halves, so this gate makes an unapproved
# send a fabricated record rather than an oversight. That is a real difference and it is not the
# same as his hand on the command.


def _batch(tmp_path: Path, *bodies: str) -> list[Path]:
    out = []
    for i, body in enumerate(bodies, 1):
        p = tmp_path / f"msg-{i}.txt"
        p.write_text(body, encoding="utf-8")
        out.append(p)
    return out


def test_commit_without_an_approval_is_refused(tmp_path: Path) -> None:
    from neo_sensor.telegram_send import check_approval

    paths = _batch(tmp_path, "One.")
    with pytest.raises(Refused, match="no approval on file"):
        check_approval(paths, "-1001", [compose(paths[0])])


def test_an_approval_survives_the_round_trip(tmp_path: Path) -> None:
    from neo_sensor.telegram_send import check_approval, write_approval

    paths = _batch(tmp_path, "One.", "Two.")
    texts = [compose(p) for p in paths]
    write_approval(paths, "-1001", "IT Desk", texts, "both drafts approved", "sess-1")
    got = check_approval(paths, "-1001", texts)
    assert got["approved_quote"] == "both drafts approved"
    assert got["display_name"] == "IT Desk"


def test_editing_the_draft_after_approval_is_refused(tmp_path: Path) -> None:
    """The core property. "Approved this, sent that" is not a mistake that can be made."""
    from neo_sensor.telegram_send import check_approval, write_approval

    paths = _batch(tmp_path, "One.", "Two.")
    write_approval(paths, "-1001", "IT Desk", [compose(p) for p in paths], "ok", None)
    paths[1].write_text("Two, but different.", encoding="utf-8")
    with pytest.raises(Refused, match="text changed since he approved it.*msg-2"):
        check_approval(paths, "-1001", [compose(p) for p in paths])


def test_a_message_added_after_approval_is_refused(tmp_path: Path) -> None:
    """Approving two and sending three is the same failure wearing a different hat."""
    from neo_sensor.telegram_send import check_approval, write_approval

    paths = _batch(tmp_path, "One.", "Two.")
    write_approval(paths, "-1001", "IT Desk", [compose(p) for p in paths], "ok", None)
    extra = tmp_path / "msg-3.txt"
    extra.write_text("Three.", encoding="utf-8")
    grown = [*paths, extra]
    with pytest.raises(Refused, match="batch changed since approval"):
        check_approval(grown, "-1001", [compose(p) for p in grown])


def test_reordering_the_batch_is_refused(tmp_path: Path) -> None:
    """Three messages in a different sequence are a different post — the weekly update reads
    header, then report, then the ask, and any other order is not what he approved."""
    from neo_sensor.telegram_send import check_approval, write_approval

    paths = _batch(tmp_path, "One.", "Two.")
    write_approval(paths, "-1001", "IT Desk", [compose(p) for p in paths], "ok", None)
    flipped = list(reversed(paths))
    with pytest.raises(Refused, match="batch changed since approval"):
        check_approval(flipped, "-1001", [compose(p) for p in flipped])


def test_approved_text_cannot_be_posted_in_a_different_room(tmp_path: Path) -> None:
    from neo_sensor.telegram_send import check_approval, write_approval

    paths = _batch(tmp_path, "One.")
    texts = [compose(paths[0])]
    write_approval(paths, "-1001", "IT Desk", texts, "ok", None)
    with pytest.raises(Refused, match="Refusing to post approved text in a different room"):
        check_approval(paths, "-1002", texts)


def test_an_expired_approval_is_refused(tmp_path: Path) -> None:
    """The most plausible way this goes wrong with nobody lying: an approval from an earlier
    conversation, honoured today."""
    import json

    from neo_sensor.telegram_send import check_approval, write_approval

    paths = _batch(tmp_path, "One.")
    texts = [compose(paths[0])]
    record_path = write_approval(paths, "-1001", "IT Desk", texts, "ok", None)
    stale = json.loads(record_path.read_text())
    stale["expires_at"] = "2026-08-17T00:00:00Z"
    record_path.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(Refused, match="expired"):
        check_approval(paths, "-1001", texts)


def test_an_approval_is_single_use(tmp_path: Path) -> None:
    """So `--commit` twice cannot post the same weekly report twice into a room of colleagues."""
    from neo_sensor.telegram_send import check_approval, consume_approval, write_approval

    paths = _batch(tmp_path, "One.")
    texts = [compose(paths[0])]
    write_approval(paths, "-1001", "IT Desk", texts, "ok", None)
    check_approval(paths, "-1001", texts)
    consume_approval(paths)
    with pytest.raises(Refused, match="already used"):
        check_approval(paths, "-1001", texts)


def test_approval_needs_his_actual_words(tmp_path: Path) -> None:
    """The quote is the only field a later reader can check against the conversation. Without it
    the record proves nothing at all, so an empty one is refused rather than stored."""
    from neo_sensor.telegram_send import write_approval

    paths = _batch(tmp_path, "One.")
    with pytest.raises(Refused, match="needs --quote"):
        write_approval(paths, "-1001", "IT Desk", [compose(paths[0])], "   ", None)


def test_a_batch_must_live_in_one_directory(tmp_path: Path) -> None:
    """One drafting task, one directory — the convention the whole `drafts/` substrate rests on,
    and a batch spanning two has no obvious home for its approval."""
    from neo_sensor.telegram_send import approval_path

    a = tmp_path / "one"
    b = tmp_path / "two"
    a.mkdir()
    b.mkdir()
    (a / "m.txt").write_text("x", encoding="utf-8")
    (b / "m.txt").write_text("y", encoding="utf-8")
    with pytest.raises(Refused, match="same directory"):
        approval_path([a / "m.txt", b / "m.txt"])


def test_the_send_record_carries_his_words(tmp_path: Path) -> None:
    """`sent.md` is the durable half. The approval file expires and is consumed; the git-tracked
    record of what went out, and on whose word, does not."""
    from neo_sensor.telegram_send import record as record_send

    path = tmp_path / "msg-1.txt"
    path.write_text("Body.\n", encoding="utf-8")
    log = record_send(
        path, "-1001", "IT Desk", compose(path), "2026-08-24T18:00:00", quote="both drafts approved"
    )
    assert "both drafts approved" in log.read_text(encoding="utf-8")


def test_live_title_uses_the_same_display_name_as_the_pull():
    """The send-time title check must name a two-part 1:1 the way the pull did.

    `title or first_name` yielded `Ann` against the corpus's `Ann Lee` and refused every
    such 1:1 (2026-10-01). Both sides now go through Telethon's `get_display_name`.
    """
    from telethon.tl.types import User
    from telethon.utils import get_display_name

    assert get_display_name(User(id=1, first_name="Ann", last_name="Lee")) == "Ann Lee"
    src = (Path(__file__).parent.parent / "neo_sensor" / "telegram_send.py").read_text()
    assert "get_display_name(entity)" in src
