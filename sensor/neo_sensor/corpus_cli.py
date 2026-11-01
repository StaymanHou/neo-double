"""`neo-corpus` — the only read path from the corpus to the agent.

The fence has two dimensions, and both are enforced here by construction rather than by
convention, because convention is exactly what failed before (F26: the egress gate's flag had
zero consumers while real AWS keys went out verbatim).

**Content.** Every row-emitting query selects `body_redacted` and requires
`redacted_at IS NOT NULL`. The raw `body` column is never named in this module — asserted by
`tests/test_fence.py`, which greps this source. Redaction already ran, at write time.

**Scope.** Every row-emitting query is filtered to `disposition IN ('work','mixed')`.
Non-admitted channels are invisible: not their bodies, not their names, not their counts.
`undecided` is withheld, so a channel nobody has ruled on cannot leak by default.

The connection is opened **read-only** at the SQLite level, so this command cannot mutate the
corpus even if a future edit tried to. Changing a disposition is `neo-channels`, a separate
command deliberately left OUT of the agent's permission allowlist: every invocation therefore
raises the harness's own approval prompt, and that prompt *is* the operator confirmation gate.

---

**The Outlook half, and what it used to be.** Until 2026-08-14 the Graph ingest stored only
`bodyPreview` — capped at 255 characters, 82% of rows at the cap — with `subject` and
`toRecipients` fetched and dropped and `conversationId` never requested. Outlook was 7,582
openings with no addressee and no threading. `neo-fetch enrich` re-fetched every row from the
mailbox by `external_id` (which IS the Graph message id) and filled all of it in. What that
changes for a reader:

* `subject` and `to` appear on Outlook rows; both are absent on Telegram, where the concepts do
  not exist, and are omitted rather than shown blank so an empty field is never mistaken for a
  message that had no subject.
* Bodies are the message, preferring Graph's `uniqueBody` — the part its author wrote, without
  the quoted thread beneath it. `cut=True` now means *this command capped an enormous body*,
  not *the ingest truncated it*; length no longer carries that information and `body_source`
  does.
* `mail` groups Outlook by `conversation_id`, which is the only way a reply sits next to what
  it answered.

**`chat_key` was NOT re-keyed to the conversation**, and that constraint outlives the migration:
it is what the operator's 108 channel dispositions are keyed on. An Outlook chat is therefore
still one *sender*, still entirely inbound for a correspondent, and all outbound mail still
shares one bucket. `outbound`'s chat column is named `chat` and not `to` for that reason —
though a per-row `to` now exists, recovered from the mailbox.

**Search cannot segment Chinese, so it does not pretend to.** The FTS5 index is built with
`tokenize='unicode61'`, which splits on Unicode word boundaries — and Chinese is written
without them, so an entire CJK run between two spaces is *one token*. `search 付费` therefore
returned nothing while `付费IDE的licence还是有点贵了` sat in the corpus verbatim, and it said
so as `(no matches in admitted channels)`, which reads as *he never said this*. In a corpus
where more than half his register is Mandarin that is the worst-shaped false negative
available. A query containing CJK now takes a substring scan over the same redacted column
instead of the index; `--substring` forces it for any query. Measured 2026-08-15.

**Suppression is relevance, not fence.** `suppressed` marks corpus noise — the reasons are
`daily_report`, `stats_report`, `separator`, `service_msg` — inherited wholesale from the
predecessor and never reviewed here. It is therefore deliberately NOT part of `EMITTABLE`:
conflating a relevance judgement with a safety boundary is how a fence stops meaning anything.
Every row-emitting verb hides suppressed rows by default and every one takes
`--include-suppressed`, which also prints `why` so an inherited judgement can be seen rather
than silently obeyed. Note that this hides the operator's own KM Daily Report from `outbound`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

from neo_sensor.config import STATE_DIR

DEFAULT_DB = STATE_DIR / "corpus.db"

# Dispositions whose content may reach the agent.
VISIBLE = ("work", "mixed")

# The emittable predicate. Every query that returns message text composes this, and must
# therefore alias the events table as `e`. Both `events` and `channels` carry a `chat_key`, so
# the columns are qualified — an unqualified predicate is an "ambiguous column name" error the
# moment a query joins the two, which is most of them.
EMITTABLE = (
    "e.redacted_at IS NOT NULL "
    f"AND e.chat_key IN (SELECT chat_key FROM channels WHERE disposition IN {VISIBLE})"
)

DISPOSITIONS = ("work", "mixed", "personal", "ignore", "undecided")

# Relevance, NOT fence — see the module docstring. Kept out of EMITTABLE on purpose so that
# widening what counts as noise can never widen what counts as safe.
RELEVANT = "e.suppressed=0"

# Graph's `bodyPreview` is capped here. A row at the cap was cut by the ingest, not by its
# author, and saying so on the row is cheaper than a reader inferring completeness.
GRAPH_PREVIEW_CAP = 255

# Scripts `unicode61` cannot break into words: CJK ideographs and Japanese kana are written
# without spaces, so the tokenizer hands FTS one token per whitespace-delimited run. Hangul is
# deliberately absent — Korean IS space-delimited, so the index works on it and keeps its
# ranking, which the substring scan does not have.
UNSEGMENTED = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿豈-﫿\U00020000-\U0003ffff]"
)


def connect(db: Path, *, writable: bool = False) -> sqlite3.Connection:
    if not db.exists():
        print(f"corpus not found at {db}", file=sys.stderr)
        raise SystemExit(2)
    if writable:
        return sqlite3.connect(db)
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def enriched(conn: sqlite3.Connection) -> bool:
    """Whether `neo-fetch enrich` has migrated this corpus yet.

    Checked rather than assumed: the enrichment columns arrive with the Graph backfill, and a
    read path that hard-codes them cannot open a corpus that predates it. Every enrichment-only
    column is therefore composed in only when it exists.
    """
    return "conversation_id" in {r[1] for r in conn.execute("PRAGMA table_info(events)")}


def tracks_forwards(conn: sqlite3.Connection) -> bool:
    """Whether this corpus records who wrote a forwarded message.

    Arrives with live ingest. Every row written before 2026-08-15 has it NULL and always will —
    the predecessor never kept `forward_from`, so for historical rows "not a forward" and
    "unknown" are the same value. Read the column as evidence, never as its absence.
    """
    return "forwarded_from" in {r[1] for r in conn.execute("PRAGMA table_info(events)")}


def mail_columns(conn: sqlite3.Connection) -> tuple[str, tuple[str, ...]]:
    """The extra select-list and field names for an enriched corpus; empty for an old one."""
    if not enriched(conn):
        return "", ()
    return ", e.subject_redacted, e.recipients, e.body_source", ("subject", "to", "body_source")


def trim(row: dict) -> dict:
    """Drop the enrichment fields that are empty, so Telegram rows do not sprout blank ones."""
    return {k: v for k, v in row.items() if not (k in ("subject", "to", "fwd") and not v)}


def relevance(args: argparse.Namespace) -> str:
    """The noise filter as a SQL fragment, or a no-op when the reader opts in."""
    return "1=1" if getattr(args, "include_suppressed", False) else RELEVANT


def window(args: argparse.Namespace) -> tuple[str, list]:
    """`--since` / `--until` as a half-open date window.

    `--until` is inclusive of the whole day: comparing against `<date>T99` beats appending a
    time, because `observed_at` is ISO-8601 text and 'T' < '9' orders every same-day timestamp
    below the sentinel whatever its precision.
    """
    sql, params = "", []
    if getattr(args, "since", None):
        sql += " AND e.observed_at >= ?"
        params.append(args.since)
    if getattr(args, "until", None):
        sql += " AND e.observed_at <= ?"
        params.append(f"{args.until}T99")
    return sql, params


def shape(rows: list[dict], args: argparse.Namespace, *, channel: str | None = None) -> list[dict]:
    """Mark ingest-truncated rows, then apply the reader's own `--preview` truncation.

    Order matters: `cut` describes what the *corpus* holds, so it is decided before this
    command shortens anything. Doing it the other way round would report every previewed row
    as truncated by Graph and make the flag worthless.

    `channel` names the channel for verbs whose rows do not each carry one (a single-chat read
    has exactly one, and repeating it on every line is noise).
    """
    for r in rows:
        text = r.get("text")
        if text is None:
            continue
        source = r.pop("body_source", None)
        if source is not None:
            if source.endswith(":capped"):
                r["cut"] = True
        elif (r.get("channel") or channel) == "graph" and len(text) >= GRAPH_PREVIEW_CAP:
            # Un-enriched corpus: length at the cap IS the evidence of truncation. Once
            # `neo-fetch enrich` has run, length says nothing and `body_source` says it all.
            r["cut"] = True
        n = getattr(args, "preview", 0)
        if n and len(text) > n:
            r["text"] = text[:n].rstrip() + " …"
    return rows


def emit(rows: list[dict], as_json: bool, *, empty: str = "(nothing)", note: str = "") -> None:
    """`note` goes to stderr, for the same reason `search`'s `mode:` line does: how complete a
    result is, is provenance for the reader, not data for a parser."""
    if note:
        print(note, file=sys.stderr)
    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if not rows:
        print(empty)
        return
    for r in rows:
        parts = [f"{k}={v!r}" if isinstance(v, str) and "\n" in v else f"{k}={v}"
                 for k, v in r.items() if k != "text"]
        print(" · ".join(parts))
        if "text" in r:
            print(f"    {r['text']}")


def like_pattern(query: str) -> str:
    """`query` as a LIKE argument, with LIKE's own wildcards escaped.

    Without this a search for `100%` or `a_b` would silently become a wildcard search and
    return rows that do not contain the query at all — a false *positive* in a tool whose whole
    job is to be quotable.
    """
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def excerpt(text: str, query: str, *, width: int = 60) -> str:
    """The substring path's stand-in for FTS5 `snippet()` — same «» marking, same elision.

    Written rather than borrowed because `snippet()` is a function of the FTS index, and the
    substring path deliberately does not use one. The marking matters more than it looks: a
    reader who cannot see *which* characters matched cannot tell a real hit from a coincidence
    inside a longer token, which is the exact confusion CJK search creates.
    """
    rx = re.compile(re.escape(query), re.IGNORECASE)
    m = rx.search(text)
    if not m:
        return text
    start, end = max(0, m.start() - width), min(len(text), m.end() + width)
    window = rx.sub(lambda hit: f"«{hit.group(0)}»", text[start:end])
    return f"{'…' if start else ''}{window}{'…' if end < len(text) else ''}"


def warn_if_name_is_ambiguous(conn: sqlite3.Connection, chat: str) -> None:
    """A display name is not a key, and reading two chats as one invents evidence.

    `thread` and `roster` resolve `chat_key OR display_name`, so a name shared by two chats
    reads both at once with no column saying so. That is not hypothetical: `people/sam-hong.md`
    and `people/<someone>.md` both justified a Telegram↔Outlook identity link with "the same 1:1
    carries both identifiers" — and no such 1:1 existed. One unioned `roster` call had made two
    chats look like one thread. Warning is the honest mitigation; silently picking one would
    hide the union instead of naming it, and the name still is not a key.
    """
    rows = conn.execute(
        f"""SELECT c.chat_key, c.channel, count(e.rowid)
              FROM channels c LEFT JOIN events e
                ON e.chat_key = c.chat_key AND {EMITTABLE}
             WHERE c.display_name = ? AND c.disposition IN {VISIBLE}
             GROUP BY c.chat_key ORDER BY count(e.rowid) DESC""",
        (chat,),
    ).fetchall()
    if len(rows) < 2:
        return
    where = ", ".join(f"{key} ({channel}, {n:,})" for key, channel, n in rows)
    print(
        f"warning: {chat!r} names {len(rows)} different chats and this reads ALL of them as "
        f"one — {where}. They are not one conversation; pass a chat_key to read just one.",
        file=sys.stderr,
    )


def why_empty(conn: sqlite3.Connection, args: argparse.Namespace) -> str:
    """A single-chat read returned nothing. Say which of the two reasons it was.

    "No such chat, or it is not admitted" and "this chat is admitted and your filters excluded
    everything" are different facts, and this command used to print the first whatever had
    happened. So `thread 'A. Colleague' --direction outbound` — an admitted 127-message
    thread he never replies in, which is itself a finding — reported the channel as withheld.
    An agent that believes that stops looking, or reports a fence where there is none.

    The not-found branch stays deliberately ambiguous between "does not exist" and "is not
    admitted", because separating those two would confirm the existence of a withheld channel,
    and existence is one of the things the fence withholds.
    """
    n_chats, visible = conn.execute(
        f"""SELECT count(DISTINCT c.chat_key), count(e.rowid)
              FROM channels c
              LEFT JOIN events e ON e.chat_key = c.chat_key
                   AND {EMITTABLE} AND {relevance(args)}
             WHERE (c.chat_key = ? OR c.display_name = ?)
               AND c.disposition IN {VISIBLE}""",
        (args.chat, args.chat),
    ).fetchone()
    if not n_chats:
        return "(no such chat, or it is not admitted — check `neo-corpus chats`)"
    if not visible:
        return (
            "(chat is admitted, but nothing in it is visible: every row is either suppressed "
            "as noise — try `--include-suppressed` — or has not been redacted)"
        )
    applied = [f"--{k.replace('_', '-')} {v}"
               for k in ("since", "until", "direction")
               if (v := getattr(args, k, None))]
    if not getattr(args, "include_suppressed", False):
        applied.append("suppressed rows hidden")
    return (
        f"(chat is admitted and holds {visible:,} visible message(s), but none match this "
        f"read: {', '.join(applied) or 'no filters'}. That is a finding about the chat, not "
        "about your access — `neo-corpus roster` will show you who speaks in it.)"
    )


# --------------------------------------------------------------------------- read verbs


def cmd_stats(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    total, keep = conn.execute(
        "SELECT count(*), sum(CASE WHEN suppressed=0 THEN 1 ELSE 0 END) FROM events"
    ).fetchone()
    vis = conn.execute(f"SELECT count(*) FROM events e WHERE {EMITTABLE}").fetchone()[0]
    lo, hi = conn.execute(
        f"SELECT min(observed_at), max(observed_at) FROM events e WHERE {EMITTABLE}"
    ).fetchone()
    print(f"corpus            {total:,} events ({keep:,} keep-signal)")
    print(f"visible to you    {vis:,} events   range {lo[:10]} .. {hi[:10]}")
    print("\nchannels by disposition:")
    for disp, n in conn.execute(
        "SELECT disposition, count(*) FROM channels GROUP BY 1 ORDER BY 1"
    ):
        mark = "  <- visible" if disp in VISIBLE else ""
        print(f"  {disp:<10} {n:>4}{mark}")
    print("\nvisible traffic by channel:")
    # `body_source`, not `enriched_at`: a row pulled by `neo-fetch pull` has its real body from
    # the moment it is written and is never enriched, so counting enrichments understates
    # coverage the moment live ingest starts — and understating it here reads as "part of
    # Outlook is still previews", which is the opposite of true.
    enrich_note = ", e.body_source" if enriched(conn) else ", NULL"
    for chan, n, done in conn.execute(
        f"""SELECT e.channel, count(*), sum(CASE WHEN x IS NOT NULL THEN 1 ELSE 0 END)
              FROM (SELECT e.channel AS channel, e.body_redacted AS body_redacted
                           {enrich_note} AS x
                      FROM events e WHERE {EMITTABLE} AND {RELEVANT}) AS e
             GROUP BY 1 ORDER BY 2 DESC"""
    ):
        note = ""
        if chan == "graph":
            note = (
                f"  ({done:,} carry a real body)" if done
                else "  (bodies are 255-char previews — run `neo-fetch enrich`)"
            )
        print(f"  {chan:<10} {n:>7,}{note}")

    # Suppression is an inherited judgement, so it is reported rather than merely applied:
    # `daily_report` in this list is the operator's own standing report being filtered out.
    print("\nhidden as noise  (`--include-suppressed` on any verb to see them):")
    for reason, n in conn.execute(
        f"""SELECT COALESCE(e.suppressed_reason,'(unlabelled)'), count(*)
              FROM events e WHERE {EMITTABLE} AND e.suppressed=1 GROUP BY 1 ORDER BY 2 DESC"""
    ):
        print(f"  {reason:<16} {n:>7,}")

    pend = conn.execute(
        "SELECT count(*) FROM channels WHERE disposition='undecided'"
    ).fetchone()[0]
    if pend:
        print(f"\n{pend} channel(s) awaiting a disposition — see `neo-corpus channels-pending`")
    return 0


def cmd_chats(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Chat-level grouping: one row per conversation."""
    where, params = window(args)
    rows = [
        dict(zip(("chat", "channel", "messages", "first", "last"), r, strict=True))
        for r in conn.execute(
            f"""SELECT COALESCE(c.display_name, e.chat_key), e.channel, count(*),
                       substr(min(e.observed_at),1,10), substr(max(e.observed_at),1,10)
                  FROM events e JOIN channels c ON c.chat_key = e.chat_key
                 WHERE {EMITTABLE} AND {relevance(args)}{where}
                 GROUP BY e.chat_key HAVING count(*) >= ?
                 ORDER BY count(*) DESC""",
            (*params, args.min_messages),
        )
    ]
    emit(rows, args.json)
    return 0


def cmd_people(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Person-level grouping, aggregated ACROSS chats.

    This is the F23 fix expressed as a query. The original engine grouped by
    `(channel, sender)` and called the result a chat, so one sender who appeared in 29
    different conversations was drafted as a single fictional entity. Person and chat are
    different groupings and both are offered explicitly; neither is a stand-in for the other.

    Windowed, because "who matters" is a question with a date on it: a person prominent in 2024
    and quiet since reads as peripheral to any query that does not name a period.
    """
    where, params = window(args)
    rows = [
        dict(zip(("person", "channel", "messages", "chats", "first", "last"), r, strict=True))
        for r in conn.execute(
            f"""SELECT e.sender, e.channel, count(*), count(DISTINCT e.chat_key),
                       substr(min(e.observed_at),1,10), substr(max(e.observed_at),1,10)
                  FROM events e
                 WHERE {EMITTABLE} AND {relevance(args)} AND e.direction='inbound'
                       AND e.sender IS NOT NULL{where}
                 GROUP BY e.sender, e.channel HAVING count(*) >= ?
                 ORDER BY count(*) DESC""",
            (*params, args.min_messages),
        )
    ]
    emit(rows, args.json)
    return 0


def cmd_outbound(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """The operator's own sent messages, with per-channel coverage GUARANTEED.

    Measured ratio in this corpus: 17,554 Telegram outbound to 206 Graph outbound, 85:1 — and
    101:1 once the noise filter runs, since 40 of the 206 are suppressed. A
    plain `ORDER BY observed_at DESC LIMIT n` therefore returns zero Outlook, which is exactly
    the defect behind `design-priors.md § self-model-spans-every-channel` (a self-model built
    from one channel is systematically wrong, not merely incomplete). So the quota is taken
    per channel and the counts are printed, rather than trusting a global ordering.

    The chat column is named `chat`, not `to`: on Telegram the two coincide, but Outlook rows
    are keyed by sender, so an outbound Graph row's chat is the operator himself. Calling that
    a recipient would invent the one field Outlook does not have.
    """
    # A forwarded message is something he SENT and did not WRITE, and this verb exists to
    # answer "how does he write". The historical rows cannot be filtered — the predecessor
    # never kept `forward_from`, so at minimum 44 of them are other people's prose recorded as
    # his — but everything ingested from 2026-08-15 can be, and is, unless asked otherwise.
    excluded = ""
    if tracks_forwards(conn) and not args.include_forwarded:
        excluded = " AND e.forwarded_from IS NULL"

    out: list[dict] = []
    for (channel,) in conn.execute(
        f"SELECT DISTINCT channel FROM events e WHERE {EMITTABLE} AND direction='outbound'"
    ):
        where, params = window(args)
        rows = conn.execute(
            f"""SELECT e.channel, substr(e.observed_at,1,10),
                       COALESCE(c.display_name, e.chat_key), e.body_redacted
                  FROM events e JOIN channels c ON c.chat_key = e.chat_key
                 WHERE {EMITTABLE} AND e.direction='outbound' AND e.channel=?
                       AND {relevance(args)} AND length(e.body_redacted) >= ?{excluded}{where}
                 ORDER BY e.observed_at DESC LIMIT ?""",
            (channel, args.min_length, *params, args.per_channel),
        ).fetchall()
        out.extend(
            dict(zip(("channel", "date", "chat", "text"), r, strict=True)) for r in rows
        )
    if not args.json:
        per = {}
        for r in out:
            per[r["channel"]] = per.get(r["channel"], 0) + 1
        print(f"coverage: {per}  (per-channel quota {args.per_channel})")
        if tracks_forwards(conn):
            held = conn.execute(
                f"""SELECT count(*) FROM events e
                     WHERE {EMITTABLE} AND e.direction='outbound'
                       AND e.forwarded_from IS NOT NULL"""
            ).fetchone()[0]
            print(
                f"forwarded rows in the corpus: {held:,} "
                + ("(excluded — `--include-forwarded` to see them)" if excluded else "(INCLUDED)")
            )
            print(
                "note: only rows ingested from 2026-08-15 carry this. Older rows are unmarked "
                "whether or not they were forwards, so this filter cannot clean the history."
            )
        print()
    emit(shape(out, args), args.json)
    return 0



def truncation_note(
    conn: sqlite3.Connection, sql: str, params: tuple | list, *, limit: int, shown: int
) -> str:
    """Say so when `--limit` dropped rows — and say that what came back is the TAIL.

    **This exists because the silence was load-bearing.** Every limited read here returns the
    *newest* n ordered `DESC`, and prints a result indistinguishable in shape from a complete
    one. On 2026-08-26 a session read a 320-message thread with `--limit 200`, saw no instance
    of a piece of feedback in the newest 200, and reported to the operator that he had **never**
    given it. He had — six times, five of them in the unread 120. The instruction *"a tool that
    reports absence is making a claim"* was already in doctrine; what was missing is that a tool
    returning a tail shaped exactly like a whole is making the same claim, silently.

    `thread`'s own docstring has said *"`--limit` takes the most recent n and always did"* since
    the day it was written. Documenting a trap where the reader is not looking is not a mitigation
    — the note has to arrive attached to the result.

    Only asked when the read came back exactly full, which is the only case in which anything can
    have been dropped. The count costs a second query and the overwhelming majority of reads are
    shorter than their limit — the same reasoning `cmd_thread` applies to `why_empty`.
    """
    if shown < limit:
        return ""
    total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", tuple(params)).fetchone()[0]
    if total <= shown:
        return ""
    return (
        f"note: showing the NEWEST {shown} of {total} matching rows — --limit dropped the "
        f"{total - shown} older ones. This is the tail of the result, not the whole of it, so "
        f"nothing here supports a claim that something never appears. Use --since/--until for a "
        f"chosen window, --sample to spread the reads over the full range, or raise --limit."
    )

def cmd_thread(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """One conversation in order.

    `--limit` takes the **most recent** n and always did; on a 5,781-message channel that
    means the opening and the middle are unreachable by default. Two ways out, and they answer
    different questions: `--since/--until` to read a chosen window, `--sample` to spread n
    reads evenly over the whole range when the question is "what is this channel like" rather
    than "what happened in March".
    """
    warn_if_name_is_ambiguous(conn, args.chat)
    where, params = window(args)
    if args.direction:
        where += " AND e.direction = ?"
        params.append(args.direction)
    mail_sql, mail_fields = mail_columns(conn)
    fwd_sql, fwd_fields = (
        (", e.forwarded_from", ("fwd",)) if tracks_forwards(conn) else ("", ())
    )
    fields = ("at", "dir", "from", "channel", "text", *mail_fields, *fwd_fields)
    sql = (
        f"""SELECT substr(e.observed_at,1,16), e.direction, e.sender, e.channel,
                   e.body_redacted{mail_sql}{fwd_sql}
              FROM events e JOIN channels c ON c.chat_key = e.chat_key
             WHERE {EMITTABLE} AND {relevance(args)}
               AND (e.chat_key = ? OR c.display_name = ?){where}"""
    )
    head = (args.chat, args.chat, *params)
    if args.sample:
        # Evenly spread rather than randomly drawn: a deterministic every-kth read is
        # reproducible across sessions, and reproducibility is what makes a second pass able
        # to confirm the first rather than merely disagree with it. Strided in Python, not in
        # SQL — a window function over a composed sub-select was harder to read than it was
        # worth, and the largest single chat is under 6,000 rows.
        every = conn.execute(f"{sql} ORDER BY e.observed_at", head).fetchall()
        step = max(1, len(every) // max(args.sample, 1))
        rows = every[::step][: args.sample]
    else:
        rows = list(
            reversed(
                conn.execute(
                    f"{sql} ORDER BY e.observed_at DESC LIMIT ?", (*head, args.limit)
                ).fetchall()
            )
        )
    out = [dict(zip(fields, r, strict=True)) for r in rows]
    channel = out[0].get("channel") if out else None
    for r in out:
        r.pop("channel", None)
    emit(
        [trim(r) for r in shape(out, args, channel=channel)],
        args.json,
        # Composed only when there is nothing to show: the diagnosis costs a second query, and
        # the overwhelming majority of reads return rows.
        empty=why_empty(conn, args) if not out else "",
        # `--sample` already spans the whole range by construction, so it drops nothing a
        # reader could mistake for absence.
        note="" if args.sample else truncation_note(
            conn, sql, head, limit=args.limit, shown=len(out)
        ),
    )
    return 0


def cmd_recent(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    where, params = window(args)
    sql = f"""SELECT substr(e.observed_at,1,16), e.channel, e.direction,
                     COALESCE(c.display_name, e.chat_key), e.sender, e.body_redacted
                FROM events e JOIN channels c ON c.chat_key = e.chat_key
               WHERE {EMITTABLE} AND {relevance(args)}{where}"""
    rows = conn.execute(
        f"{sql} ORDER BY e.observed_at DESC LIMIT ?", (*params, args.limit)
    ).fetchall()
    emit(
        shape(
            [dict(zip(("at", "channel", "dir", "chat", "from", "text"), r, strict=True))
             for r in rows],
            args,
        ),
        args.json,
        note=truncation_note(conn, sql, params, limit=args.limit, shown=len(rows)),
    )
    return 0


def cmd_search(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """FTS5 over `body_redacted`, or a substring scan when the index cannot see the query.

    The index is built on the redacted column deliberately: a search snippet is a read path
    like any other, and indexing `body` would let a match leak precisely what redaction removed.

    **Two paths, and the reader is always told which one answered.** `unicode61` cannot
    segment Chinese (see the module docstring), so a query containing CJK or kana takes a
    `LIKE` scan over the same column instead. A scan rather than a second index, deliberately:
    rebuilding FTS is a *write* to the fenced store — the operator's command, not the agent's —
    and it would fall due again after every ingest, whereas a scan needs no migration and can
    never drift from what it is scanning. What the scan gives up is real and worth knowing:
    no ranking and no boolean/phrase operators, just literal substring. It costs a full read
    of the visible corpus, which at this size is well under a second.
    """
    scan = args.substring or bool(UNSEGMENTED.search(args.query))
    where, params = window(args)
    if args.chat:
        where += " AND (e.chat_key = ? OR c.display_name = ?)"
        params += [args.chat, args.chat]
    if args.sender:
        where += " AND e.sender = ?"
        params.append(args.sender)
    if args.direction:
        where += " AND e.direction = ?"
        params.append(args.direction)
    # `--full` swaps the snippet for the stored row. Both are redacted; the snippet is
    # narrower, so it stays the default and widening is the reader's explicit act.
    if scan:
        base = f"""FROM events e
                  JOIN channels c ON c.chat_key = e.chat_key
                 WHERE e.body_redacted LIKE ? ESCAPE '\\'
                       AND {EMITTABLE} AND {relevance(args)}{where}"""
        count_sql = f"SELECT 1 {base}"
        count_params = (like_pattern(args.query), *params)
        rows = conn.execute(
            f"""SELECT substr(e.observed_at,1,10), e.channel,
                       COALESCE(c.display_name, e.chat_key), e.sender, e.body_redacted
                  {base}
                 ORDER BY e.observed_at DESC LIMIT ?""",
            (*count_params, args.limit),
        ).fetchall()
        if not args.full:
            rows = [(*r[:4], excerpt(r[4], args.query)) for r in rows]
    else:
        text = "e.body_redacted" if args.full else "snippet(events_fts, 0, '«', '»', '…', 18)"
        sql = f"""SELECT substr(e.observed_at,1,10), e.channel,
                         COALESCE(c.display_name, e.chat_key), e.sender, {text}
                    FROM events_fts f
                    JOIN events e ON e.rowid = f.rowid
                    JOIN channels c ON c.chat_key = e.chat_key
                   WHERE events_fts MATCH ? AND {EMITTABLE} AND {relevance(args)}{where}
                   ORDER BY e.observed_at DESC LIMIT ?"""
        used = args.query
        try:
            rows = conn.execute(sql, (args.query, *params, args.limit)).fetchall()
        except sqlite3.OperationalError as exc:
            # The bound parameter is safe from SQL injection, but FTS5 parses the *value* as
            # a query expression, and ordinary prose is not valid FTS5. `cost-benefit`,
            # `AWS-2` and `1-5` all raised `no such column: <tail>` — an unhandled traceback
            # out of a read verb, on hyphenated terms, in a corpus full of them.
            #
            # Retrying with every whitespace-separated token quoted makes each one a literal
            # phrase and keeps FTS5's implicit AND between them. It also disables the
            # operator syntax (`OR`, `NEAR`, `*`), which is why it is a FALLBACK rather than
            # the default: a query that parses keeps its full power, and only one that
            # cannot parse is reinterpreted. Either way the reader is told which happened —
            # silently changing what a query means is the failure this file keeps finding.
            quoted = " ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in args.query.split())
            try:
                rows = conn.execute(sql, (quoted, *params, args.limit)).fetchall()
            except sqlite3.OperationalError:
                print(f"neo-corpus search: cannot parse {args.query!r} as a query, even "
                      f"quoted ({exc}). Try --substring for a literal scan.", file=sys.stderr)
                return 1
            used = quoted
            print(f"note: {args.query!r} is not valid FTS5 syntax, so it was re-read as "
                  f"literal terms ({quoted}). Operators like OR / NEAR / * are not active "
                  f"in this result.", file=sys.stderr)
        # Counted against the query that actually answered, not the one that was typed — a
        # re-quoted fallback matches a different set, and a total from the original would be a
        # figure about a query nobody ran.
        count_sql = f"""SELECT 1 FROM events_fts f
                          JOIN events e ON e.rowid = f.rowid
                          JOIN channels c ON c.chat_key = e.chat_key
                         WHERE events_fts MATCH ? AND {EMITTABLE}
                               AND {relevance(args)}{where}"""
        count_params = (used, *params)
    # To stderr so it survives `--json` without becoming a field: which mechanism answered is
    # provenance for the reader, not data for a parser. A CJK query answered by the index would
    # have been a false negative, so the line is never noise.
    print(
        f"mode: {'substring scan' if scan else 'FTS index'}"
        + (" (unicode61 cannot segment this query's script)"
           if scan and not args.substring else ""),
        file=sys.stderr,
    )
    emit(
        shape(
            [dict(zip(("date", "channel", "chat", "from", "text"), r, strict=True)) for r in rows],
            args,
        ),
        args.json,
        empty="(no matches in admitted channels"
              + (" — literal substring, no wildcards)" if scan else ")"),
        note=truncation_note(conn, count_sql, count_params, limit=args.limit, shown=len(rows)),
    )
    return 0


def cmd_roster(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Who speaks in ONE conversation — metadata only, never a body.

    `people` deliberately aggregates across chats (the F23 fix) and so cannot answer "who is in
    this group". Reading 5,781 messages to find out is the alternative this verb removes, and
    it is the cheap half of the work: membership and volume are structure, not content.
    """
    warn_if_name_is_ambiguous(conn, args.chat)
    where, params = window(args)
    rows = conn.execute(
        f"""SELECT e.sender, e.direction, count(*),
                   substr(min(e.observed_at),1,10), substr(max(e.observed_at),1,10)
              FROM events e JOIN channels c ON c.chat_key = e.chat_key
             WHERE {EMITTABLE} AND {relevance(args)}
               AND (e.chat_key = ? OR c.display_name = ?){where}
             GROUP BY e.sender, e.direction ORDER BY count(*) DESC""",
        (args.chat, args.chat, *params),
    ).fetchall()
    emit(
        [dict(zip(("who", "dir", "messages", "first", "last"), r, strict=True)) for r in rows],
        args.json,
        empty=why_empty(conn, args) if not rows else "",
    )
    return 0


def cmd_timeline(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Monthly volume — metadata only, never a body.

    Answers "when was this alive" before "what does it say", so a read pass can spend its
    budget on the months that carry the project rather than on the most recent n messages,
    which is all `thread` can reach by default.
    """
    where, params = window(args)
    scope, head = "", []
    if args.chat:
        scope = " AND (e.chat_key = ? OR c.display_name = ?)"
        head = [args.chat, args.chat]
    rows = conn.execute(
        f"""SELECT substr(e.observed_at,1,7), e.channel, count(*),
                   count(DISTINCT e.sender), count(DISTINCT e.chat_key)
              FROM events e JOIN channels c ON c.chat_key = e.chat_key
             WHERE {EMITTABLE} AND {relevance(args)}{scope}{where}
             GROUP BY substr(e.observed_at,1,7), e.channel
             ORDER BY substr(e.observed_at,1,7)""",
        (*head, *params),
    ).fetchall()
    emit(
        [dict(zip(("month", "channel", "messages", "people", "chats"), r, strict=True))
         for r in rows],
        args.json,
        empty="(nothing in range)",
    )
    return 0


def cmd_mail(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Outlook by CONVERSATION rather than by sender — what the store could not express.

    Outlook `chat_key` is `graph:<sender>`, so a correspondent's chat is entirely inbound and
    every reply the operator sent sits in a different bucket. `conversation_id`, recovered by
    `neo-fetch enrich`, is the only thing that puts a question back next to its answer. The
    key itself is untouched: the operator's 108 disposition decisions are keyed on it.

    Without an argument, lists conversations; with `--conversation`, reads one in order.
    """
    if not enriched(conn):
        print(
            "this corpus predates the Graph enrichment — no subjects, recipients or\n"
            "conversation ids to group by. Run `neo-fetch enrich --commit` first."
        )
        return 1
    where, params = window(args)
    if args.chat:
        where += " AND (e.chat_key = ? OR c.display_name = ?)"
        params += [args.chat, args.chat]

    if args.conversation:
        rows = conn.execute(
            f"""SELECT substr(e.observed_at,1,16), e.direction, e.sender, e.recipients,
                       e.subject_redacted, e.body_redacted
                  FROM events e JOIN channels c ON c.chat_key = e.chat_key
                 WHERE {EMITTABLE} AND {relevance(args)}
                   AND e.conversation_id = ?{where}
                 ORDER BY e.observed_at""",
            (args.conversation, *params),
        ).fetchall()
        out = [
            dict(zip(("at", "dir", "from", "to", "subject", "text"), r, strict=True))
            for r in rows
        ]
        emit(
            [trim(r) for r in shape(out, args, channel="graph")],
            args.json,
            empty="(no such conversation in an admitted channel)",
        )
        return 0

    rows = conn.execute(
        f"""SELECT e.conversation_id, count(*),
                   sum(CASE WHEN e.direction='outbound' THEN 1 ELSE 0 END),
                   count(DISTINCT e.sender),
                   substr(min(e.observed_at),1,10), substr(max(e.observed_at),1,10),
                   max(e.subject_redacted)
              FROM events e JOIN channels c ON c.chat_key = e.chat_key
             WHERE {EMITTABLE} AND {relevance(args)}
               AND e.conversation_id IS NOT NULL{where}
             GROUP BY e.conversation_id
             HAVING count(*) >= ?
             ORDER BY max(e.observed_at) DESC LIMIT ?""",
        (*params, args.min_messages, args.limit),
    ).fetchall()
    cols = ("conversation", "messages", "sent", "people", "first", "last", "subject")
    emit(
        [dict(zip(cols, r, strict=True)) for r in rows],
        args.json,
        empty=_why_no_mail(conn, args, where, params) if not rows else "",
    )
    return 0


def _why_no_mail(
    conn: sqlite3.Connection, args: argparse.Namespace, where: str, params: list
) -> str:
    """The mail listing returned nothing. Say which of the three reasons it was.

    It used to say `(no enriched Outlook conversations in range)` whatever had happened, and that
    is the `thread`-said-"not admitted" defect of 2026-08-15 in a second verb — a filter emptying
    a result, reported as an absence of data. **This one is the worse wording of the two**, because
    it does not merely say "nothing here", it says *why*, and the why was usually false: it names
    enrichment, i.e. `neo-fetch enrich --commit`, a twenty-two-minute mailbox re-fetch, as the
    remedy for a result that was never missing.

    Found 2026-09-07: `mail --since 2026-08-31` printed it over 77 freshly-pulled Graph rows that
    all carried `subject`, `recipients` **and** `conversation_id`. `--min-messages` defaults to 2
    ("skip singletons") and a single week of Graph mail is nearly all singletons, so the `HAVING`
    clause emptied it. `--min-messages 1` returned the lot.
    """
    total, threaded = conn.execute(
        f"""SELECT count(e.rowid), count(e.conversation_id)
              FROM events e JOIN channels c ON c.chat_key = e.chat_key
             WHERE {EMITTABLE} AND {relevance(args)}
               AND e.channel = 'graph'{where}""",
        params,
    ).fetchone()
    if not total:
        return "(no visible Outlook mail in range — widen --since/--until, or check `neo-corpus stats`)"
    if not threaded:
        return (
            f"({total:,} visible Outlook message(s) in range, but none carries a "
            "conversation_id to group by. THIS is the enrichment case: run "
            "`neo-fetch enrich --commit`.)"
        )
    # Rows exist and are threaded, so the only thing left that can have emptied it is the
    # per-conversation floor. Name the flag and the number, because the default is not 1.
    biggest = conn.execute(
        f"""SELECT max(n) FROM (
                SELECT count(*) AS n
                  FROM events e JOIN channels c ON c.chat_key = e.chat_key
                 WHERE {EMITTABLE} AND {relevance(args)}
                   AND e.channel = 'graph' AND e.conversation_id IS NOT NULL{where}
                 GROUP BY e.conversation_id)""",
        params,
    ).fetchone()[0]
    return (
        f"({threaded:,} threaded Outlook message(s) in range, but the largest conversation holds "
        f"{biggest} and --min-messages is {args.min_messages}. Nothing is missing from the "
        f"corpus — pass --min-messages 1 to see them. The default skips singletons, and a short "
        f"window of mail is mostly singletons.)"
    )


# `pending_from=<ts>; <n> message(s) not stored` — written by `Writer.register_pending`.
_HELD_COUNT = re.compile(r";\s*(\d+)\s+message")


def cmd_channels_pending(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Undecided channels, METADATA ONLY — never a message body.

    Operator decision 2026-08-14: the agent may see a name, a volume and a date range in order
    to *propose* a disposition, but not the content it is proposing about. Choosing to ingest a
    chat into a pipeline was not consent to show it to an agent, so admission stays an explicit
    human act. Record a decision with `neo-channels set`.

    **Amended 2026-08-24, on his ruling.** That rule left one case unanswerable: a live-ingest
    registration reports `messages=0` (nothing was stored, truthfully), and when the account also
    has no display name the row carries a bare numeric id and nothing else. Handing him that and
    asking for a decision is asking him to rule on a string he cannot read — *"Don't give me a
    bare ID and ask me to decide. Give me context!!! username, last message (time and
    content)!!!"* So the four `pending_*` columns are shown here: username and kind, which were
    always inside the metadata rule and were simply never captured, and the newest message's
    time plus a redacted, truncated preview, which he authorised. They are written only for an
    `undecided` channel and cleared the moment it is ruled on.
    """
    # A corpus that has never been written to by this version has no `pending_*` columns, and
    # `neo-corpus` opens the store READ-ONLY — it cannot migrate one. So it degrades to the old
    # shape rather than raising: the honest failure here is less context, not a broken verb.
    have = {r[1] for r in conn.execute("PRAGMA table_info(channels)")}
    # Two guards, not one. A corpus written by the version that added the first four columns has
    # `pending_preview` and no `pending_last_out`, and asking for the fifth would raise on a verb
    # whose whole contract is to degrade rather than break.
    out_sql = "c.pending_last_out" if "pending_last_out" in have else "NULL"
    if "pending_preview" not in have:
        rows = conn.execute(
            """SELECT c.chat_key, c.channel, c.display_name, NULL, c.group_kind,
                      c.seed_provenance, count(e.rowid),
                      substr(min(e.observed_at),1,10), substr(max(e.observed_at),1,10),
                      c.notes, NULL, NULL, NULL
                 FROM channels c LEFT JOIN events e ON e.chat_key = c.chat_key
                WHERE c.disposition = 'undecided'
                GROUP BY c.chat_key ORDER BY count(e.rowid) DESC LIMIT ?""",
            (args.limit,),
        ).fetchall()
        print(
            "note: this corpus predates the pending-context columns — no username, newest "
            "message or preview available until the next `neo-fetch pull --commit` migrates it."
        )
    else:
        rows = conn.execute(
            f"""SELECT c.chat_key, c.channel, c.display_name, c.pending_username,
                      COALESCE(c.pending_kind, c.group_kind), c.seed_provenance,
                      count(e.rowid), substr(min(e.observed_at),1,10),
                      substr(max(e.observed_at),1,10),
                      c.notes, c.pending_last_at, c.pending_preview, {out_sql}
                 FROM channels c LEFT JOIN events e ON e.chat_key = c.chat_key
                WHERE c.disposition = 'undecided'
                GROUP BY c.chat_key ORDER BY count(e.rowid) DESC LIMIT ?""",
            (args.limit,),
        ).fetchall()
    cols = (
        "chat_key", "channel", "name", "username", "kind", "why_pending",
        "messages", "first", "last", "notes", "newest_at", "newest_preview",
        "newest_is_his",
    )
    records = [dict(zip(cols, r, strict=True)) for r in rows]
    # `messages` is the stored count and is 0 for every live-ingest registration. The number a
    # disposition is actually ruled from is the one the pull printed and wrote into `notes`, so
    # lift it out rather than leaving him to reconcile a 0 against the pull log.
    for rec in records:
        match = _HELD_COUNT.search(rec.get("notes") or "")
        rec["held_not_stored"] = int(match.group(1)) if match else None
        rec.pop("notes", None)
    if args.json:
        emit(records, True, empty="(nothing pending — every channel has a disposition)")
        return 0
    if not records:
        print("(nothing pending — every channel has a disposition)")
        return 0
    for rec in records:
        name = rec["name"] or ""
        handle = f" (@{rec['username']})" if rec["username"] else ""
        print(f"\n{name or handle.strip() or '(no name)'}{handle if name else ''}")
        bits = [rec["channel"], rec["chat_key"]]
        if rec["kind"]:
            bits.append(rec["kind"])
        if rec["held_not_stored"] is not None:
            bits.append(f"{rec['held_not_stored']} message(s) held, not stored")
        elif rec["messages"]:
            bits.append(f"{rec['messages']} stored")
        print("  " + " · ".join(str(b) for b in bits))
        if rec["newest_at"]:
            print(f"  newest {rec['newest_at']} (UTC)")
        if rec["newest_preview"]:
            # WHO said it, never just what was said. The preview is the newest message in the
            # dialog whichever way it was going, and in a 1:1 that is the operator's own about
            # half the time — so an unlabelled quote hands him his own words as the stranger's,
            # which is the thing he is being asked to identify. `None` stays unattributed
            # rather than guessing: a corpus written before `pending_last_out` existed knows
            # the text and genuinely does not know the speaker.
            who = {1: "he said", 0: "they said"}.get(rec["newest_is_his"])
            print(f"  > {rec['newest_preview']}" if who is None
                  else f"  > [{who}] {rec['newest_preview']}")
    print(
        f"\n{len(records)} undecided. Propose a disposition from the context above, then "
        "the operator records it:\n  neo-channels set <chat_key> work|personal|ignore|mixed"
    )
    return 0


# --------------------------------------------------------------------------- write verb


def cmd_set(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.disposition not in DISPOSITIONS:
        print(f"disposition must be one of {DISPOSITIONS}", file=sys.stderr)
        return 2
    cur = conn.execute(
        "UPDATE channels SET disposition=?, decided_at=datetime('now'), "
        "notes=COALESCE(?, notes) WHERE chat_key=?",
        (args.disposition, args.note, args.chat_key),
    )
    if not cur.rowcount:
        print(f"no channel with chat_key {args.chat_key!r}", file=sys.stderr)
        return 1
    conn.commit()
    name, disp = conn.execute(
        "SELECT display_name, disposition FROM channels WHERE chat_key=?", (args.chat_key,)
    ).fetchone()
    print(f"{args.chat_key} ({name}) -> {disp}")
    return 0


# --------------------------------------------------------------------------- entry points


def _base(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true")


def _reading(ap: argparse.ArgumentParser) -> None:
    """Options every body-emitting verb takes, so that reading cheaply is never verb-specific."""
    ap.add_argument(
        "--preview", type=int, default=0, metavar="N",
        help="truncate each body to N characters — scan a channel without paying for it",
    )
    ap.add_argument(
        "--include-suppressed", action="store_true",
        help="also return rows marked corpus noise (daily_report, stats_report, separator, "
             "service_msg) — an inherited judgement, not a safety boundary",
    )


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?$")


def _date_arg(value: str) -> str:
    """Reject anything that is not a date, at parse time.

    The window is a *lexical* comparison against ISO-8601 text (see `window()`), and an
    unvalidated bound is therefore not a filter that fails — it is a filter that silently
    stops existing. `--until garbage` becomes `observed_at <= 'garbageT99'`, and every
    date sorts below 'g', so the whole corpus comes back looking filtered. `--since
    garbage` is the mirror image and returns nothing, which reads as *there is no such
    traffic*. Both were live until 2026-08-16 and neither printed a word.

    This is the same failure family as the CJK search and the emptied `thread` filter: a
    tool making a false claim about absence, or about scope, in a way nothing downstream
    can see. Anything that consumes this filter as a boundary — the evaluation harness
    clamps graded sessions with `--until` — inherits the defect silently, which is why it
    is rejected here rather than documented.

    Accepts `YYYY-MM-DD` and, because the lexical comparison supports it correctly,
    an optional `THH`, `THH:MM` or `THH:MM:SS`.
    """
    if not _DATE_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a date. Use YYYY-MM-DD, optionally YYYY-MM-DDTHH:MM. "
            "A malformed bound would be silently ignored rather than applied."
        )
    try:
        dt.date.fromisoformat(value[:10])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a real date: {exc}") from exc
    return value


def _windowed(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--since", metavar="YYYY-MM-DD", type=_date_arg)
    ap.add_argument("--until", metavar="YYYY-MM-DD", type=_date_arg,
                    help="inclusive of the whole day; YYYY-MM-DDTHH:MM narrows within it")


def build_parser() -> argparse.ArgumentParser:
    """The whole `neo-corpus` argument surface, built without running anything.

    Split out from `main()` so a test can assert on how an argument is *parsed* — which is
    where `--since` / `--until` validation lives — without opening the corpus.
    """
    ap = argparse.ArgumentParser(prog="neo-corpus", description=__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("stats", help="counts, coverage, pending disposition count")
    _base(p)
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("chats", help="one row per admitted conversation")
    _base(p)
    _windowed(p)
    p.add_argument("--min-messages", type=int, default=1)
    p.add_argument("--include-suppressed", action="store_true")
    p.set_defaults(fn=cmd_chats)

    p = sub.add_parser("people", help="senders aggregated ACROSS chats (see F23)")
    _base(p)
    _windowed(p)
    p.add_argument("--min-messages", type=int, default=5)
    p.add_argument("--include-suppressed", action="store_true")
    p.set_defaults(fn=cmd_people)

    p = sub.add_parser("outbound", help="the operator's sent messages, per-channel quota")
    _base(p)
    _reading(p)
    _windowed(p)
    p.add_argument("--per-channel", type=int, default=40)
    p.add_argument("--min-length", type=int, default=80)
    p.add_argument(
        "--include-forwarded", action="store_true",
        help="include messages he forwarded rather than wrote (excluded by default)",
    )
    p.set_defaults(fn=cmd_outbound)

    p = sub.add_parser("thread", help="one conversation in order")
    _base(p)
    _reading(p)
    _windowed(p)
    p.add_argument("chat", help="chat_key or display name")
    p.add_argument("--limit", type=int, default=100, help="the most RECENT n")
    p.add_argument(
        "--sample", type=int, default=0, metavar="N",
        help="instead of the most recent n, take N spread evenly over the whole range",
    )
    p.add_argument("--direction", choices=("inbound", "outbound"))
    p.set_defaults(fn=cmd_thread)

    p = sub.add_parser("mail", help="Outlook grouped by conversation (needs `neo-fetch enrich`)")
    _base(p)
    _reading(p)
    _windowed(p)
    p.add_argument("--conversation", help="read one conversation in order")
    p.add_argument("--chat", help="scope the listing to one chat_key or display name")
    p.add_argument("--min-messages", type=int, default=2, help="listing: skip singletons")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(fn=cmd_mail)

    p = sub.add_parser("roster", help="who speaks in ONE chat, METADATA ONLY")
    _base(p)
    _windowed(p)
    p.add_argument("chat", help="chat_key or display name")
    p.add_argument("--include-suppressed", action="store_true")
    p.set_defaults(fn=cmd_roster)

    p = sub.add_parser("timeline", help="monthly volume, METADATA ONLY")
    _base(p)
    _windowed(p)
    p.add_argument("chat", nargs="?", help="one chat, or omit for the whole visible corpus")
    p.add_argument("--include-suppressed", action="store_true")
    p.set_defaults(fn=cmd_timeline)

    p = sub.add_parser("recent", help="most recent admitted traffic")
    _base(p)
    _reading(p)
    _windowed(p)
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(fn=cmd_recent)

    p = sub.add_parser("search", help="FTS5 over redacted bodies")
    _base(p)
    _reading(p)
    _windowed(p)
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--chat", help="scope to one chat_key or display name")
    p.add_argument("--sender", help="scope to one sender id or address")
    p.add_argument("--direction", choices=("inbound", "outbound"))
    p.add_argument("--full", action="store_true", help="the stored row, not the snippet")
    p.add_argument(
        "--substring", action="store_true",
        help="force the literal substring scan (automatic for Chinese/Japanese, which the "
             "FTS tokenizer cannot segment); finds partial words the index cannot",
    )
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("channels-pending", help="undecided channels, METADATA ONLY")
    _base(p)
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(fn=cmd_channels_pending)

    return ap


def main() -> int:
    args = build_parser().parse_args()
    conn = connect(args.db)
    try:
        return int(args.fn(conn, args))
    finally:
        conn.close()


def channels_main() -> int:
    """`neo-channels` — the operator's curation tool. NOT in the agent's allowlist."""
    ap = argparse.ArgumentParser(prog="neo-channels", description=cmd_set.__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("set", help="record a disposition for one channel")
    p.add_argument("chat_key")
    p.add_argument("disposition", choices=DISPOSITIONS)
    p.add_argument("--note")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.set_defaults(fn=cmd_set)
    args = ap.parse_args()
    conn = connect(args.db, writable=True)
    try:
        return int(args.fn(conn, args))
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
