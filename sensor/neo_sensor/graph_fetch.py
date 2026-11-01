"""`neo-fetch` — the write path from Microsoft Graph back into the fenced corpus.

The Outlook half of this corpus was stored as Graph's `bodyPreview`, capped at 255 characters,
with `subject` and `toRecipients` fetched and then dropped by the predecessor's mapper and
`conversationId` never requested at all. 82% of Outlook rows sit at that cap. What survives is
openings: the salutation and the first two sentences, never how a message closes or concedes.

This command re-fetches those messages from the mailbox by id and fills in what was lost.
`external_id` IS the Graph message id, so every row is addressable; nothing has to be matched
heuristically.

**It is a write path, so it sits on the far side of the fence, not the near side.** Raw bodies
pass through this process exactly as they do at ingest, are redacted here before they are
stored, and only ever reach an agent through `neo-corpus` afterwards. Nothing this module
prints contains an unredacted body — the counters and the `--sample` output are both taken
from the redacted text, so a run is safe to watch.

**`chat_key` is deliberately NOT re-keyed.** Grouping Outlook by `conversationId` would be the
natural fix for the missing threading, and it is the one thing this must not do: channel
dispositions are keyed on `chat_key`, all 108 of them were decided by the operator against
`graph:<sender>`, and re-keying would orphan every one of those decisions and drop the rows
back to `undecided` — i.e. invisible. `conversation_id` is stored as a column instead, so
threading becomes available to queries without a single admission decision being disturbed.

Two commands:

    neo-fetch login     device-code sign-in; the operator runs this, once
    neo-fetch enrich    re-fetch and fill in; safe to re-run, resumes where it stopped

The token blob lives at `~/.local/state/neo/graph-token.json`, mode 0600 — inside the path the
agent is denied, next to the corpus it serves.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from neo_sensor.config import ENV_FILE, STATE_DIR

DEFAULT_DB = STATE_DIR / "corpus.db"
TOKEN_PATH = STATE_DIR / "graph-token.json"
DEFAULT_ENV = ENV_FILE

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read"]
TOKEN_MODE = 0o600

# `uniqueBody` is the message stripped of the quoted thread beneath it — the part its author
# actually wrote. That is the whole point of the exercise, so it is requested first and `body`
# is kept only as the fallback for messages where Graph declines to produce it.
SELECT = "id,subject,from,toRecipients,ccRecipients,conversationId,uniqueBody,body"
SELECT_FALLBACK = "id,subject,from,toRecipients,ccRecipients,conversationId,body"

BATCH = 20  # Graph's $batch ceiling per request

MIGRATION = """
ALTER TABLE events ADD COLUMN subject TEXT;
ALTER TABLE events ADD COLUMN subject_redacted TEXT;
ALTER TABLE events ADD COLUMN recipients TEXT;
ALTER TABLE events ADD COLUMN conversation_id TEXT;
ALTER TABLE events ADD COLUMN body_source TEXT;
ALTER TABLE events ADD COLUMN enriched_at TEXT;
ALTER TABLE events ADD COLUMN enrich_status TEXT;
CREATE INDEX IF NOT EXISTS events_conversation ON events (conversation_id);
"""


# --------------------------------------------------------------------------- schema


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    if "conversation_id" in cols:
        return
    conn.executescript(MIGRATION)
    conn.commit()
    print("migrated: subject / recipients / conversation_id / body_source / enrich_* columns")


# --------------------------------------------------------------------------- html


class _Text(HTMLParser):
    """Graph returns HTML. This keeps the words and the line breaks and drops the rest.

    Deliberately not a general HTML-to-Markdown pass: what downstream wants is how a sentence
    was phrased, and every extra transform is another chance to alter it.
    """

    _DROP = {"script", "style", "head"}
    _BREAK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._DROP:
            self._skip += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._DROP and self._skip:
            self._skip -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


# Marketing mail pads its preheader with hundreds of invisible characters so the
# preview line in a client looks clean. They are not content, they survive every
# whitespace collapse because they are not whitespace, and they would be indexed and
# redaction-scanned as though they were words. Zero-width space / non-joiner /
# joiner, combining grapheme joiner, soft hyphen, BOM.
INVISIBLE = re.compile("[\u200b\u200c\u200d\u034f\u00ad\ufeff]+")

# Redaction is a spaCy pass, so its cost is linear in length and one 400 KB marketing
# mail can dominate a whole run. Nothing downstream needs the twentieth screen of a
# newsletter. The cap is recorded on the row rather than applied silently.
BODY_CAP = 20_000


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html or "")
    text = INVISIBLE.sub("", "".join(p.parts).replace(" ", " "))
    text = re.sub(r"[ \t]+", " ", text)
    # Strip each line BEFORE collapsing blank runs, not after. Outlook composes with
    # `<div>&nbsp;</div>` spacers, which become lines holding a single space — not
    # empty, so a newline collapse that runs first leaves every one of them behind.
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def body_of(message: dict) -> tuple[str, str]:
    """Return (text, source). `uniqueBody` when Graph produced one, else `body`."""
    for key in ("uniqueBody", "body"):
        blob = message.get(key) or {}
        content = blob.get("content") or ""
        if content.strip():
            text = html_to_text(content) if blob.get("contentType") == "html" else content.strip()
            if text:
                if len(text) > BODY_CAP:
                    return text[:BODY_CAP], f"{key}:capped"
                return text, key
    return "", "empty"


def addresses(message: dict, *fields: str) -> list[str]:
    out: list[str] = []
    for f in fields:
        for entry in message.get(f) or []:
            addr = ((entry or {}).get("emailAddress") or {}).get("address")
            if addr and addr not in out:
                out.append(addr)
    return out


# --------------------------------------------------------------------------- auth


def credentials(env_file: Path) -> tuple[str, str]:
    """Client and tenant id, from the environment or from an env file.

    Only the two `MS_GRAPH_*` keys are read out of the file and neither is ever printed — the
    file holds unrelated secrets and this has no business seeing them.
    """
    client = os.environ.get("MS_GRAPH_CLIENT_ID")
    tenant = os.environ.get("MS_GRAPH_TENANT_ID")
    if client and tenant:
        return client, tenant
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key == "MS_GRAPH_CLIENT_ID" and not client:
                client = value
            elif key == "MS_GRAPH_TENANT_ID" and not tenant:
                tenant = value
    if not (client and tenant):
        raise SystemExit(
            "MS_GRAPH_CLIENT_ID / MS_GRAPH_TENANT_ID not found in the environment or "
            f"{env_file}. Pass --env-file, or export them."
        )
    return client, tenant


def _cache(): # noqa: ANN202
    import msal

    cache = msal.SerializableTokenCache()
    if TOKEN_PATH.exists():
        cache.deserialize(TOKEN_PATH.read_text())
    return cache


def _persist(cache) -> None:  # noqa: ANN001
    """Write the token blob at 0600 BEFORE the rename, so it is never briefly world-readable."""
    if not cache.has_state_changed:
        return
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_PATH.with_suffix(".tmp")
    tmp.touch(mode=TOKEN_MODE)
    tmp.write_text(cache.serialize())
    os.chmod(tmp, TOKEN_MODE)
    tmp.replace(TOKEN_PATH)


def _app(args):  # noqa: ANN001, ANN202
    import msal

    client, tenant = credentials(args.env_file)
    return msal.PublicClientApplication(
        client_id=client,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=_cache(),
    )


def cmd_login(args: argparse.Namespace) -> int:
    """Device-code sign-in. Interactive by nature — the operator runs this, not the agent."""
    app = _app(args)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print(f"device flow failed: {flow.get('error_description', flow)}", file=sys.stderr)
        return 2
    print(f"\n{flow['message']}\n")
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print(f"login failed: {result.get('error_description', result)}", file=sys.stderr)
        return 2
    _persist(app.token_cache)
    print(f"signed in; token cached at {TOKEN_PATH} (mode {TOKEN_MODE:o})")
    return 0


def bearer(args: argparse.Namespace) -> str:
    app = _app(args)
    accounts = app.get_accounts()
    if not accounts:
        raise SystemExit("not signed in — run `neo-fetch login` first")
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _persist(app.token_cache)
    if not result or "access_token" not in result:
        raise SystemExit("token refresh failed — run `neo-fetch login` again")
    return result["access_token"]


# --------------------------------------------------------------------------- fetch


def fetch_batch(client, token, ids: list[str], select: str) -> dict[str, dict]:  # noqa: ANN001
    """One `$batch` of id-keyed GETs. Returns {external_id: message} for the ones that came back.

    A 404 is expected and is not an error: a message deleted from the mailbox since ingest is
    simply not recoverable, and saying so per row beats failing the run.

    `token` is a CALLABLE, not a string. A full pass over 7,871 rows runs close to an hour and a
    Graph access token lives about that long, so a token acquired once at the start expires
    somewhere in the middle — and because `--redo` reprocesses every row rather than resuming,
    dying at row 5,000 costs the whole run, not the remainder. Asking for the token per batch
    costs nothing (msal serves it from cache until it actually needs refreshing) and removes the
    failure entirely.
    """
    payload = {
        "requests": [
            {"id": str(i), "method": "GET", "url": f"/me/messages/{mid}?$select={select}"}
            for i, mid in enumerate(ids)
        ]
    }
    for attempt in range(5):
        r = client.post(
            f"{GRAPH}/$batch",
            headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 2 ** (attempt + 1)))
            print(f"  throttled, waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        out: dict[str, dict] = {}
        for resp in r.json().get("responses", []):
            if resp.get("status") == 200:
                out[ids[int(resp["id"])]] = resp.get("body") or {}
            elif resp.get("status") == 429:
                time.sleep(2)
        return out
    raise SystemExit("gave up after repeated throttling — try again later")


def run(args: argparse.Namespace) -> int:
    import httpx

    from neo_sensor.redaction import redact  # deferred: ~2s spaCy load

    conn = sqlite3.connect(args.db)
    try:
        ensure_columns(conn)
        where = "channel='graph'" if args.redo else "channel='graph' AND enriched_at IS NULL"
        rows = conn.execute(
            f"SELECT rowid, external_id FROM events WHERE {where} ORDER BY observed_at DESC"  # noqa: S608
        ).fetchall()
        if args.limit:
            rows = rows[: args.limit]
        if not rows:
            print("nothing to enrich — every Graph row already carries an enriched_at")
            return 0

        print(f"{len(rows):,} Graph rows to enrich" + ("" if args.commit else "  [DRY RUN]"))

        def token() -> str:
            return bearer(args)

        token()  # fail fast on a missing login rather than after the first batch
        select = SELECT
        stats = {"ok": 0, "not_found": 0, "empty": 0}
        removed: Counter[str] = Counter()
        grew = shrank = 0
        samples: list[tuple[str, str]] = []

        with httpx.Client() as client:
            for start in range(0, len(rows), BATCH):
                chunk = rows[start : start + BATCH]
                ids = [external for _, external in chunk]
                try:
                    got = fetch_batch(client, token, ids, select)
                except Exception:
                    if select == SELECT_FALLBACK:
                        raise
                    print("  uniqueBody rejected; falling back to body")
                    select = SELECT_FALLBACK
                    got = fetch_batch(client, token, ids, select)

                for rowid, external in chunk:
                    message = got.get(external)
                    if message is None:
                        stats["not_found"] += 1
                        if args.commit:
                            conn.execute(
                                "UPDATE events SET enriched_at=datetime('now'), "
                                "enrich_status='not_found' WHERE rowid=?",
                                (rowid,),
                            )
                        continue

                    text, source = body_of(message)
                    subject = (message.get("subject") or "").strip()
                    to = addresses(message, "toRecipients", "ccRecipients")
                    conversation = message.get("conversationId")

                    old = conn.execute(
                        "SELECT length(body_redacted) FROM events WHERE rowid=?", (rowid,)
                    ).fetchone()[0] or 0
                    if len(text) > old:
                        grew += 1
                    elif text and len(text) < old:
                        shrank += 1

                    if not text:
                        stats["empty"] += 1
                    else:
                        stats["ok"] += 1

                    body_result = redact(text) if text else None
                    body_r = body_result.text if body_result else ""
                    subject_r = redact(subject).text if subject else ""
                    meta = (
                        json.dumps([m.model_dump() for m in body_result.redaction_metadata])
                        if body_result else "[]"
                    )
                    if body_result:
                        for item in body_result.redaction_metadata:
                            removed[item.entity_type] += 1
                    if len(samples) < args.sample:
                        samples.append((subject_r, body_r[:200]))

                    if args.commit:
                        conn.execute(
                            "UPDATE events SET body=?, body_redacted=?, "
                            "redaction_meta=?, redacted_at=datetime('now'), "
                            "subject=?, subject_redacted=?, recipients=?, conversation_id=?, "
                            "body_source=?, enriched_at=datetime('now'), enrich_status='ok' "
                            "WHERE rowid=?",
                            (
                                text, body_r, meta, subject, subject_r,
                                json.dumps(to, ensure_ascii=False), conversation, source, rowid,
                            ),
                        )
                if args.commit:
                    conn.commit()
                done = min(start + BATCH, len(rows))
                print(f"  {done:,}/{len(rows):,}  ok={stats['ok']} missing={stats['not_found']}")

        print(
            f"\nenriched {stats['ok']:,} · not in mailbox {stats['not_found']:,} · "
            f"empty body {stats['empty']:,}"
        )
        print(f"bodies that grew: {grew:,}   bodies that shrank: {shrank:,}")
        print(
            "redaction removed: "
            + (", ".join(f"{k} x{v}" for k, v in removed.most_common()) or "nothing")
        )
        if samples:
            print("\nsample (REDACTED — this is post-filter text, not raw mail):")
            for subject, body in samples:
                print(f"  subject: {subject}")
                print(f"     body: {body[:160]}\n")
        if args.commit:
            from neo_sensor.redact_corpus import rebuild_fts_over_redacted

            rebuild_fts_over_redacted(conn)
        else:
            print("\nDRY RUN — nothing written. Re-run with --commit.")
        return 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- pull

# The two folders the historical corpus came from. `/me/messages` would be one call instead of
# two, and would also sweep in Drafts and Deleted Items — messages he never sent and messages
# he threw away, both of which would read as ordinary traffic once stored.
FOLDERS = ("inbox", "sentitems")

PULL_SELECT = SELECT + ",receivedDateTime"

# All of his addresses (`NEO_OPERATOR_EMAILS`, comma-separated). A different person with a similar
# address is deliberately absent — merging two people is the mistake a wrong list makes silently.
OPERATOR_EMAILS = tuple(
    a.strip().lower()
    for a in os.environ.get(
        "NEO_OPERATOR_EMAILS", ""
    ).split(",")
    if a.strip()
)


def sender_of(message: dict) -> str:
    addr = ((message.get("from") or {}).get("emailAddress") or {}).get("address") or ""
    return addr.lower()


def fetch_page(client, token, url: str) -> dict:  # noqa: ANN001
    """One page of a mail-folder listing, with the same throttle handling as `$batch`."""
    for attempt in range(5):
        r = client.get(url, headers={"Authorization": f"Bearer {token()}"}, timeout=90)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 2 ** (attempt + 1)))
            print(f"  throttled, waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise SystemExit("gave up after repeated throttling — try again later")


@dataclass
class _Held:
    """One never-ruled mail correspondent, counted and characterised but not stored.

    `count` accumulates across the whole run; `last_at`, `subject`, `body` and `out` describe
    the **newest** message of the run, kept by `see()` as a running maximum. Nothing here is
    written as an event — `Writer.register_pending` redacts and truncates the body on the way
    into the channel row, and clears it again the moment the correspondent stops being pending.

    **It used to set those fields once, on first sight, "because Graph pages newest-first" — and
    Graph does not.** The default sort for a message collection is `receivedDateTime desc`, but
    this puller asks for `$orderby=receivedDateTime`, which is **ascending**; so first sight was
    the OLDEST message and `channels-pending` printed it under the word `newest`. Only visible on
    a correspondent with two or more held messages, which is one row in a typical pull, which is
    exactly why it survived.

    Fixed 2026-09-07 as a running maximum rather than by correcting the sort, for the reason the
    `newest` cursor in `pull_mail` is already a maximum: a comparison cannot be wrong about the
    page order, and a comment about the page order can.
    """

    sender: str
    count: int
    last_at: str | None = None
    subject: str | None = None
    body: str | None = None
    out: bool | None = None

    def see(self, received: str | None, subject: str | None, body: str | None, out: bool) -> None:
        """Count one more message, and keep it if it is the newest so far."""
        self.count += 1
        if (received or "") > (self.last_at or ""):
            self.last_at, self.subject, self.body, self.out = received, subject, body, out


def pull_mail(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Fetch mail newer than the last pull and write it through the shared ingest path.

    **Direction is decided by the author, not the folder.** The historical rows took it from
    where the message sat, which files a mail he wrote and also received as inbound (24 rows
    measured 2026-08-14) and a send-as copy from someone else as outbound (1 row). Since the
    206-row Outlook outbound sample is the scarcest and most load-bearing in the corpus, a
    handful of rows is a meaningful fraction of it. New rows compare `from` against his own
    addresses and record `direction_source='author'` so the two derivations stay distinguishable
    rather than silently merged.

    What this does NOT fix: a vendor mail he forwarded still sits in Sent Items and is still
    genuinely *from* him, so it lands outbound with someone else's words in the body. That is
    the relay defect, it is not a direction defect, and Graph carries nothing that would let
    ingest tell the two apart.
    """
    import httpx

    from neo_sensor.ingest import (
        IncomingEvent,
        PendingChannel,
        Writer,
        corpus_high_water,
        corpus_stamp,
        dispositions,
        ensure_schema,
        get_cursor,
        graph_stamp,
        pending_floors,
        rebuild_index,
        report,
        set_cursor,
    )

    if args.commit:
        ensure_schema(conn)
    known = dispositions(conn)
    floor = args.since or get_cursor(conn, "graph") or corpus_high_water(conn, "graph")
    if not floor:
        raise SystemExit("no cursor and no existing Graph rows — pass --since YYYY-MM-DD")

    # A correspondent the operator has just ruled on has held mail older than the cursor, and
    # Graph has no per-chat resume — its filter is one date range over two folders. So the run
    # rewinds to the earliest held date among the channels that are now admitted. Rewinding is
    # cheap because the dedup index makes a re-scan a no-op; NOT rewinding silently loses
    # exactly the mail he just asked for, which is the expensive half of the trade.
    rewind = [
        when
        for chat_key, when in pending_floors(conn).items()
        if chat_key.startswith("graph:") and known.get(chat_key) in ("work", "mixed")
    ]
    if rewind and min(rewind) < floor:
        floor = min(rewind)
        print(f"rewinding to {floor} to collect mail held for newly-admitted correspondents")
    stamp = graph_stamp(floor)
    print(f"resuming from {stamp}" + ("" if args.commit else "  [DRY RUN]"))

    def token() -> str:
        return bearer(args)

    token()  # fail fast on a missing login rather than after the first page
    writer = Writer(conn, commit=args.commit)
    newest = floor
    scanned = 0
    held: dict[str, _Held] = {}

    with httpx.Client() as client:
        for folder in FOLDERS:
            url = (
                f"{GRAPH}/me/mailFolders/{folder}/messages"
                f"?$select={PULL_SELECT}&$top=50&$orderby=receivedDateTime"
                f"&$filter=receivedDateTime ge {stamp}"
            )
            page_no = 0
            while url:
                page = fetch_page(client, token, url)
                messages = page.get("value", [])
                page_no += 1
                for message in messages:
                    scanned += 1
                    sender = sender_of(message)
                    if not sender:
                        continue
                    chat_key = f"graph:{sender}"
                    disposition = known.get(chat_key)
                    if disposition in ("personal", "ignore"):
                        continue
                    # `undecided` is never-ruled, same as no row at all. Reading this as
                    # `is None` meant the second pull stored what the first one held back —
                    # `register_pending` writes an `undecided` row, so the test passed once and
                    # then silently stopped applying. Fixed 2026-08-24; see the same comment in
                    # `telegram_fetch.pull`.
                    if disposition in (None, "undecided") and not args.include_new:
                        # Same rule as Telegram: a correspondent he has never ruled on is
                        # counted, not stored. Milder here — this is his work mailbox — but a
                        # rule that holds on one channel and not the other is one he would have
                        # to remember, and remembering is what the fence exists to avoid.
                        # Keep the newest of the held messages, not just the count. Graph
                        # pages newest-first within a folder, so the FIRST one seen for a
                        # sender is the newest — hence `setdefault` on the body and a plain
                        # increment on the count.
                        prev = held.get(chat_key)
                        if prev is None:
                            prev = held[chat_key] = _Held(sender=sender, count=0)
                        text, _src = body_of(message)
                        prev.see(
                            message.get("receivedDateTime") or None,
                            (message.get("subject") or "").strip() or None,
                            text,
                            sender in OPERATOR_EMAILS,
                        )
                        continue
                    writer.note_channel(chat_key, "graph", sender)
                    text, source = body_of(message)
                    received = message.get("receivedDateTime") or ""
                    writer.write(
                        IncomingEvent(
                            channel="graph",
                            external_id=message.get("id") or "",
                            chat_key=chat_key,
                            observed_at=received,
                            sender=sender,
                            direction="outbound" if sender in OPERATOR_EMAILS else "inbound",
                            direction_source="author",
                            body=text,
                            chat_display=sender,
                            subject=(message.get("subject") or "").strip() or None,
                            recipients=addresses(message, "toRecipients", "ccRecipients"),
                            conversation_id=message.get("conversationId"),
                            body_source=source,
                        )
                    )
                    if received > (newest or ""):
                        newest = received
                writer.flush()
                print(f"  {folder}: page {page_no}, {len(messages)} message(s)")
                url = page.get("@odata.nextLink")

    for chat_key, h in held.items():
        # The subject is the recognisable half of a mail, so it leads the preview. It goes
        # through the same redaction call the body does — `preview_of` is the only path.
        body = f"{h.subject} — {h.body}" if h.subject else h.body
        if args.commit:
            writer.register_pending(
                chat_key, "graph", h.sender, h.count, stamp,
                kind="mail-sender", last_at=h.last_at, last_body=body,
                last_out=h.out,
            )
        else:
            writer.pending[chat_key] = PendingChannel(
                channel="graph", display=h.sender, count=h.count,
                kind="mail-sender",
                last_at=corpus_stamp(h.last_at) if h.last_at else None,
                preview=writer.preview_of(body),
                last_out=h.out,
            )

    print(f"\nscanned {scanned:,} messages across {', '.join(FOLDERS)}")
    report(writer, commit=args.commit)
    if args.commit:
        if newest:
            set_cursor(conn, "graph", newest, f"pull; {writer.written} written")
        conn.commit()
        rebuild_index(conn)
    return 0


def cmd_reclassify(args: argparse.Namespace) -> int:
    from neo_sensor.ingest import reclassify

    conn = (
        sqlite3.connect(args.db)
        if args.commit
        else sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    )
    try:
        return reclassify(conn, commit=args.commit)
    finally:
        conn.close()


def telegram_login(args: argparse.Namespace) -> int:
    """Deferred so that the Graph verbs keep working on a machine without Telethon installed."""
    from neo_sensor.telegram_fetch import cmd_login

    return cmd_login(args)


def cmd_pull(args: argparse.Namespace) -> int:
    """`pull` over one channel or both. Each half opens its own connection and reports its own
    counts — a combined total would hide the skew that makes this corpus what it is."""
    codes = []
    for channel in (("telegram", "graph") if args.channel == "both" else (args.channel,)):
        print(f"\n=== {channel} ===")
        # A dry run opens the corpus READ-ONLY. Without this, `--commit`-less still migrates the
        # schema on the way past, which is a write to the fenced store performed by the verb
        # whose entire promise is that it does not write. The connection mode is the guarantee;
        # a branch inside the writer would only be a promise again.
        conn = (
            sqlite3.connect(args.db)
            if args.commit
            else sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        )
        try:
            if channel == "telegram":
                from neo_sensor.telegram_fetch import cmd_pull as telegram_pull

                codes.append(telegram_pull(conn, args))
            else:
                codes.append(pull_mail(conn, args))
        finally:
            conn.close()
    return max(codes) if codes else 0


def cmd_withdraw_pending(args: argparse.Namespace) -> int:
    """`neo-fetch withdraw-pending` — see `ingest.withdraw_pending` for why this exists.

    Read-only connection without `--commit`, same rule as `pull`: the verb that promises not to
    write must be unable to, and the connection mode is the only version of that promise that
    cannot be forgotten.
    """
    from neo_sensor.ingest import ensure_schema, rebuild_index, withdraw_pending

    conn = (
        sqlite3.connect(args.db)
        if args.commit
        else sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    )
    try:
        return _withdraw(conn, args, ensure_schema, rebuild_index, withdraw_pending)
    finally:
        conn.close()


def _withdraw(conn, args, ensure_schema, rebuild_index, withdraw_pending) -> int:  # noqa: ANN001
    if args.commit:
        ensure_schema(conn)
    found = withdraw_pending(conn, commit=args.commit)
    if not found:
        print("nothing to withdraw — no never-ruled channel has stored messages")
        return 0
    verb = "withdrew" if args.commit else "would withdraw"
    total = sum(n for _, _, n, _ in found)
    print(f"{verb} {total} row(s) across {len(found)} never-ruled channel(s):")
    for chat_key, display, n, floor in found:
        print(f"  {chat_key:<24} {n:>4} row(s)  {display or '(no name)'}")
        print(f"    resume floor restored to {floor}")
    if args.commit:
        rebuild_index(conn)
        print("\nFTS rebuilt. Re-run the pull — each chat re-registers from its restored floor.")
    else:
        print("\ndry run — nothing written. Add --commit.")
    return 0


def main() -> int:
    # Line-buffer stdout. Both verbs are long-running and print things the operator needs
    # WHILE they run — a device code he has to type, a progress count over 7,582 rows — and
    # Python block-buffers as soon as stdout is not a terminal. Backgrounding `login` was
    # enough to swallow the code entirely: the process sat polling for a login nobody could
    # see how to complete.
    sys.stdout.reconfigure(line_buffering=True)

    ap = argparse.ArgumentParser(prog="neo-fetch", description=__doc__)
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("login", help="device-code sign-in (operator runs this)")
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("enrich", help="re-fetch Outlook rows and fill in what ingest dropped")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    p.add_argument("--limit", type=int, default=0, help="stop after N rows (try a small one first)")
    p.add_argument("--redo", action="store_true", help="re-enrich rows already done")
    p.add_argument("--sample", type=int, default=0, help="print N redacted samples")
    p.add_argument("--commit", action="store_true", help="write; without it nothing is stored")
    p.set_defaults(fn=run)

    p = sub.add_parser(
        "reclassify",
        help="apply the noise rules to rows ingested before they were ported; "
             "report (never rewrite) any disagreement with the historical classification",
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--commit", action="store_true", help="write; without it nothing is stored")
    p.set_defaults(fn=cmd_reclassify)

    p = sub.add_parser("telegram-login", help="interactive Telegram sign-in (operator runs this)")
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    p.set_defaults(fn=telegram_login)

    p = sub.add_parser(
        "withdraw-pending",
        help="un-store any messages stored for a channel he has never ruled on",
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--commit", action="store_true")
    p.set_defaults(fn=cmd_withdraw_pending)

    p = sub.add_parser("pull", help="catch up from the last pull — new messages, both channels")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    p.add_argument(
        "--channel", choices=("telegram", "graph", "both"), default="both",
        help="which side to catch up (default both)",
    )
    p.add_argument(
        "--since",
        help="override the resume point, YYYY-MM-DD. Without it the cursor is used, and "
             "without a cursor the newest message already in the corpus.",
    )
    p.add_argument("--chat", help="one Telegram dialog only, by chat_key or name")
    p.add_argument("--limit-per-chat", type=int, default=0, help="stop after N per dialog")
    p.add_argument(
        "--include-new", action="store_true",
        help="also STORE conversations you have never ruled on. Off by default: they are "
             "counted and registered so you can rule on them, and their messages are left "
             "where they are until you do.",
    )
    p.add_argument("--commit", action="store_true", help="write; without it nothing is stored")
    p.set_defaults(fn=cmd_pull)

    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
