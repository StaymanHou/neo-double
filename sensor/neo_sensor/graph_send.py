"""`neo-mail` — the Outlook send path, built 2026-09-22 on his instruction.

*"can you then build yourself an cli similar to the Telegram sends? make sure yourself have the
prompt to properly gate it. always ask for my confirmation before any send"* — after a reply-all
to a colleague's `Fw: cp email issue from client` was drafted and the only way out was for him to
paste it. That reverses the 2026-08-24 scope ruling (*"For now, just Telegram, not Graph /
Outlook"*) for this one shape, and **only** this shape.

**Everything `neo-send` guarantees, this guarantees, by importing it rather than restating it.**
The footnote constant, the per-message digest, the approval record with his verbatim words, the
60-minute expiry, single-use consumption, `sent.md` — all of it comes from `telegram_send`. Two
send paths that each implement "the gate" are two gates that drift apart, and the one that drifts
is the one nobody is looking at.

**What is different about mail, and what the differences cost.**

**1. The target is a MESSAGE, not a room — so the recipients are pinned inside the approval.**
A Telegram post goes to a chat whose membership he already knows. A reply-all goes to whoever was
on the original, and that list is not visible in the draft he approves. So the preview prints
every recipient, and the approval code is computed over the target message id **and the sorted
recipient set**. At `--commit` the reply-all draft is created in his mailbox, *its* recipients
are read back from Graph, and if they are not exactly the approved set the draft is deleted and
the send refused. "Approved for Sam, Alice and Erin; also went to the client" cannot happen.

**2. Only reply-all, to a message already in the corpus, from a correspondent ruled `work`.**
There is no `--to`. There is no new-message verb. The target is resolved from the corpus by chat
and exact subject, and **ambiguity is refused** — a thread holds several messages with the same
subject, and picking the newest is how a reply lands on the wrong one. Narrow on purpose: this is
the shape he asked for, and a free-form `--to` is a way to write to anybody.

**3. Mail is HTML, and his text is not.** The draft Graph creates carries the quoted thread as
HTML. His message is inserted above it **HTML-escaped**, one `<div>` per line and `<div><br></div>`
per blank line — so every `<`, `&` and `*` he typed arrives literally, the mail analogue of
`parse_mode=None`. After the PATCH the draft body is **read back and rendered to text**, and the
send is refused unless his whole message, footnote included, appears verbatim at the top. Outlook
rewrites HTML freely; verifying the stored draft rather than the bytes we sent is the only check
that describes what the recipient will get.

**4. A separate token, with send scopes, that the read path never sees.** `neo-fetch` holds a
`Mail.Read` token. This holds `Mail.ReadWrite` + `Mail.Send` in its own file,
`~/.local/state/neo/graph-send-token.json`, 0600. Putting send scopes in the pull's cache would
mean every `neo-fetch pull` runs holding a token that can send mail as him, which nothing about
pulling needs. `Mail.ReadWrite` is required because `createReplyAll` makes a draft and the body
must be patched; the draft is deleted on every refusal after creation. **Signing in is his act**
(`neo-mail login`, a device code on his phone) — the third of the three reasons a command is his.

**What it does not do, the same sentence as `neo-send`'s: it cannot prove a human approved
anything.** The agent runs both halves. What stands in for his hand on the command is his words in
`approval.json` and `sent.md`, in git, beside the draft — and the doctrine in
`~/.config/claude-neo/CLAUDE.md`: **never write a quote he did not say.**

**Not built, deliberately:** new mail, reply (not reply-all), forward, attachments, cc/bcc edits,
sending from an address other than the signed-in one. Each is a separate decision.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from neo_sensor.config import STATE_DIR
from neo_sensor.graph_fetch import DEFAULT_ENV, GRAPH, OPERATOR_EMAILS, TOKEN_MODE, html_to_text
from neo_sensor.ingest import DEFAULT_DB
from neo_sensor.telegram_send import (
    APPROVAL_TTL_MINUTES,
    FOOTNOTE,
    FOOTNOTE_BLOCK,
    SENDABLE,
    Refused,
    batch_code,
    check_approval,
    consume_approval,
    digest,
    record,
    write_approval,
)

SEND_TOKEN_PATH = STATE_DIR / "graph-send-token.json"
SEND_SCOPES = ["Mail.ReadWrite", "Mail.Send"]

# No transport limit worth enforcing for mail; this exists so a wrong file (a log, a dump) is
# refused rather than mailed to three colleagues.
MAX_CHARS = 20_000


# --------------------------------------------------------------------------- target


def resolve_message(
    conn: sqlite3.Connection, chat: str, subject: str, at: str | None
) -> tuple[str, str, str, str]:
    """`(external_id, chat_key, subject, observed_at)` for exactly one message to reply to.

    Resolved from the corpus — metadata columns only, never `body` — so the target is something
    the sensor has already admitted. The correspondent's channel must be ruled `work`, the same
    rule as `neo-send`: the read fence says what may be read, this says what may be answered.
    """
    key = chat if chat.startswith("graph:") else f"graph:{chat.lower()}"
    row = conn.execute(
        "SELECT disposition FROM channels WHERE chat_key = ? AND channel = 'graph'", (key,)
    ).fetchone()
    if row is None:
        raise Refused(f"no Outlook correspondent {key!r} in the corpus")
    if row[0] not in SENDABLE:
        raise Refused(f"{key} is ruled {row[0]!r}, not 'work'. Refusing to reply there.")
    sql = (
        "SELECT external_id, chat_key, subject, observed_at FROM events "
        "WHERE channel='graph' AND chat_key = ? AND subject = ?"
    )
    params: list[str] = [key, subject]
    if at:
        sql += " AND substr(observed_at, 1, ?) = ?"
        params += [str(len(at)), at]  # type: ignore[list-item]
    rows = conn.execute(sql + " ORDER BY observed_at", params).fetchall()
    if not rows:
        raise Refused(
            f"no message from {key} with subject {subject!r}"
            + (f" at {at}" if at else "")
            + ". Exact subject, as `neo-corpus thread` prints it."
        )
    if len(rows) > 1:
        stamps = ", ".join(r[3] for r in rows)
        raise Refused(
            f"{len(rows)} messages from {key} carry that subject ({stamps}). Pass --at with the "
            f"UTC stamp of the one to answer — picking the newest is how a reply lands on the "
            f"wrong message."
        )
    return rows[0]


def reply_all_recipients(message: dict, me: str) -> list[str]:
    """Who a reply-all to `message` goes to: its sender plus To and Cc, minus him, lowercased.

    This is the PREDICTION shown in the preview and pinned in the approval. It is checked at
    commit against what Graph actually put on the draft, so a wrong prediction is a refusal and
    never a wrong send.
    """
    out: set[str] = set()
    sender = ((message.get("from") or {}).get("emailAddress") or {}).get("address")
    if sender:
        out.add(sender.lower())
    for field in ("toRecipients", "ccRecipients"):
        for entry in message.get(field) or []:
            addr = ((entry or {}).get("emailAddress") or {}).get("address")
            if addr:
                out.add(addr.lower())
    mine = {me.lower(), *OPERATOR_EMAILS}
    return sorted(out - mine)


def draft_recipients(draft: dict) -> list[str]:
    """Every address on the draft Graph created — To, Cc AND Bcc, so nothing is invisible."""
    out: set[str] = set()
    for field in ("toRecipients", "ccRecipients", "bccRecipients"):
        for entry in draft.get(field) or []:
            addr = ((entry or {}).get("emailAddress") or {}).get("address")
            if addr:
                out.add(addr.lower())
    return sorted(out)


def target_key(external_id: str, recipients: list[str]) -> str:
    """What the approval is pinned to: this message, and exactly these people."""
    return f"reply-all:{digest(external_id)}:" + ",".join(recipients)


# --------------------------------------------------------------------------- body


def compose(path: Path) -> str:
    """The file's text plus the footnote — `telegram_send.compose`'s rules, mail's limit."""
    if not path.exists():
        raise Refused(f"{path} does not exist")
    body = path.read_text(encoding="utf-8").rstrip()
    if not body:
        raise Refused(f"{path} is empty — refusing to send a blank message")
    if FOOTNOTE in body:
        raise Refused(f"{path} already contains the footnote; it is appended by the sender")
    text = body + FOOTNOTE_BLOCK
    if not text.endswith(FOOTNOTE):
        raise Refused("footnote assembly failed — refusing to send")
    if len(text) > MAX_CHARS:
        raise Refused(f"{path} is {len(text):,} characters — that is not an email, refusing")
    return text


def to_html(text: str) -> str:
    """His plain text as Outlook-style HTML: escaped, one `<div>` per line."""
    lines = []
    for line in text.split("\n"):
        lines.append(f"<div>{html.escape(line)}</div>" if line.strip() else "<div><br></div>")
    return "".join(lines)


_BODY_OPEN = re.compile(r"<body[^>]*>", re.IGNORECASE)


def insert_above_quote(draft_html: str, text: str) -> str:
    """Put his message at the top of the reply draft's body, above the quoted thread."""
    block = to_html(text) + "<div><br></div>"
    m = _BODY_OPEN.search(draft_html or "")
    if not m:
        return block + (draft_html or "")
    return draft_html[: m.end()] + block + draft_html[m.end() :]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def verify_body(stored_html: str, text: str) -> None:
    """Refuse unless the stored draft, rendered to text, opens with his message verbatim.

    Whitespace is normalised on both sides — Outlook reflows blank lines — and nothing else is.
    """
    rendered = _norm(html_to_text(stored_html))
    if not rendered.startswith(_norm(text)):
        raise Refused(
            "the draft Outlook stored does not open with the approved text verbatim. Refusing "
            "and deleting the draft."
        )
    if FOOTNOTE not in rendered:
        raise Refused("the footnote is missing from the stored draft. Refusing.")


# --------------------------------------------------------------------------- auth


def _send_app(args):  # noqa: ANN001, ANN202
    import msal

    from neo_sensor.graph_fetch import credentials

    cache = msal.SerializableTokenCache()
    if SEND_TOKEN_PATH.exists():
        cache.deserialize(SEND_TOKEN_PATH.read_text())
    client, tenant = credentials(args.env_file)
    app = msal.PublicClientApplication(
        client_id=client,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache,
    )
    return app, cache


def _persist(cache) -> None:  # noqa: ANN001
    if not cache.has_state_changed:
        return
    SEND_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEND_TOKEN_PATH.with_suffix(".tmp")
    tmp.touch(mode=TOKEN_MODE)
    tmp.write_text(cache.serialize())
    os.chmod(tmp, TOKEN_MODE)
    tmp.replace(SEND_TOKEN_PATH)


def cmd_login(args: argparse.Namespace) -> int:
    """Device-code sign-in with send scopes. His command — it needs his phone."""
    app, cache = _send_app(args)
    flow = app.initiate_device_flow(scopes=SEND_SCOPES)
    if "user_code" not in flow:
        print(f"device flow failed: {flow.get('error_description', flow)}", file=sys.stderr)
        return 2
    print(f"\n{flow['message']}\n")
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print(f"login failed: {result.get('error_description', result)}", file=sys.stderr)
        return 2
    _persist(cache)
    print(f"signed in with {SEND_SCOPES}; token at {SEND_TOKEN_PATH} (mode {TOKEN_MODE:o})")
    return 0


def _token(args: argparse.Namespace) -> tuple[str, str]:
    """`(access_token, signed-in address)`.

    The address comes from the MSAL account, not from `GET /me` — that call needs `User.Read`,
    which this token deliberately does not carry (first live run, 2026-09-22: HTTP 403).
    """
    app, cache = _send_app(args)
    accounts = app.get_accounts()
    if not accounts:
        raise Refused("not signed in for sending — he runs `neo-mail login` once (device code)")
    result = app.acquire_token_silent(SEND_SCOPES, account=accounts[0])
    _persist(cache)
    if not result or "access_token" not in result:
        raise Refused("send token refresh failed — he runs `neo-mail login` again")
    return result["access_token"], (accounts[0].get("username") or "").lower()


# --------------------------------------------------------------------------- send


def _check(r, what: str) -> dict:  # noqa: ANN001
    if r.status_code >= 400:
        raise Refused(f"Graph refused {what}: HTTP {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


def cmd_reply_all(args: argparse.Namespace) -> int:
    import httpx

    if len(args.message) != 1:
        raise Refused("one file per reply-all — an email is one message")
    path = args.message[0]
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        ext_id, chat_key, subject, at = resolve_message(conn, args.chat, args.subject, args.at)
    finally:
        conn.close()
    text = compose(path)

    token, me_addr = _token(args)
    if not me_addr:
        raise Refused("could not tell which mailbox is signed in — refusing")
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=60) as client:
        original = _check(
            client.get(
                f"{GRAPH}/me/messages/{ext_id}"
                "?$select=subject,from,toRecipients,ccRecipients,receivedDateTime",
                headers=h,
            ),
            "reading the original message",
        )
        recipients = reply_all_recipients(original, me_addr)
        if not recipients:
            raise Refused("reply-all would reach nobody but you — refusing")
        key = target_key(ext_id, recipients)
        display = f"reply-all to {chat_key} “{subject}” ({at} UTC)"

        print(f"from:    {me_addr}")
        print(f"target:  {display}")
        print(f"to/cc:   {', '.join(recipients)}")
        print(f"\n--- {path} - {len(text):,} chars - digest {digest(text)} ---")
        print(text)

        if args.approve:
            rec = write_approval([path], key, display, [text], args.quote or "", args.session)
            print(f"\nAPPROVAL RECORDED in {rec}")
            print(f"  code {batch_code(key, [text])} · expires in {APPROVAL_TTL_MINUTES} min")
            print(f"  his words: “{(args.quote or '').strip()}”")
            print("Single-use. Now run the same command with --commit instead of --approve.")
            return 0
        if not args.commit:
            print(f"\nPREVIEW — nothing sent. Would reply-all to {len(recipients)} people.")
            print("Show him this, get his words, then --approve --quote '<his words>'.")
            return 0

        # The gate, before anything is created in his mailbox. The recipients are part of `key`,
        # so a change in who is on the original since approval is refused here.
        approval = check_approval([path], key, [text])
        print(f"\napproval {approval['code']} from {approval['approved_at']}")
        print(f"  his words: “{approval['approved_quote']}”")

        draft = _check(
            client.post(f"{GRAPH}/me/messages/{ext_id}/createReplyAll", headers=h),
            "createReplyAll",
        )
        draft_id = draft["id"]
        try:
            actual = draft_recipients(draft)
            if actual != recipients:
                raise Refused(
                    f"Outlook put {actual} on the reply-all; he approved {recipients}. Refusing."
                )
            body = draft.get("body") or {}
            _check(
                client.patch(
                    f"{GRAPH}/me/messages/{draft_id}",
                    headers=h,
                    json={
                        "body": {
                            "contentType": "html",
                            "content": insert_above_quote(body.get("content") or "", text),
                        }
                    },
                ),
                "patching the draft body",
            )
            stored = _check(
                client.get(
                    f"{GRAPH}/me/messages/{draft_id}"
                    "?$select=body,toRecipients,ccRecipients,bccRecipients",
                    headers=h,
                ),
                "reading the draft back",
            )
            verify_body((stored.get("body") or {}).get("content") or "", text)
            if draft_recipients(stored) != recipients:
                raise Refused("recipients changed on the stored draft. Refusing.")
        except Exception:
            client.delete(f"{GRAPH}/me/messages/{draft_id}", headers=h)
            print("draft deleted from your mailbox", file=sys.stderr)
            raise

        # Consumed before the send, as in `neo-send`: a crash after this cannot be retried into a
        # second copy of the same email.
        consume_approval([path])
        _check(client.post(f"{GRAPH}/me/messages/{draft_id}/send", headers=h), "send")
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        log = record(path, key, display, text, stamp, quote=approval["approved_quote"])
        print(f"sent reply-all to {', '.join(recipients)}; recorded in {log}")
    print("Run `neo-fetch pull --commit` to bring it into the corpus as an outbound row.")
    return 0


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(
        prog="neo-mail",
        description="Reply-all, from his Outlook, to a message already in the corpus — gated "
        "exactly like `neo-send`. Preview, then --approve --quote '<his words>', then --commit.",
    )
    ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    sub = ap.add_subparsers(dest="verb", required=True)

    lg = sub.add_parser("login", help="device-code sign-in with send scopes (his command)")
    lg.set_defaults(fn=cmd_login)

    ra = sub.add_parser("reply-all", help="reply-all to one corpus message")
    ra.add_argument("--chat", required=True, help="the correspondent, e.g. alice@example.com")
    ra.add_argument("--subject", required=True, help="exact subject of the message to answer")
    ra.add_argument("--at", help="UTC stamp prefix, e.g. 2026-09-22T18:05, when a subject repeats")
    ra.add_argument("message", type=Path, nargs="+", help="the message file (exactly one)")
    ra.add_argument("--db", type=Path, default=DEFAULT_DB)
    ra.add_argument("--approve", action="store_true", help="record his approval; needs --quote")
    ra.add_argument("--quote", help="his own words approving it, verbatim")
    ra.add_argument("--session", help="agent session id, for provenance")
    ra.add_argument("--commit", action="store_true", help="send; refused without live approval")
    ra.set_defaults(fn=cmd_reply_all)

    args = ap.parse_args()
    if getattr(args, "approve", False) and getattr(args, "commit", False):
        ap.error("--approve and --commit are separate steps. Approve, show him, then commit.")
    if getattr(args, "quote", None) and not getattr(args, "approve", False):
        ap.error("--quote belongs with --approve")
    try:
        return int(args.fn(args))
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
