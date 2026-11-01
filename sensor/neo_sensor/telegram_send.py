"""The send path — the first thing in this project that puts text in front of another human.

Built 2026-08-24 on his instruction. Until now the whole system was structurally incapable of
egress: `~/.config/claude-neo/CLAUDE.md` said *"nothing you write leaves this machine by itself
— there is no send path"*, and that was true of the code, not just of the doctrine. It is no
longer true, so the properties that used to be free have to be built.

**Why it lives in `neo_sensor` and not a new top-level package.** The Telethon session at
`~/.local/state/neo/telegram.session` authenticates as him and is a credential in every real
sense; a second package would either duplicate the credential handling or reach across a
boundary to borrow it, and duplicated credential handling is how a fence rots. `sensor/` is
already the Telegram/Graph I/O layer in practice — `neo-fetch` writes to the store from here.
What `sensor/` claims is that the agent cannot read raw message bodies, and nothing in this
module touches `events.body`.

Five properties, each of them structural rather than remembered:

**The text is a FILE, never an argument.** What goes out is byte-identical to what he read and
approved. There is no `--text`, no template, no substitution and no assembly at send time — the
class of bug where a draft is approved and something adjacent is sent cannot occur, because
there is nothing to assemble. `--commit` reads the file off disk and hands those bytes to
Telegram.

**The footnote is a constant, not a parameter.** His standing requirement, 2026-08-24: *"for the
messages you will send. Always append a footnote saying it's written by <the assistant> and reviewed,
edited, and approved by me"*. A flag can be forgotten by a call site; the predecessor gated
egress on `credentials_redacted`, a boolean every call site hardcoded `True`, and leaked real AWS
keys while reporting itself clean. So this module composes the footnote itself and then
**verifies it is present in the final bytes** before the send, rather than trusting that it added
it.

**`parse_mode=None`.** Telethon parses Markdown by default. His prose contains none —
`knowledge/self/style.md` § *The negative space*: *"No bold or markdown headers in his own
Telegram prose"* — so every `*`, `_` and backtick in a message is a literal character he typed.
Under the default parser a stray `*` silently eats the rest of a line. Literal text is the only
correct mode for a voice model built on someone who does not write Markdown.

**Only a channel he has ruled `work` can be written to.** The read fence decides which channels
an agent may read; this is that shape applied to egress. `personal`, `ignore` and `undecided` are
all refused, and refused *before* the client connects. An `undecided` channel is the sharp case:
he has not decided whether an agent may even read it, so it is certainly not somewhere an agent
may post.

**Nothing is sent without `--commit`, and every send is recorded.** The dry run prints the exact
bytes, the resolved chat title and id, and the character count. A committed send appends to
`sent.md` beside the message file — the `drafts/` template has always had that slot and nothing
ever filled it, because until today there was nothing to fill it with.

**`--commit` requires a live approval record, and this is the gate.** His ruling, 2026-08-24:
*"the sends should also become yours but there must be an in-conversation approval gate."* So the
send is the agent's to run, and it is gated on an `approval.json` written by `--approve` that
carries the target, a digest of **each message's final bytes**, his own words approving it, and a
timestamp.

Four things that record makes mechanically impossible, and one it does not:

- **Approved this, sent that.** The digest is over the composed bytes, footnote included. Change
  one character of one file after approval and the batch code no longer matches — refused.
- **Approved these, sent those.** The record pins the file list *and its order*. Adding a fourth
  message, dropping one, or reordering them invalidates it.
- **Approved for one room, sent to another.** The chat_key is in the code.
- **Approved last week, sent today.** The record expires (`APPROVAL_TTL_MINUTES`) and is
  **single-use** — consumed on send, so a second `--commit` cannot repost the same batch.

**What it does not do, said plainly: it cannot prove a human approved anything.** The agent runs
both halves, so an agent that fabricates a quote and calls `--approve` gets through. There is no
way around that once the send is the agent's to run — his hand on the command was the only
structural version of that gate, and he has deliberately traded it for speed. What this buys
instead is that an unapproved send stops being an *oversight* and becomes a *fabricated record*:
his words are written into `approval.json` and into `sent.md`, in the repo, in git, next to the
draft he actually read. The failure is still possible and it is no longer invisible.

**What is deliberately NOT here.** No reply-to, no editing, no deleting, no media, no forwarding,
and no way to name a chat that is not already in the corpus with a `work` disposition. Each of
those is a separate decision and none of them was asked for.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from neo_sensor.config import ASSISTANT_NAME
from neo_sensor.ingest import DEFAULT_DB

# Telegram's hard per-message ceiling. Telethon will silently split a longer message into
# several, which for a formatted weekly report means a mangled one — the `=========` header
# lands on its own and the bullets arrive as a second message. Refusing is the honest failure.
MAX_CHARS = 4096

# His requirement, verbatim in intent: every message says it was drafted by the assistant and
# reviewed by him. The assistant's name is configuration (`NEO_ASSISTANT_NAME`), read ONCE at import
# — so it is still a constant at runtime, not a per-send value.
#
# A constant, and the only one. If a future call site wants different wording, it changes here in
# one place and every message changes with it; there is deliberately no per-send override, because
# a per-send override is a per-send opportunity to omit it.
FOOTNOTE = f"— Drafted by {ASSISTANT_NAME}, my AI double. Reviewed, edited and approved by me."

# Separated by a blank line. He fences topics inside a message with `=====` and separates
# thoughts with a blank line; a footnote glued to the last bullet reads as another bullet.
FOOTNOTE_BLOCK = f"\n\n{FOOTNOTE}"

SENDABLE = ("work",)

# An approval is a statement about a conversation that is happening now. An hour is long enough
# for a turn that involves a correction or two, and short enough that "he approved this last
# week" cannot be honoured — which is the whole point, since a stale approval is the most
# plausible way this goes wrong without anybody lying.
APPROVAL_TTL_MINUTES = 60

APPROVAL_FILE = "approval.json"


class Refused(Exception):
    """A send that will not happen, with the reason the operator needs to read."""


def resolve_target(conn: sqlite3.Connection, name: str) -> tuple[str, str]:
    """`(chat_key, display_name)` for a Telegram channel he has ruled `work`.

    Exact match on `display_name` or on `chat_key`, and **ambiguity is refused rather than
    resolved** — this corpus holds `IT Desk` and `IT Desk Administrators`, and it also holds pairs
    that share a display name outright (`roster` unions by name, which once made two chats look
    like one and put a manufactured claim on two knowledge files). Picking the bigger one would
    be the same mistake with a worse consequence: a message in the wrong room.
    """
    rows = conn.execute(
        """SELECT chat_key, display_name, disposition
             FROM channels
            WHERE channel='telegram' AND (display_name = ? OR chat_key = ?)""",
        (name, name),
    ).fetchall()
    if not rows:
        raise Refused(
            f"no Telegram channel named {name!r}. It must already be in the corpus — this "
            f"cannot address a chat the sensor has never seen."
        )
    if len(rows) > 1:
        keys = ", ".join(f"{k} ({d})" for k, d, _ in rows)
        raise Refused(
            f"{name!r} is ambiguous — {len(rows)} channels match: {keys}. Use a chat_key."
        )
    chat_key, display, disposition = rows[0]
    if disposition not in SENDABLE:
        raise Refused(
            f"{display or chat_key} is ruled {disposition!r}, not 'work'. Refusing to post there. "
            f"The read fence says which channels an agent may read; this says which it may write "
            f"to, and an `undecided` channel is one you have not yet decided it may even read."
        )
    return chat_key, display or chat_key


def digest(text: str) -> str:
    """A short digest of the exact bytes that would be sent, footnote included."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def batch_code(chat_key: str, texts: list[str]) -> str:
    """One code over the target and every message, in order.

    Order is inside the hash deliberately. A weekly post is three messages whose meaning depends
    on their sequence, and "same three messages, different order" is a different post.
    """
    joined = "|".join([chat_key, *(digest(t) for t in texts)])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def approval_path(paths: list[Path]) -> Path:
    """`approval.json` beside the messages — which requires them to live together.

    A batch spanning two directories has no single obvious home for its approval, and more to the
    point it is almost certainly a mistake: one drafting task is one directory, and that is the
    convention the whole `drafts/` substrate rests on.
    """
    parents = {p.resolve().parent for p in paths}
    if len(parents) != 1:
        raise Refused(
            "all messages in one batch must sit in the same directory — one drafting task, one "
            f"directory. Got {len(parents)}: " + ", ".join(str(p) for p in sorted(parents))
        )
    return parents.pop() / APPROVAL_FILE


def compose(path: Path) -> str:
    """The file's bytes plus the footnote, with the checks that make that claim true.

    Trailing whitespace is stripped from the body — a trailing newline in a text file is
    invisible on disk and arrives in Telegram as a blank line before the footnote.
    """
    if not path.exists():
        raise Refused(f"{path} does not exist")
    body = path.read_text(encoding="utf-8").rstrip()
    if not body:
        raise Refused(f"{path} is empty — refusing to send a blank message")
    if FOOTNOTE in body:
        raise Refused(
            f"{path} already contains the footnote. It is appended by the sender, not written "
            f"into the draft — otherwise it arrives twice and nothing notices."
        )
    text = body + FOOTNOTE_BLOCK
    # Verified, not assumed. This is the whole reason the footnote is not a flag.
    if not text.endswith(FOOTNOTE):
        raise Refused("footnote assembly failed — refusing to send")
    if len(text) > MAX_CHARS:
        raise Refused(
            f"{path} is {len(text):,} characters with the footnote, over Telegram's "
            f"{MAX_CHARS:,} limit. Telethon would split it silently and the formatting would "
            f"not survive. Split it yourself, deliberately."
        )
    return text


def write_approval(
    paths: list[Path],
    chat_key: str,
    display: str,
    texts: list[str],
    quote: str,
    session: str | None,
) -> Path:
    """Record that he approved *these bytes*, *to this room*, *now* — in his own words.

    `quote` is required and is not decorative. It is the one field a later reader can check
    against the conversation, and it is what turns an unapproved send from an oversight into a
    fabricated record sitting in git next to the draft.
    """
    if not quote.strip():
        raise Refused("--approve needs --quote: his own words approving this text, verbatim")
    now = datetime.now(UTC)
    path = approval_path(paths)
    path.write_text(
        json.dumps(
            {
                "chat_key": chat_key,
                "display_name": display,
                "code": batch_code(chat_key, texts),
                "messages": [
                    {"file": p.name, "digest": digest(t), "chars": len(t)}
                    for p, t in zip(paths, texts, strict=True)
                ],
                "approved_quote": quote.strip(),
                "approved_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expires_at": (now + timedelta(minutes=APPROVAL_TTL_MINUTES)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "session": session,
                "consumed_at": None,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def check_approval(paths: list[Path], chat_key: str, texts: list[str]) -> dict:
    """The gate. Returns the approval record, or refuses with the reason it does not apply.

    Every branch here is a way a send could have gone out carrying something he did not approve.
    They are separate messages rather than one "invalid approval" because *which* of them fired
    is the thing worth knowing: a drifted digest means someone edited the draft after approval,
    an expired record means the conversation moved on, and a consumed one means this batch has
    already been posted once.
    """
    path = approval_path(paths)
    if not path.exists():
        raise Refused(
            f"no approval on file at {path}. A send is gated on his in-conversation approval — "
            f"show him the preview, then record it with:\n"
            f"  neo-send --chat <chat> --approve --quote '<his words>' <files...>"
        )
    try:
        record_ = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Refused(f"{path} is not readable JSON ({exc}). Refusing.") from exc

    if record_.get("consumed_at"):
        raise Refused(
            f"that approval was already used at {record_['consumed_at']} — approvals are "
            f"single-use, so a second --commit cannot repost the same batch. Re-approve if he "
            f"actually wants it sent again."
        )
    expires = record_.get("expires_at") or ""
    if not expires or datetime.strptime(expires, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    ) < datetime.now(UTC):
        raise Refused(
            f"that approval expired at {expires or 'an unrecorded time'}. An approval is a "
            f"statement about a conversation that is happening now; ask him again."
        )
    if record_.get("chat_key") != chat_key:
        raise Refused(
            f"approved for {record_.get('display_name')} ({record_.get('chat_key')}), not for "
            f"{chat_key}. Refusing to post approved text in a different room."
        )
    approved_files = [m["file"] for m in record_.get("messages", [])]
    if approved_files != [p.name for p in paths]:
        raise Refused(
            f"the batch changed since approval. Approved: {approved_files}. "
            f"Now: {[p.name for p in paths]}. Order counts — three messages in a different "
            f"sequence are a different post."
        )
    if record_.get("code") != batch_code(chat_key, texts):
        drifted = [
            m["file"]
            for m, t in zip(record_["messages"], texts, strict=True)
            if m["digest"] != digest(t)
        ]
        raise Refused(
            "the text changed since he approved it"
            + (f" — {', '.join(drifted)}" if drifted else "")
            + ". Refusing. Show him the new version and re-approve."
        )
    return record_


def consume_approval(paths: list[Path]) -> None:
    """Mark it used, so `--commit` twice cannot post twice."""
    path = approval_path(paths)
    record_ = json.loads(path.read_text(encoding="utf-8"))
    record_["consumed_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(record_, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def record(
    path: Path, chat_key: str, display: str, text: str, stamp: str, quote: str | None = None
) -> Path:
    """Append what was sent to `sent.md` beside the message file.

    The `drafts/` template has carried a `sent.md` slot since it was written and nothing ever
    filled it, because there was no send path. This is the transparency substrate closing: the
    directory now holds the request, the context, the candidates and what actually went out.
    """
    log = path.parent / "sent.md"
    if not log.exists():
        log.write_text(
            "# Sent\n\nWhat actually went out, appended by `neo-send` at send time — "
            "not by hand.\n",
            encoding="utf-8",
        )
    with log.open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n## {stamp} UTC -> {display} (`{chat_key}`)\n\n"
            f"From `{path.name}`, {len(text):,} characters including the footnote.\n\n"
            + (f"Approved in conversation: *\u201c{quote}\u201d*\n\n" if quote else "")
            + f"```\n{text}\n```\n"
        )
    return log


async def _send(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    from neo_sensor.telegram_fetch import _client, _secure_session

    chat_key, display = resolve_target(conn, args.chat)
    # Composed for EVERY file before ANY is sent. A three-message post where the second file is
    # over the limit must fail before the first one lands, or he is left with half a report in a
    # room and no clean way to finish it.
    planned = [(path, compose(path)) for path in args.message]

    paths = [p for p, _ in planned]
    texts = [t for _, t in planned]

    print(f"target: {display}  ({chat_key})")
    for path, text in planned:
        print(f"\n--- {path} - {len(text):,} chars - digest {digest(text)} ---")
        print(text)

    if args.approve:
        record_path = write_approval(
            paths, chat_key, display, texts, args.quote or "", args.session
        )
        print(f"\nAPPROVAL RECORDED in {record_path}")
        print(f"  code {batch_code(chat_key, texts)} · expires in {APPROVAL_TTL_MINUTES} min")
        print(f"  his words: \u201c{(args.quote or '').strip()}\u201d")
        print("Single-use. Now run the same command with --commit instead of --approve.")
        return 0

    if not args.commit:
        print(f"\nPREVIEW — nothing sent. {len(planned)} message(s) would go to {display}.")
        print("Show him this, then --approve --quote '<his words>', then --commit.")
        return 0

    # The gate, BEFORE the client connects. A refusal that happens after authentication has
    # already touched the network for a send it was never going to make.
    approval = check_approval(paths, chat_key, texts)
    print(f"\napproval {approval['code']} from {approval['approved_at']}")
    print(f"  his words: \u201c{approval['approved_quote']}\u201d")

    client = _client(args)
    await client.start()
    _secure_session()
    try:
        me = await client.get_me()
        print(f"\nauthenticated as {me.first_name or ''} (id {me.id})")
        target = int(chat_key) if chat_key.lstrip("-").isdigit() else chat_key
        entity = await client.get_entity(target)
        # Telethon's own display name — the same function that produced the corpus
        # `display_name` at pull time (`dialog.name`), so the two are comparable. The previous
        # `title or first_name` dropped the surname, which made every two-part 1:1 name
        # (`Ann Lee` vs `Ann`) permanently unsendable. Found 2026-10-01.
        from telethon.utils import get_display_name

        title = get_display_name(entity) or None
        # The corpus says what the id was called at the last pull; Telegram says what it is
        # called now. The disposition is keyed on `chat_key` and is the whole authorisation, so
        # what this check actually protects is the PREVIEW: he approved a send to a room named in
        # a sentence, and if that is not its name any more he approved a description of somewhere
        # else.
        #
        # **This comment used to read "A renamed group is fine" and the code did not implement
        # it.** `Writer.note_channel` wrote `display_name` once at registration and no later pull
        # could change it, so a group renamed in Telegram became permanently unsendable — caught
        # 2026-09-07 on `Partner Assembly 2026 (UK)`, renamed to `Partner Members` some time before. The fix
        # was in `note_channel`, not here: the name now follows the rename on the next pull, so a
        # mismatch has stopped meaning "renamed at some point" and started meaning "renamed since
        # the last pull". Refusing that is right — it is the one case where the name he read is
        # demonstrably not the name of the room — and it is one pull away from resolved, which is
        # why the remedy is in the message.
        if title != display:
            raise Refused(
                f"live title {title!r} does not match the corpus display name {display!r} for "
                f"{chat_key} — the room has been renamed since the last pull, so the name in the "
                f"preview he approved is not its name any more. Run `neo-fetch pull --commit` to "
                f"refresh it, show him the corrected target, and take his approval again."
            )
        # Consumed BEFORE the first send, not after the last. A crash mid-batch must not leave
        # a live approval behind that a retry could use to repost the messages that did land.
        consume_approval(paths)
        for path, text in planned:
            sent = await client.send_message(entity, text, parse_mode=None)
            stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
            log = record(
                path, chat_key, display, text, stamp, quote=approval["approved_quote"]
            )
            print(f"sent {path.name} -> message id {sent.id}; recorded in {log}")
    finally:
        await client.disconnect()
    print(f"\n{len(planned)} message(s) sent to {display}.")
    print("Run `neo-fetch pull --commit` to bring them into the corpus as outbound rows.")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    # Read-only connection: this verb resolves a target and never writes to the corpus. The
    # messages it sends come back in on the next pull, through the ordinary redacting write path,
    # rather than being inserted here — one door in, and this is not it.
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        return asyncio.run(_send(conn, args))
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(
        prog="neo-send",
        description=(
            "Send an already-approved message file to a Telegram channel ruled `work`. "
            "The text is a file so that what is sent is what was read; the assistant footnote "
            "is appended and verified by this tool, not written into the draft."
        ),
    )
    ap.add_argument("--chat", required=True, help="exact display name or chat_key, e.g. 'IT Desk'")
    ap.add_argument(
        "message",
        type=Path,
        nargs="+",
        help="one file per message, sent in the order given. Composed and checked in full before "
        "the first one is sent.",
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    # The SAME default `neo-fetch`'s Telegram verbs use, imported rather than retyped — a send
    # path that looks for credentials somewhere else would fail with "not found" on a machine
    # where the read path works, which is the most confusing failure available here.
    from neo_sensor.graph_fetch import DEFAULT_ENV

    ap.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV,
        help="Telegram api_id/api_hash — same file `neo-fetch telegram-login` uses",
    )
    ap.add_argument(
        "--approve",
        action="store_true",
        help="record his in-conversation approval of exactly these bytes, to this chat. "
             "Requires --quote. Single-use and expires; see the module docstring for what this "
             "gate does and does not guarantee.",
    )
    ap.add_argument(
        "--quote", help="his own words approving it, verbatim. Required with --approve."
    )
    ap.add_argument("--session", help="agent session id, recorded in the approval for provenance")
    ap.add_argument(
        "--commit",
        action="store_true",
        help="send. Refused without a live, matching, unconsumed approval.",
    )
    ap.set_defaults(fn=cmd_send)
    args = ap.parse_args()
    if args.approve and args.commit:
        ap.error("--approve and --commit are separate steps. Approve, show him, then commit.")
    if args.quote and not args.approve:
        ap.error("--quote belongs with --approve")
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
