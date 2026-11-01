"""The fence, asserted against the real corpus — not fixtures.

F26's lesson is specifically that isolated tests passed while the real payload leaked: the
credential was detected at 0.30, dropped by a 0.5 threshold, and the gate flag read True. So
these tests run against `$NEO_STATE_DIR/corpus.db` (default `~/.local/state/neo/corpus.db`) when it exists, and the leak checks are
regex sweeps over every redacted body actually in it.

Two of these are source-greps rather than behaviour tests. That is deliberate: the guarantee
being protected is "no read path can name the raw column", which is a property of the source,
and a behaviour test cannot prove the absence of a query that nobody wrote yet.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest

from neo_sensor import corpus_cli
from neo_sensor.config import STATE_DIR
from neo_sensor.redaction import REDACT_ENTITIES, redact

DB = STATE_DIR / "corpus.db"
SRC = Path(corpus_cli.__file__)

requires_corpus = pytest.mark.skipif(not DB.exists(), reason="corpus.db not present")


@pytest.fixture(scope="module")
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    yield c
    c.close()


# --------------------------------------------------------------- source-level guarantees


def _sql_literals(path: Path) -> list[str]:
    """Every SQL string in a module.

    Parsing beats grepping the raw source here: docstrings in this module discuss the raw
    column in prose, and a text search cannot tell an explanation from a query.
    """
    tree = ast.parse(path.read_text())
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"\b(SELECT|UPDATE|INSERT|FROM events)\b", node.value)
    ]


def test_read_path_never_names_the_raw_body_column() -> None:
    """No SQL in `neo-corpus` may select `body`.

    This is the structural half of the fence. The old architecture gated egress behind a
    boolean that had zero consumers, so the gate was decorative; here the raw column simply
    cannot be named by a read path, and this test fails if a future edit names it.

    Matches `body` only where it is NOT `body_redacted`/`body_hash`, so the intended column
    passes and a bare one fails.
    """
    sql = _sql_literals(SRC)
    assert sql, "found no SQL to check — the test is not actually guarding anything"
    offenders = [q for q in sql if re.search(r"\bbody\b(?!_redacted|_hash)", q)]
    assert not offenders, f"read-path SQL names the raw body column: {offenders}"


def test_read_path_never_names_the_raw_subject_column() -> None:
    """`subject` is content, and gets the same rule as `body`.

    The Graph enrichment (2026-08-14) added `subject` alongside `subject_redacted`. An email
    subject line carries exactly the things redaction exists to remove — an account name, a
    system, sometimes a credential pasted into a "here are your logins" heading — so a read
    path that selected the raw one would reopen the fence on a new column while the old guard
    kept passing.
    """
    offenders = [
        q for q in _sql_literals(SRC) if re.search(r"\bsubject\b(?!_redacted)", q)
    ]
    assert not offenders, f"read-path SQL names the raw subject column: {offenders}"


def test_the_fence_test_can_actually_fail() -> None:
    """Guard the guard: a bare `body` in SQL must be detected.

    Without this, a refactor that broke `_sql_literals` would leave the fence test passing
    vacuously — the same failure mode as the flag with no consumers.
    """
    tmp = Path(__file__).parent / "_probe_negative.py"
    tmp.write_text('Q = "SELECT body FROM events"\n')
    try:
        sql = _sql_literals(tmp)
        assert any(re.search(r"\bbody\b(?!_redacted|_hash)", q) for q in sql)
    finally:
        tmp.unlink()


def test_every_row_emitting_query_filters_on_disposition_and_redaction() -> None:
    """Both halves of the fence must appear in the shared predicate."""
    assert "redacted_at IS NOT NULL" in corpus_cli.EMITTABLE
    assert "disposition IN" in corpus_cli.EMITTABLE
    assert corpus_cli.VISIBLE == ("work", "mixed")
    # `undecided` is withheld: a channel nobody ruled on cannot leak by default.
    assert "undecided" not in corpus_cli.VISIBLE


def test_corpus_connection_is_read_only() -> None:
    if not DB.exists():
        pytest.skip("corpus.db not present")
    c = corpus_cli.connect(DB)
    try:
        with pytest.raises(sqlite3.OperationalError):
            c.execute("UPDATE channels SET disposition='work'")
    finally:
        c.close()


def _masked(match: str) -> str:
    """Render a credential match safe to print: keep the account, describe the secret.

    Returns e.g. `alice@example.com/<9 chars, alnum+special>` — enough to tell a real login
    from a false positive, and never enough to be a leak in a test log or CI output.
    """
    m = re.match(r"(?s)^(.*?)([/|:]\s*)(\S+)$", match)
    if not m:
        return f"<unparsed match, {len(match)} chars>"
    account, sep, value = m.groups()
    classes = []
    if re.search(r"[A-Za-z]", value):
        classes.append("alpha")
    if re.search(r"\d", value):
        classes.append("digit")
    if re.search(r"[^A-Za-z0-9]", value):
        classes.append("special")
    return f"{account}{sep.strip()}<{len(value)} chars, {'+'.join(classes)}>"


# --------------------------------------------------------------- behaviour on real data


@requires_corpus
def test_no_credential_shape_survives_in_any_redacted_body(conn: sqlite3.Connection) -> None:
    patterns = {
        "AWS access key id": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        "JWT triple": r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
        "GitHub PAT (classic)": r"\bghp_[A-Za-z0-9]{36,}\b",
        "GitHub PAT (fine)": r"\bgithub_pat_[A-Za-z0-9_]{82,}\b",
        "labelled credential": r"(?i)\bpass(?:word|code|phrase)\s*[:=]\s*\S{3,}",
        # Added 2026-08-14. Unlabelled `account/secret` — the shape that reached the agent
        # verbatim. Until the corpus is re-redacted with the CREDENTIAL_PAIR recogniser
        # (`redact_corpus --redo --commit`) this SHOULD fail: the fix protects new writes,
        # and every already-stamped row keeps whatever the old recogniser set missed.
        "unlabelled account/secret": (
            r"(?<![:/\w])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\s*[/|]\s*"
            r"(?=[^\s/?=]{6,})(?=[^\s/?=]*[A-Za-z])"
            r"(?=[^\s/?=]*[\d!#$%^&*()_+\[\]{};'\"\\,<>])[^\s/?=]+"
        ),
        # Added 2026-08-15, both after live credentials reached the agent through
        # `neo-corpus` during the step-3 read. Like CREDENTIAL_PAIR before them, these
        # SHOULD fail until `redact_corpus --redo --commit` is re-run: the recognisers
        # protect new writes, and every already-stamped row keeps what the previous
        # recogniser set missed. Do NOT weaken these to make the suite green.
        "network label/secret": (
            r"(?i)\b(?:wi-?fi|ssid|wlan|hotspot|network)\b[^\n]{0,24}?[:=]\s*"
            r"[^\s/|]+(?:[ \t]+[^\s/|]+){0,3}\s*[/|]\s*"
            r"(?=[^\s/|]{8,})(?=[^\s/|]*[A-Za-z])(?=[^\s/|]*[!#$%&*+=?^~])[^\s/|]+"
        ),
        # Added 2026-08-16, after eight live-shaped Anthropic keys came back in full from one
        # `neo-corpus search`. Same rule as the two above: FAILS until
        # `redact_corpus --redo --commit` is re-run. Do not weaken it to get green.
        #
        # **This test found none of them, and could not have.** It enumerates shapes somebody
        # already thought of, so its real guarantee is "these specific shapes do not survive",
        # not what its name says. Every entry below was added *after* a credential of that
        # shape had already reached an agent. A novel vendor prefix is invisible to it by
        # construction — which is why the honest guarantee of the fence is "the filter ran".
        "Anthropic API key": r"\bsk-ant-[A-Za-z0-9_-]{40,}",
        # Added 2026-08-31, and the same rule as every entry above: these SHOULD FAIL until
        # `redact_corpus --redo --commit` is re-run. The recognisers protect new writes; the 66 rows
        # already carrying a one-time code in clear keep it until the corpus is swept.
        #
        # **Restated here rather than imported from `redaction.py` on purpose** — an assertion that
        # imports the implementation it is checking only proves the implementation equals itself.
        # These are narrower than the recognisers deliberately: the label arm only, and the digit
        # floor and token guards spelled out, so a false alarm is impossible and the test says
        # "a code of the shape we KNOW about survived" rather than "something might have".
        "one-time code (labelled)": (
            r"(?i)\b(?:verification|security|one[- ]?time|sign[- ]?in|log[- ]?in|access|"
            r"authoriz\w*|authoris\w*)[ \t]+code\b[^0-9]{0,40}?"
            r"(?<![A-Za-z0-9])(?=(?:[\d\-\s]*\d){4,})\d[\d\-\s]{2,17}\d(?![A-Za-z0-9])"
        ),
        # The natural-Chinese copula. `密码：` has never matched in this corpus; every real
        # instance uses `是`, which the recogniser could not see until 2026-08-31. Two rows.
        "CJK password, copula form": (
            r"(?:初始密码|登录密码|登陆密码|账号密码|帳號密碼|密码|密碼|口令)[是为]"
            r"(?![0-9]{1,4}\s*[年月日])(?=[!-~]{3,40}(?![!-~]))(?=[!-~]*[0-9])[!-~]{3,40}"
        ),
        "standalone high-entropy body": (
            r"\A\s*(?![^\s]*://)"
            r"(?![A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\s*\Z)"
            r"(?=[\x21-\x7e]{10,64}\s*\Z)(?=\S*[A-Za-z])(?=\S*\d)(?=\S*[!#$%&*+=?^~])[\x21-\x7e]+"
        ),
        # Added 2026-08-16 (Q-043), found while reading his outbound for the voice build rather
        # than by any sweep — which is the point made three comments above, holding again. Same
        # rule as every entry since CREDENTIAL_PAIR: these SHOULD fail until
        # `redact_corpus --redo --commit` is re-run. Do not weaken them to get green.
        #
        # Measured over the visible corpus before shipping: 11 / 2 / 3 rows respectively, every
        # one a real credential. An earlier draft of the first pattern allowed `/` without
        # surrounding whitespace and matched **315** rows by sliding along URL path segments.
        "console sign-in login": (
            r"(?i)https?://\S*(?:signin|sign-in|/login|console)\S*\s+(?:[-–—:]\s+)?"
            r"[A-Za-z][A-Za-z0-9._@-]{2,30}(?:[ \t]+[/|][ \t]+|[ \t]*\n\s*)"
            r"(?=[^\s/|]{6,40})(?=[^\s/|]*[A-Za-z])(?=[^\s/|]*\d)"
            r"(?=[^\s/|]*[!#$%&*+=?^~()\[\]{}<>])[^\s/|]+"
        ),
        "ID/PW labelled login": (
            r"(?i)\bID\s*/\s*PW\b[^\n]{0,40}?\bis\b\s+"
            r"[^\s/|]{2,30}[ \t]*[/|][ \t]*[^\s/|.,;]{3,40}"
        ),
        "CJK password label": (
            r"(?:初始密码|登录密码|登陆密码|账号密码|帳號密碼|密码|密碼|口令)\s*[:：=]\s*[^\s\"',;，。；]{3,40}"
        ),
        # Added 2026-08-17 (Q-042). The first entries here that are not credentials: a labelled bank
        # wire block authorises payment rather than authenticating. Same rule as every entry since
        # CREDENTIAL_PAIR — these SHOULD fail until `redact_corpus --redo --commit` is re-run, and
        # must not be weakened to get green.
        #
        # Measured before shipping: **5 rows in 4 chats**, all Telegram, all inbound. The account
        # pattern here is deliberately NOT the recogniser's — the recogniser gates it on a payment
        # anchor anywhere in the message, and a sweep cannot express that per-pattern, so this one
        # requires the routing label and the account label to sit within 200 characters of each
        # other. Broadened to "account number" alone it would report 138 rows, 133 of them SendGrid
        # customer ids, Meta ad-account numbers and Rackspace accounts.
        "bank routing number": r"(?i)\b(?:routing|aba|rtn|ach)\b[^\n]{0,30}?\b\d{9}\b",
        "bank account in a wire block": (
            r"(?i)\b(?:account|acct|a/c)\s*(?:number|no\.?|#)\s*[:#=]?[ \t]*"
            r"(?=(?:[\d \t-]*\d){6,})\d[\d \t-]{4,22}\d"
            r"[\s\S]{0,200}?\b(?:routing|aba|rtn|ach)\b"
        ),
    }
    compiled = {k: re.compile(v) for k, v in patterns.items()}
    found: dict[str, int] = dict.fromkeys(patterns, 0)
    # Masked evidence. A bare count tells you a leak exists but not whether the recogniser is
    # over-firing, and judging that is exactly when you must NOT print the match: redaction is
    # at rest, so acting on a count alone risks destroying real rows permanently. Show the
    # left-hand (account) side and the SHAPE of the right-hand side, never its characters.
    samples: dict[str, list[str]] = {k: [] for k in patterns}
    for (body,) in conn.execute("SELECT body_redacted FROM events WHERE body_redacted <> ''"):
        for name, rx in compiled.items():
            m = rx.search(body)
            if not m:
                continue
            found[name] += 1
            if len(samples[name]) < 12:
                samples[name].append(_masked(m.group(0)))
    detail = {k: v for k, v in samples.items() if v}
    assert not any(found.values()), (
        f"credential shapes survived redaction: {found}\nmasked evidence: {detail}"
    )


@requires_corpus
def test_every_row_is_redacted(conn: sqlite3.Connection) -> None:
    assert conn.execute("SELECT count(*) FROM events WHERE redacted_at IS NULL").fetchone()[0] == 0


@requires_corpus
def test_withheld_channels_emit_nothing(conn: sqlite3.Connection) -> None:
    """A non-admitted channel must be absent from every emitting query."""
    withheld = conn.execute(
        "SELECT chat_key FROM channels WHERE disposition NOT IN ('work','mixed') LIMIT 1"
    ).fetchone()
    if withheld is None:
        pytest.skip("no withheld channel to test against")
    n = conn.execute(
        f"SELECT count(*) FROM events e WHERE {corpus_cli.EMITTABLE} AND e.chat_key = ?",
        (withheld[0],),
    ).fetchone()[0]
    assert n == 0


@requires_corpus
def test_fts_indexes_the_redacted_column_not_the_raw_one(conn: sqlite3.Connection) -> None:
    """A search snippet is a read path; indexing `body` would leak what redaction removed."""
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='events_fts'"
    ).fetchone()[0]
    assert "body_redacted" in ddl
    assert not re.search(r"\bbody\b(?!_redacted)", ddl)


@requires_corpus
def test_outbound_covers_every_channel_despite_volume_skew(conn: sqlite3.Connection) -> None:
    """The F12 regression guard.

    Graph outbound is 206 messages against Telegram's 18,236 in this corpus. A global
    most-recent-N ordering returns zero Graph, which is the bug that produced the
    `self-model-spans-every-channel` prior. Assert the skew still exists (so the guard stays
    meaningful) and that a per-channel quota reaches both sides.
    """
    counts = dict(
        conn.execute(
            f"SELECT channel, count(*) FROM events e WHERE {corpus_cli.EMITTABLE} "
            "AND direction='outbound' GROUP BY 1"
        )
    )
    assert len(counts) >= 2, f"expected outbound on both channels, got {counts}"
    for channel in counts:
        got = conn.execute(
            f"SELECT count(*) FROM (SELECT 1 FROM events e WHERE {corpus_cli.EMITTABLE} "
            "AND direction='outbound' AND channel=? ORDER BY observed_at DESC LIMIT 40)",
            (channel,),
        ).fetchone()[0]
        assert got > 0, f"per-channel quota returned nothing for {channel}"


# --------------------------------------------------- relevance is not the fence (2026-08-14)


def _args(**kw: object) -> argparse.Namespace:
    base = dict(json=False, preview=0, include_suppressed=False, since=None, until=None)
    return argparse.Namespace(**{**base, **kw})


@requires_corpus
def test_outbound_excludes_what_he_forwarded_rather_than_wrote(
    conn: sqlite3.Connection, capsys
) -> None:
    """`outbound` answers "how does he write", so a message he relayed is a wrong answer.

    Measured on the first four weeks of instrumented ingest: 47 of 372 outbound messages were
    forwards — 12.6%. The historical estimate was 44 rows across 26 months, because the only
    way to detect one without `forward_from` was to notice him greeting himself by name. So
    the defect was roughly an order of magnitude larger than the floor it was measured at, and
    the floor was the only number anyone had.

    Two-sided on purpose: the filter must exclude, and it must be visibly overridable, because
    the history it cannot clean is exactly what someone will want to look at.
    """
    if not corpus_cli.tracks_forwards(conn):
        pytest.skip("corpus predates live ingest")

    args = argparse.Namespace(
        per_channel=9999, min_length=0, since="2026-07-18", until=None, json=True,
        include_suppressed=False, preview=0, include_forwarded=False,
    )
    corpus_cli.cmd_outbound(conn, args)
    clean = json.loads(capsys.readouterr().out)

    args.include_forwarded = True
    corpus_cli.cmd_outbound(conn, args)
    everything = json.loads(capsys.readouterr().out)

    assert len(everything) > len(clean), (
        "no forwarded outbound rows at all — either ingest stopped capturing `forward_from`, "
        "or this window has none and the guard is no longer proving anything"
    )


def test_suppression_is_not_part_of_the_fence() -> None:
    """`suppressed` marks noise; `EMITTABLE` marks safety. Merging them would let a widened
    noise rule widen the fence, which is the F26 shape (one flag, two meanings, no consumers).
    """
    assert "suppressed" not in corpus_cli.EMITTABLE
    assert corpus_cli.RELEVANT == "e.suppressed=0"
    assert corpus_cli.relevance(_args()) == corpus_cli.RELEVANT
    assert corpus_cli.relevance(_args(include_suppressed=True)) == "1=1"


@requires_corpus
def test_enrichment_columns_are_detected_not_assumed(conn: sqlite3.Connection, capsys) -> None:
    """A read path that hard-codes the Graph-enrichment columns cannot open a corpus that
    predates them. `mail` must say so and exit non-zero rather than raise an OperationalError,
    and the body verbs must compose those columns in only when they exist.
    """
    has = corpus_cli.enriched(conn)
    sql, fields = corpus_cli.mail_columns(conn)
    assert bool(sql) == has
    assert fields == (("subject", "to", "body_source") if has else ())
    # `body_source` is plumbing, not a field for the reader: `shape()` consumes it to decide
    # `cut` and pops it. It must never survive into an emitted row.
    assert "body_source" not in corpus_cli.shape(
        [{"channel": "graph", "text": "x", "body_source": "uniqueBody"}], _args()
    )[0]

    if not has:
        capsys.readouterr()
        rc = corpus_cli.cmd_mail(conn, _args(chat=None, conversation=None,
                                             min_messages=2, limit=5, direction=None))
        assert rc == 1
        assert "neo-fetch" in capsys.readouterr().out


def test_empty_enrichment_fields_are_dropped_not_shown_blank() -> None:
    """Telegram rows have no subject and no recipients. Emitting them as empty columns would
    read as "this message had no subject", which is a different claim from "this channel has
    no such concept".
    """
    assert corpus_cli.trim({"at": "x", "subject": "", "to": None, "text": "hi"}) == {
        "at": "x", "text": "hi"
    }
    kept = corpus_cli.trim({"at": "x", "subject": "Re: budget", "to": '["a@b.com"]'})
    assert kept["subject"] == "Re: budget"


def test_sql_helpers_bind_rather_than_interpolate() -> None:
    """S608 is silenced for the read path, so the reason it is safe needs a guard of its own.

    The fence predicates are interpolated on purpose; caller input never is. A `--since` that
    reached the query as text rather than as a bound `?` would be both an injection and a way
    to smuggle arbitrary SQL past `EMITTABLE`.
    """
    hostile = "2024-01-01' OR '1'='1"
    sql, params = corpus_cli.window(_args(since=hostile, until=hostile))
    assert hostile not in sql
    assert sql.count("?") == len(params) == 2
    assert params[0] == hostile

    assert corpus_cli.relevance(_args()) in ("1=1", corpus_cli.RELEVANT)


@requires_corpus
def test_noise_is_hidden_by_default_and_reachable_on_request(conn: sqlite3.Connection) -> None:
    """The 174-vs-224 defect: `chats` counted with the noise filter, `thread` read without it,
    so a count and its own content disagreed depending on which verb you asked.
    """
    hidden = conn.execute(
        f"SELECT count(*) FROM events e WHERE {corpus_cli.EMITTABLE} AND e.suppressed=1"
    ).fetchone()[0]
    assert hidden > 0, "no suppressed rows — this guard would pass vacuously"

    def n(chat: str, **kw: object) -> int:
        rows = conn.execute(
            f"""SELECT count(*) FROM events e JOIN channels c ON c.chat_key = e.chat_key
                 WHERE {corpus_cli.EMITTABLE} AND {corpus_cli.relevance(_args(**kw))}
                   AND (e.chat_key = ? OR c.display_name = ?)""",
            (chat, chat),
        ).fetchone()[0]
        return rows

    chat = conn.execute(
        f"""SELECT COALESCE(c.display_name, e.chat_key) FROM events e
              JOIN channels c ON c.chat_key = e.chat_key
             WHERE {corpus_cli.EMITTABLE} AND e.suppressed=1
             GROUP BY e.chat_key ORDER BY count(*) DESC LIMIT 1"""
    ).fetchone()[0]
    assert n(chat) < n(chat, include_suppressed=True)


@requires_corpus
def test_outlook_carries_real_bodies_or_says_it_does_not(conn: sqlite3.Connection) -> None:
    """Outlook was 7,582 openings: `bodyPreview`, capped at 255, 82% of rows at the cap.
    `neo-fetch enrich` replaced them with the messages themselves.

    This guard is deliberately two-sided, because the interesting failure is not "enrichment
    is missing" — it is enrichment that half-ran and left the corpus claiming a completeness
    it does not have. So: an un-enriched corpus must still look like previews, and an enriched
    one must not, and a partly-enriched one fails both ways.
    """
    if not corpus_cli.enriched(conn):
        total, capped = conn.execute(
            f"""SELECT count(*), sum(CASE WHEN length(e.body_redacted) >= ? THEN 1 ELSE 0 END)
                  FROM events e WHERE {corpus_cli.EMITTABLE} AND e.channel='graph'""",
            (corpus_cli.GRAPH_PREVIEW_CAP,),
        ).fetchone()
        assert capped / total > 0.5, "un-enriched Outlook should be mostly at the preview cap"
        return

    # Asked through `body_source`, not `enriched_at`. There are now two ways a Graph row can
    # carry a real body — the 2026-08-14 backfill, and `neo-fetch pull`, which fetches the body
    # at ingest and never needs enriching. `enriched_at` was a faithful proxy for "has a real
    # body" only while enrichment was the sole route; asking through it after live ingest landed
    # reported 245 correctly-bodied rows as a partial backfill. The column that answers the
    # actual question is the one that records where the body came from.
    # …or says it does not. A message deleted from the mailbox before the backfill reached it
    # keeps its 255-character preview and is stamped `not_found`; that row is accounted for
    # rather than silently short. The failure this guards against is a row that is short and
    # says nothing about why.
    total, done = conn.execute(
        f"""SELECT count(*),
                   sum(CASE WHEN e.body_source IS NOT NULL
                             OR e.enrich_status = 'not_found' THEN 1 ELSE 0 END)
              FROM events e WHERE {corpus_cli.EMITTABLE} AND e.channel='graph'"""
    ).fetchone()
    assert done == total, (
        f"{total - done} Graph rows carry no real body and no reason for it — a partial "
        "backfill reads as a complete one to every downstream query. "
        "Re-run `neo-fetch enrich --commit`."
    )
    still_capped = conn.execute(
        f"""SELECT count(*) FROM events e
             WHERE {corpus_cli.EMITTABLE} AND e.channel='graph'
               AND e.enrich_status='ok'
               AND length(e.body_redacted) = ?""",
        (corpus_cli.GRAPH_PREVIEW_CAP,),
    ).fetchone()[0]
    assert still_capped / total < 0.05, (
        f"{still_capped}/{total} enriched Graph rows are still exactly at the old 255 cap — "
        "the enrichment ran but did not replace the previews"
    )


def test_cut_is_decided_before_preview_shortens_the_row() -> None:
    """Order-of-operations guard. `cut` describes the corpus; `--preview` describes this read.
    Truncating first would flag every previewed row as cut and make the marker meaningless.
    """
    long_graph = [{"channel": "graph", "text": "x" * corpus_cli.GRAPH_PREVIEW_CAP}]
    out = corpus_cli.shape(long_graph, _args(preview=40))
    assert out[0]["cut"] is True
    assert len(out[0]["text"]) < 60

    short_graph = corpus_cli.shape([{"channel": "graph", "text": "brief"}], _args(preview=40))
    assert "cut" not in short_graph[0]

    telegram = [{"channel": "telegram", "text": "y" * 400}]
    assert "cut" not in corpus_cli.shape(telegram, _args())[0]


@requires_corpus
def test_metadata_verbs_emit_no_body(conn: sqlite3.Connection, capsys) -> None:
    """`roster` and `timeline` exist to answer "who" and "when" without paying for "what".
    If either ever grows a body column it stops being the cheap half of the read.
    """
    chat = conn.execute(
        f"""SELECT COALESCE(c.display_name, e.chat_key) FROM events e
              JOIN channels c ON c.chat_key = e.chat_key
             WHERE {corpus_cli.EMITTABLE} GROUP BY e.chat_key
             ORDER BY count(*) DESC LIMIT 1"""
    ).fetchone()[0]
    for fn in (corpus_cli.cmd_roster, corpus_cli.cmd_timeline):
        capsys.readouterr()
        assert fn(conn, _args(chat=chat, json=True)) == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows, f"{fn.__name__} returned nothing for {chat!r}"
        assert not any("text" in r for r in rows), f"{fn.__name__} emitted a body"


@requires_corpus
def test_sample_reaches_past_the_most_recent_window(conn: sqlite3.Connection, capsys) -> None:
    """`--limit` can only ever see the tail. On a 5,781-message channel the opening and the
    middle were unreachable, which is how a read pass mistakes the last month for the project.
    """
    chat = conn.execute(
        f"""SELECT COALESCE(c.display_name, e.chat_key) FROM events e
              JOIN channels c ON c.chat_key = e.chat_key
             WHERE {corpus_cli.EMITTABLE} AND e.suppressed=0 GROUP BY e.chat_key
             ORDER BY count(*) DESC LIMIT 1"""
    ).fetchone()[0]

    def span(**kw: object) -> tuple[str, str]:
        capsys.readouterr()
        corpus_cli.cmd_thread(conn, _args(chat=chat, json=True, direction=None, **kw))
        rows = json.loads(capsys.readouterr().out)
        return rows[0]["at"], rows[-1]["at"]

    tail_first, _ = span(limit=40, sample=0)
    sample_first, _ = span(limit=40, sample=40)
    assert sample_first < tail_first, "sampling did not reach earlier than the most recent n"


# ------------------------------------------------- search must not lie about absence (08-15)


def _search_args(query: str, **kw: object) -> argparse.Namespace:
    base = dict(limit=20, chat=None, sender=None, direction=None,
                full=False, substring=False, json=True)
    return _args(query=query, **{**base, **kw})


def test_like_wildcards_in_a_query_are_escaped_not_honoured() -> None:
    """A search for `100%` must find `100%`, not "100 followed by anything".

    The substring path hands the query to LIKE, whose own wildcards would otherwise turn a
    literal query into a pattern — a false *positive* in the one tool whose output gets quoted
    back to people.
    """
    assert corpus_cli.like_pattern("100%") == r"%100\%%"
    assert corpus_cli.like_pattern("a_b") == r"%a\_b%"
    assert corpus_cli.like_pattern(r"c:\tmp") == "%c:\\\\tmp%"


@requires_corpus
def test_a_query_fts5_cannot_parse_falls_back_instead_of_crashing(
    conn: sqlite3.Connection, capsys
) -> None:
    """Ordinary prose is not valid FTS5, and a read verb must not traceback on it.

    The query is a bound parameter, so this was never an injection — but FTS5 parses the
    *value* as a query expression, and `cost-benefit`, `AWS-2` and `1-5` each raised
    `sqlite3.OperationalError: no such column: <tail>` straight out of the verb. Hyphenated
    terms in a corpus of cost-benefit comparisons and instance types are not an edge case.

    The fallback re-reads the query as quoted literal terms, keeping FTS5's implicit AND and
    losing its operators — so it is a fallback, not the default: a query that parses keeps
    its full power. **Both branches must say which one ran.** Reinterpreting a query and
    reporting the result as though nothing happened is the same defect as the CJK false
    negative, one layer up.

    Found 2026-08-16 when an evaluation session's `search 1-5` returned a traceback.
    """
    def run(query: str, **kw: object) -> tuple[str, str]:
        capsys.readouterr()
        corpus_cli.cmd_search(conn, _args(
            query=query, limit=3, substring=False, full=False, chat=None, sender=None,
            direction=None, sample=0, **kw
        ))
        cap = capsys.readouterr()
        return cap.out, cap.err

    for hostile in ("cost-benefit", "AWS-2", "1-5", "a OR", "NEAR("):
        out, err = run(hostile)
        assert "Traceback" not in out + err
        assert "re-read as literal terms" in err, f"{hostile!r} fell back without saying so"

    # A query FTS5 accepts must NOT be rewritten — the operators still have to work.
    _, err = run("Hetzner OR Lightsail")
    assert "re-read as literal terms" not in err
    _, err = run("beehiiv*")
    assert "re-read as literal terms" not in err


def test_unsegmented_script_detection_routes_only_what_it_must() -> None:
    """CJK and kana route to the scan; Latin and Hangul keep the index.

    Korean is space-delimited, so `unicode61` segments it correctly and it keeps FTS ranking —
    routing it to the scan would trade a working path for a slower one.
    """
    assert corpus_cli.UNSEGMENTED.search("付费IDE")
    assert corpus_cli.UNSEGMENTED.search("ありがとう")
    assert not corpus_cli.UNSEGMENTED.search("beehiiv migration")
    assert not corpus_cli.UNSEGMENTED.search("크리스천 투데이")


@requires_corpus
def test_search_reaches_inside_an_unsegmented_chinese_token(
    conn: sqlite3.Connection, capsys
) -> None:
    """The 2026-08-15 defect: FTS could not see *inside* a Chinese run, and said so as absence.

    `unicode61` treats a whitespace-delimited CJK run as one token, so only an exact match on
    the entire run hit. `search 付费` returned "(no matches in admitted channels)" while
    `付费IDE的licence还是有点贵了` sat in the corpus — and a knowledge base built by search
    reads that as *he never said this*. More than half his register is Mandarin, so this is
    the highest-consequence false negative the sensor can produce.

    Both halves are asserted: that the index genuinely still cannot find the substring (or the
    guard would pass vacuously the day the tokenizer changes), and that the reader can.
    """
    run = re.compile(r"[\u4e00-\u9fff]{6,}")
    needle = None
    for (body,) in conn.execute(
        f"""SELECT e.body_redacted FROM events e
             WHERE {corpus_cli.EMITTABLE} AND {corpus_cli.RELEVANT} LIMIT 20000"""
    ):
        m = run.search(body)
        if m:
            # From INSIDE the run, so it is a proper substring of its token, never the token.
            needle = m.group(0)[1:5]
            break
    if needle is None:
        pytest.skip("no Chinese in the visible corpus to test against")

    missed = conn.execute(
        f"""SELECT count(*) FROM events_fts f JOIN events e ON e.rowid = f.rowid
             WHERE events_fts MATCH ? AND {corpus_cli.EMITTABLE}""",
        (needle,),
    ).fetchone()[0]
    assert missed == 0, f"the index found {needle!r} — this guard is no longer guarding"

    capsys.readouterr()
    assert corpus_cli.cmd_search(conn, _search_args(needle)) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows, f"substring scan missed {needle!r}, which the corpus demonstrably contains"
    assert all(needle in r["text"] for r in rows), "a hit that does not contain the query"


@requires_corpus
def test_the_substring_scan_stays_behind_the_fence(conn: sqlite3.Connection, capsys) -> None:
    """A second read path is a second way through the fence if it forgets the predicate.

    The scan bypasses `events_fts` entirely, so nothing about the index protects it: it has to
    compose `EMITTABLE` itself, and this asserts that it does, on data rather than on source.
    """
    admitted = {
        r[0] for r in conn.execute(
            f"""SELECT DISTINCT COALESCE(c.display_name, e.chat_key)
                  FROM events e JOIN channels c ON c.chat_key = e.chat_key
                 WHERE {corpus_cli.EMITTABLE}"""
        )
    }
    capsys.readouterr()
    corpus_cli.cmd_search(conn, _search_args("e", substring=True, limit=200))
    rows = json.loads(capsys.readouterr().out)
    assert rows, "the broadest possible substring returned nothing — guard is vacuous"
    assert {r["chat"] for r in rows} <= admitted, "the scan emitted a non-admitted chat"


@requires_corpus
def test_an_empty_read_says_which_of_the_two_reasons_it_was(
    conn: sqlite3.Connection, capsys
) -> None:
    """"Not admitted" and "your filters excluded everything" are different facts.

    `thread 'A. Colleague' --direction outbound` — 127 admitted messages he never replies
    in, which is itself a finding — used to report the channel as withheld. An agent that
    believes that stops looking, or reports a fence where there is none.

    The not-found branch must STAY ambiguous between "does not exist" and "is not admitted":
    separating them confirms a withheld channel exists, and existence is one of the things the
    fence withholds.
    """
    chat = conn.execute(
        f"""SELECT COALESCE(c.display_name, e.chat_key) FROM events e
              JOIN channels c ON c.chat_key = e.chat_key
             WHERE {corpus_cli.EMITTABLE} AND {corpus_cli.RELEVANT}
             GROUP BY e.chat_key ORDER BY count(*) DESC LIMIT 1"""
    ).fetchone()[0]

    def read(**kw: object) -> str:
        capsys.readouterr()
        corpus_cli.cmd_thread(conn, _args(limit=20, sample=0, direction=None, **kw))
        return capsys.readouterr().out

    unknown = read(chat="no chat has ever been called this")
    assert "no such chat, or it is not admitted" in unknown
    assert "admitted and holds" not in unknown, "existence of a withheld chat must stay unsaid"

    filtered = read(chat=chat, since="2099-01-01")
    assert "admitted and holds" in filtered
    assert "--since 2099-01-01" in filtered
    assert "no such chat" not in filtered


def test_a_malformed_window_bound_is_rejected_rather_than_ignored() -> None:
    """A date filter that cannot parse must fail loudly, not stop existing.

    `window()` compares `observed_at` *lexically* against ISO-8601 text, so an unvalidated
    bound does not error — it silently changes what the query means:

      `--until garbage` -> `observed_at <= 'garbageT99'`, and every date sorts below 'g',
      so the whole corpus comes back looking like a filtered window.
      `--since garbage`  -> `observed_at >= 'garbage'`, true for no row, which prints as
      *there is no traffic in that window*.

    Same family as the CJK search and the emptied `thread` filter: the tool makes a false
    claim about scope and nothing downstream can see it. It matters more than a typo
    because things consume this bound as a *boundary* — `harness/shim/neo-corpus` clamps a
    graded session with `--until` so it cannot read the traffic its scenario came from, and
    an inert clamp means every run silently grades against the answer key.

    Found 2026-08-16 while wiring that clamp.
    """
    parser = corpus_cli.build_parser()

    for bad in ("garbage", "2026-13-45", "2026-08-12x", "08/12/2026", ""):
        with pytest.raises(SystemExit):
            parser.parse_args(["recent", "--until", bad])
        with pytest.raises(SystemExit):
            parser.parse_args(["recent", "--since", bad])

    # The forms that do work must keep working — including the sub-day bound, which the
    # lexical comparison supports correctly and the harness relies on.
    for good in ("2026-08-12", "2026-08-12T14", "2026-08-12T14:52", "2026-08-12T14:52:00"):
        assert parser.parse_args(["recent", "--until", good]).until == good


def test_a_rejected_bound_would_otherwise_have_widened_the_window() -> None:
    """Guard the guard: prove the unvalidated bound really was a no-op.

    Without this, the validation above could be protecting against nothing and nobody would
    know — the flag-with-no-consumers shape one more time. This asserts the underlying SQL
    comparison behaves as claimed, so the test fails if someone swaps the lexical window for
    a typed one and leaves the validation in place as decoration.
    """
    args = argparse.Namespace(since=None, until="garbage")
    sql, params = corpus_cli.window(args)
    assert params == ["garbageT99"]
    assert "2026-08-16T12:00" <= params[0], "a real timestamp must sort below the junk bound"

    args = argparse.Namespace(since="garbage", until=None)
    _, params = corpus_cli.window(args)
    assert not ("2026-08-16T12:00" >= params[0]), "junk lower bound must exclude every row"


@requires_corpus
def test_a_display_name_shared_by_two_chats_is_flagged_not_silently_unioned(
    conn: sqlite3.Connection, capsys
) -> None:
    """A display name is not a key, and reading two chats as one invents evidence.

    `thread`/`roster` resolve `chat_key OR display_name`. Where a name is shared — `Sam Lee`
    is a 5,721-row Telegram 1:1 AND a 10-row Graph chat — one call reads both with no column
    saying so. That produced a false provenance line in two person files ("the same 1:1 carries
    both identifiers"), which is worse than a wrong answer: it is a manufactured *reason*.
    """
    dupe = conn.execute(
        f"""SELECT c.display_name FROM channels c
             WHERE c.disposition IN {corpus_cli.VISIBLE} AND c.display_name IS NOT NULL
             GROUP BY c.display_name HAVING count(DISTINCT c.chat_key) > 1 LIMIT 1"""
    ).fetchone()
    if dupe is None:
        pytest.skip("no shared display name in this corpus")

    capsys.readouterr()
    corpus_cli.warn_if_name_is_ambiguous(conn, dupe[0])
    warned = capsys.readouterr().err
    assert "names 2 different chats" in warned or "different chats" in warned
    assert "chat_key" in warned, "the warning must say how to read just one"

    unique = conn.execute(
        f"""SELECT c.display_name FROM channels c
             WHERE c.disposition IN {corpus_cli.VISIBLE} AND c.display_name IS NOT NULL
             GROUP BY c.display_name HAVING count(DISTINCT c.chat_key) = 1 LIMIT 1"""
    ).fetchone()[0]
    capsys.readouterr()
    corpus_cli.warn_if_name_is_ambiguous(conn, unique)
    assert capsys.readouterr().err == "", "an unambiguous name must not be warned about"


# --------------------------------------------------------------- redaction policy


def test_identity_passes_through_but_credentials_do_not() -> None:
    """Identity must survive: translate cannot calibrate register without knowing the recipient."""
    text = "Carol (carol@example.com) asked about the access key rotation"
    assert redact(text).text == text

    leaky = 'creds AKIAIOSFODNN7EXAMPLE "secretAccessKey":"wJalrXUtnFEMI/K7MDENGbPxRfiCYEXAMPLEK"'
    out = redact(leaky).text
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "wJalrXUtnFEMI" not in out


def test_credential_label_and_value_are_masked_together() -> None:
    """The F26-adjacent bug: masking the label while publishing the secret.

    The removed `PASSWORD` recogniser matched label and value as separate candidates, and
    overlap resolution kept whichever sorted first — turning `| Password: zqx2026` into
    `| <PASSWORD>: zqx2026`. One span over both halves cannot fail that way.
    """
    out = redact("Dashboard: bit.ly/3AbCdEf | Password: zqx2026").text
    assert "zqx2026" not in out
    assert "CREDENTIAL_ASSIGNMENT" in out


def test_unlabelled_account_and_secret_are_masked_together() -> None:
    """The 2026-08-14 leak: a login shared the way a person actually shares one.

    A live bit.ly password reached the agent verbatim through `neo-corpus` because every
    recogniser needed something this message did not have — CREDENTIAL_ASSIGNMENT needs a label
    word before a `:`/`=`, CLOUD_SECRET needs a self-identifying key name, URL_WITH_CREDENTIALS
    needs a URL. Here the "label" is an email address and the separator is a slash, so nothing
    matched at all. It was never a scoring failure, which is why no threshold tuning would have
    closed it.

    The value below is a structural stand-in, not the leaked secret.
    """
    out = redact("Bit.ly alice@example.com/ Bluebird777!!!").text
    assert "Bluebird777" not in out, "the secret survived redaction"
    assert "CREDENTIAL_PAIR" in out
    # Both halves in one span: masking the account while publishing the secret is the
    # failure mode CREDENTIAL_ASSIGNMENT was reshaped to avoid, and this must not reintroduce it.
    assert "alice@example.com" not in out


def test_credential_pair_variants() -> None:
    """Separator and spacing vary; the shape does not."""
    for text in (
        "login is operator@example.com / Tr0ub4dor",
        "bitly a@b.com|Passw0rd!",
        "account bob@lawfirm.example/Hunter7chars",
    ):
        assert "CREDENTIAL_PAIR" in redact(text).text, f"missed credential pair: {text!r}"


def test_credential_pair_does_not_fire_on_ordinary_email_adjacency() -> None:
    """An email followed by a slash is overwhelmingly not a credential.

    Redaction runs at rest, so a false positive here is permanent corpus damage — these are the
    shapes the value test must reject.
    """
    for text in (
        "Folder is shared with ceo@example.com/ 2026 archive",  # value has no letter
        "write to sara@newsletter.example | Media Trends",  # value has no digit or special
        "see https://user@example.com/path1 for the staging build",  # URL userinfo
        "mailto:dana.writer@example.com: Re: FYI",  # mailto, and no secret shape
        "unsubscribe at noreply@x.com/unsubscribe?id=123abc",  # URL tail, not a secret
        "repo is git@github.com:someuser/somerepo",  # scp-style git remote
        # The only false positives measured on the real corpus were scp-style remotes whose
        # org segment mixes letters and digits. `:` was dropped as a separator because of this.
        "clone git@github.com:km2024/site1.git",
    ):
        assert redact(text).text == text, f"redaction damaged ordinary content: {text!r}"


def test_prose_and_urls_are_not_shredded() -> None:
    """Measured false positives from the removed context-boosted recognisers."""
    for text in (
        "It seems they detect the ip address when I tried to reset pw in my house earlier.",
        "We need to login Amazon affilate center to check earning report",
        "will the keywords show up on https://feeds.transistor.fm/tcpdaily",
        "[audio: Rethinking_Key_Hypotheses_refined.wav]",
        "https://www.amazon.com/Real-Life-MBA/dp/0062362801/ref=sr_1_1",
    ):
        assert redact(text).text == text, f"redaction damaged ordinary content: {text!r}"


def test_credit_card_is_not_redacted() -> None:
    """Removed after measuring a 100% false-positive rate over the full corpus.

    Luhn validates Meta ad-account numbers, so it masked a real ad-revenue fact on 79 visible
    rows and caught no card. See the note in `redaction.py`.
    """
    assert "CREDIT_CARD" not in REDACT_ENTITIES
    text = "Acme News Ad Account (123456789012347) receipt"
    assert redact(text).text == text


@requires_corpus
def test_a_never_ruled_channel_has_no_stored_messages(conn: sqlite3.Connection) -> None:
    """`undecided` means never ruled, and a never-ruled conversation is counted, not stored.

    Added 2026-08-24, against the live store, after a second pull wrote 15 rows from four
    never-ruled conversations — one of them a family chat. The never-ruled test read
    `disposition is None`, which is true on the first pull and false on every one after it,
    because `register_pending` itself writes the row as `undecided`. So the rule applied exactly
    once per chat and then silently stopped applying.

    `undecided` still withheld those rows from being READ, and that is precisely why this test
    belongs in the fence suite rather than in `test_ingest.py`. Withheld-in-the-database is the
    shape `register_pending` exists to refuse: the fence decides what an agent may read, and it
    was never meant to be the thing standing between his family chat and a database. A unit test
    can pin the predicate; only a query against the real store can say the store is clean.

    Repair verb if this ever fails again: `neo-fetch withdraw-pending` (dry), then `--commit`.
    """
    rows = conn.execute(
        """SELECT c.chat_key, c.display_name, count(e.rowid)
             FROM channels c JOIN events e ON e.chat_key = c.chat_key
            WHERE c.disposition = 'undecided'
            GROUP BY c.chat_key ORDER BY count(e.rowid) DESC"""
    ).fetchall()
    assert not rows, (
        "never-ruled conversations have stored messages: "
        + "; ".join(f"{k} ({d or 'no name'}) {n} row(s)" for k, d, n in rows)
        + " — run `neo-fetch withdraw-pending --commit`"
    )


# ------------------------------ a limited read must say it is a tail (2026-08-26, F-011)


@requires_corpus
def test_a_limited_thread_read_says_it_is_a_tail(conn: sqlite3.Connection, capsys) -> None:
    """`--limit` returns the newest n and used to print a result shaped like a whole one.

    **The failure this pins.** A session read a 320-message thread with `--limit 200`, found no
    instance of a piece of feedback in the newest 200, and told the operator they had *never* given
    it. They had, several times, mostly in the unread 120. `cmd_thread`'s docstring had warned
    about exactly this since it was written — documenting a trap where the reader is not looking is
    not a mitigation. The note has to arrive attached to the result.

    Asserted on the note's *substance*, not its wording: the two counts and the word that denies
    completeness. A future rephrasing is free; dropping the numbers or the denial is not.
    """
    chat, total = conn.execute(
        f"""SELECT e.chat_key, count(*) FROM events e
              JOIN channels c ON c.chat_key = e.chat_key
             WHERE {corpus_cli.EMITTABLE} AND e.suppressed=0 GROUP BY e.chat_key
             HAVING count(*) > 20 ORDER BY count(*) DESC LIMIT 1"""
    ).fetchone()

    capsys.readouterr()
    corpus_cli.cmd_thread(conn, _args(chat=chat, direction=None, limit=5, sample=0))
    err = capsys.readouterr().err
    assert "NEWEST 5" in err, f"a truncated read did not say what it dropped: {err!r}"
    assert str(total) in err, f"the note did not name the full matching count {total}: {err!r}"
    assert "never" in err, "the note must deny that absence can be read from a tail"


@requires_corpus
def test_a_complete_read_stays_silent(conn: sqlite3.Connection, capsys) -> None:
    """The note is a warning, not a banner. A read that dropped nothing must not carry it —
    a caveat printed on every result is a caveat nobody reads, which is how the original
    docstring warning failed."""
    chat = conn.execute(
        f"""SELECT e.chat_key FROM events e JOIN channels c ON c.chat_key = e.chat_key
             WHERE {corpus_cli.EMITTABLE} AND e.suppressed=0 GROUP BY e.chat_key
             ORDER BY count(*) ASC LIMIT 1"""
    ).fetchone()[0]

    capsys.readouterr()
    corpus_cli.cmd_thread(conn, _args(chat=chat, direction=None, limit=10_000, sample=0))
    assert "NEWEST" not in capsys.readouterr().err


@requires_corpus
def test_the_tail_warning_can_actually_fail(conn: sqlite3.Connection) -> None:
    """The check above is worthless if `truncation_note` can only ever return a string.

    Pinned because this suite has already shipped a check that fired on correct behaviour and
    another that could not fail at all. `shown < limit` is the whole guard, so exercise both
    sides of it against the real store.
    """
    sql = f"""SELECT 1 FROM events e JOIN channels c ON c.chat_key = e.chat_key
               WHERE {corpus_cli.EMITTABLE}"""
    assert corpus_cli.truncation_note(conn, sql, (), limit=5, shown=5) != ""
    assert corpus_cli.truncation_note(conn, sql, (), limit=5, shown=4) == ""


@requires_corpus
def test_search_and_recent_carry_the_same_warning(conn: sqlite3.Connection, capsys) -> None:
    """The false "never" came from `thread`, but the pattern-hunting that fed it was `search
    --limit 40`. Fixing only the verb that happened to be caught is how F-007 recurred on a
    second surface after the first was closed."""
    capsys.readouterr()
    corpus_cli.cmd_search(conn, _search_args("the", limit=3))
    assert "NEWEST 3" in capsys.readouterr().err, "search does not report its truncation"

    corpus_cli.cmd_recent(conn, _args(limit=3, chat=None))
    assert "NEWEST 3" in capsys.readouterr().err, "recent does not report its truncation"


@requires_corpus
def test_sampling_is_not_warned_about(conn: sqlite3.Connection, capsys) -> None:
    """`--sample` spans the whole range by construction, so it drops nothing a reader could
    mistake for absence. Warning there would train the reader to ignore the line."""
    chat = conn.execute(
        f"""SELECT e.chat_key FROM events e JOIN channels c ON c.chat_key = e.chat_key
             WHERE {corpus_cli.EMITTABLE} AND e.suppressed=0 GROUP BY e.chat_key
             ORDER BY count(*) DESC LIMIT 1"""
    ).fetchone()[0]

    capsys.readouterr()
    corpus_cli.cmd_thread(conn, _args(chat=chat, direction=None, limit=5, sample=5))
    assert "NEWEST" not in capsys.readouterr().err
