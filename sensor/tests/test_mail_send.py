"""`neo-mail` — every gate up to the network call, as `test_send.py` does for Telegram.

Built 2026-09-22 with the module. The Graph calls are not tested here; what is tested is what
decides whether a reply-all happens, to whom, and with which bytes.
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from neo_sensor import graph_send, telegram_send
from neo_sensor.graph_fetch import html_to_text
from neo_sensor.graph_send import (
    FOOTNOTE,
    Refused,
    compose,
    draft_recipients,
    insert_above_quote,
    reply_all_recipients,
    resolve_message,
    target_key,
    verify_body,
)


@pytest.fixture
def store(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(tmp_path / "s.db")
    c.execute("CREATE TABLE channels (chat_key TEXT, channel TEXT, display_name TEXT, disposition TEXT)")
    c.execute(
        "CREATE TABLE events (channel TEXT, external_id TEXT, chat_key TEXT, subject TEXT, "
        "observed_at TEXT, body TEXT)"
    )
    c.executemany(
        "INSERT INTO channels VALUES (?,?,?,?)",
        [
            ("graph:dave@x.com", "graph", "dave@x.com", "work"),
            ("graph:news@x.com", "graph", "news@x.com", "ignore"),
            ("graph:pending@x.com", "graph", "pending@x.com", "undecided"),
        ],
    )
    c.executemany(
        "INSERT INTO events VALUES (?,?,?,?,?,?)",
        [
            ("graph", "AAA", "graph:dave@x.com", "Fw: issue", "2026-09-22T18:05:00Z", "b"),
            ("graph", "BBB", "graph:dave@x.com", "Weekly", "2026-09-01T10:00:00Z", "b"),
            ("graph", "CCC", "graph:dave@x.com", "Weekly", "2026-09-08T10:00:00Z", "b"),
            ("graph", "DDD", "graph:news@x.com", "Hello", "2026-09-22T10:00:00Z", "b"),
        ],
    )
    c.commit()
    yield c
    c.close()


def test_resolves_one_message_by_chat_and_exact_subject(store: sqlite3.Connection) -> None:
    ext, key, subj, _ = resolve_message(store, "dave@x.com", "Fw: issue", None)
    assert (ext, key, subj) == ("AAA", "graph:dave@x.com", "Fw: issue")


def test_a_repeated_subject_is_refused_until_at_disambiguates(store: sqlite3.Connection) -> None:
    with pytest.raises(Refused, match="2 messages"):
        resolve_message(store, "dave@x.com", "Weekly", None)
    assert resolve_message(store, "dave@x.com", "Weekly", "2026-09-08")[0] == "CCC"


@pytest.mark.parametrize("who", ["news@x.com", "pending@x.com", "stranger@x.com"])
def test_only_a_work_correspondent_can_be_answered(store: sqlite3.Connection, who: str) -> None:
    with pytest.raises(Refused):
        resolve_message(store, who, "Hello", None)


def test_resolution_reads_no_body_column() -> None:
    """The fence: the send path reads metadata, never `body`."""
    src = inspect.getsource(resolve_message)
    assert "body" not in src.split('"""', 2)[2]


def test_reply_all_prediction_drops_him_and_dedupes() -> None:
    msg = {
        "from": {"emailAddress": {"address": "Dave@x.com"}},
        "toRecipients": [
            {"emailAddress": {"address": "sam@x.com"}},
            {"emailAddress": {"address": "operator@example.com"}},
            {"emailAddress": {"address": "dave@x.com"}},
        ],
        "ccRecipients": [{"emailAddress": {"address": "erin@x.com"}}],
    }
    assert reply_all_recipients(msg, "operator@example.com") == [
        "dave@x.com",
        "erin@x.com",
        "sam@x.com",
    ]


def test_bcc_counts_as_a_recipient_on_the_draft() -> None:
    d = {"toRecipients": [{"emailAddress": {"address": "a@x.com"}}],
         "bccRecipients": [{"emailAddress": {"address": "hidden@x.com"}}]}
    assert draft_recipients(d) == ["a@x.com", "hidden@x.com"]


def test_the_approval_is_pinned_to_the_recipients(tmp_path: Path) -> None:
    """Approved for three people; the original gains a fourth; refused."""
    p = tmp_path / "msg.txt"
    p.write_text("Erin, one question.\n", encoding="utf-8")
    text = compose(p)
    approved = target_key("AAA", ["erin@x.com", "sam@x.com"])
    telegram_send.write_approval([p], approved, "d", [text], "send it", None)
    telegram_send.check_approval([p], approved, [text])  # same people: passes
    widened = target_key("AAA", ["client@y.com", "erin@x.com", "sam@x.com"])
    with pytest.raises(Refused, match="not for"):
        telegram_send.check_approval([p], widened, [text])


def test_an_edit_after_approval_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "msg.txt"
    p.write_text("v1\n", encoding="utf-8")
    key = target_key("AAA", ["sam@x.com"])
    telegram_send.write_approval([p], key, "d", [compose(p)], "ok", None)
    p.write_text("v2\n", encoding="utf-8")
    with pytest.raises(Refused, match="changed"):
        telegram_send.check_approval([p], key, [compose(p)])


def test_compose_appends_the_same_footnote_and_takes_only_a_path(tmp_path: Path) -> None:
    p = tmp_path / "m.txt"
    p.write_text("Hi.\n", encoding="utf-8")
    assert compose(p).endswith(FOOTNOTE)
    assert graph_send.FOOTNOTE is telegram_send.FOOTNOTE
    assert list(inspect.signature(compose).parameters) == ["path"]


def test_his_text_is_escaped_not_interpreted() -> None:
    text = "a <b>not bold</b> & *stars*\n\nsecond para"
    out = insert_above_quote("<html><body><div>quoted</div></body></html>", text)
    assert "<b>not bold</b>" not in out
    assert "&lt;b&gt;" in out
    rendered = html_to_text(out)
    assert rendered.startswith("a <b>not bold</b> & *stars*")
    assert rendered.index("second para") < rendered.index("quoted"), "above the quote"


def test_verify_body_refuses_a_draft_that_does_not_open_with_the_text() -> None:
    text = "Erin, a question." + telegram_send.FOOTNOTE_BLOCK
    good = insert_above_quote("<html><body><div>quoted</div></body></html>", text)
    verify_body(good, text)
    with pytest.raises(Refused):
        verify_body("<html><body><div>something else</div>" + good, text)
