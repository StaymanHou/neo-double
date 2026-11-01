"""`neo-fetch telegram-login` / `neo-fetch pull --channel telegram` — the Telegram half.

Telegram is ~98% of this corpus by volume and almost all of the operator's own prose, so it is
the channel that decides whether the knowledge base is current in any useful sense. The
predecessor observed it over a Telethon user session; that path did not survive the migration,
and nothing here has inserted a Telegram row since. This module is the replacement, and it is
deliberately a *pull*, not a live observer: there is no daemon in this design, the operator
invokes a catch-up when he wants one.

**Reads only.** `iter_dialogs` and `iter_messages` are the entire surface. Nothing marks a
message read, nothing downloads media bytes, nothing sends. A media-only message still gets a
row — a text placeholder composed from metadata already on the fetched object — because "he
sent a photo here at 14:02" is context and a blank row is not.

**`forward_from` is captured, and that is the point of the rewrite.** The historical corpus
attributes a relayed message to whoever relayed it, so at minimum 44 rows have the operator
greeting himself and a voice model over raw `outbound` trains partly on other people's writing.
Operator's ruling 2026-08-15 (Q-031): capture it. Note what this does and does not fix — a
*forwarded* message carries `fwd_from` and is now recorded as one; a message someone
**pasted** carries nothing at all, and no schema can recover it. The historical rows stay
contaminated either way; this only stops the contamination growing.

**Direction is author-derived.** `outbound` means the operator wrote it, decided by comparing
the sender id against his own, not by trusting a flag. Where a message has no sender at all —
an anonymous channel post — the flag is the only thing available and the row says so in
`direction_source`.

**A dialog the operator has not ruled on is pulled but not readable.** New chats land as
`undecided`, which withholds the name, the volume and every body until he decides. Chats he has
ruled `personal` or `ignore` are not pulled at all: continuing to copy his personal life into a
store he told me to stay out of is not something the fence should have to catch.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

from neo_sensor.config import STATE_DIR
from neo_sensor.ingest import (
    IncomingEvent,
    PendingChannel,
    Writer,
    as_utc,
    corpus_high_water,
    corpus_stamp,
    dispositions,
    ensure_schema,
    get_cursor,
    pending_floors,
    rebuild_index,
    report,
    set_cursor,
    telegram_watermarks,
)

SESSION_PATH = STATE_DIR / "telegram.session"
SESSION_MODE = 0o600

# His Telegram user id. Configuration, not a constant: a constant in a file is documentation, and
# the next operator would inherit it. With it unset every row would be stored `inbound` — silently
# wrong — so the client refuses to start without it.
OPERATOR_ID = os.environ.get("NEO_TELEGRAM_OPERATOR_ID", "")

# Dispositions this must not touch. `personal` and `ignore` are decisions he already made.
SKIP = ("personal", "ignore")

# Telethon sleeps through a flood-wait shorter than this and raises beyond it. A catch-up is
# not latency-sensitive, so a generous threshold turns "the run failed" into "the run took
# longer", which is the better failure for something the operator invokes by hand.
FLOOD_THRESHOLD = 300


def credentials(env_file: Path) -> tuple[int, str]:
    """`api_id` / `api_hash` from the environment or an env file — the same shape `graph_fetch`
    uses, and the same discipline: only the two keys wanted are read, and neither is printed."""
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    if not (api_id and api_hash) and env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key == "TELEGRAM_API_ID" and not api_id:
                api_id = value
            elif key == "TELEGRAM_API_HASH" and not api_hash:
                api_hash = value
    if not (api_id and api_hash):
        raise SystemExit(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH not found in the environment or "
            f"{env_file}. Pass --env-file, or export them."
        )
    return int(api_id), api_hash


def _client(args: argparse.Namespace):  # noqa: ANN202
    from telethon import TelegramClient

    if not OPERATOR_ID:
        sys.exit("NEO_TELEGRAM_OPERATOR_ID is not set — without it every message is stored as inbound.")
    api_id, api_hash = credentials(args.env_file)
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(SESSION_PATH), api_id, api_hash, flood_sleep_threshold=FLOOD_THRESHOLD
    )


def _secure_session() -> None:
    """0600 the session file. It authenticates as him; it is a credential in every real sense."""
    for path in (SESSION_PATH, SESSION_PATH.with_suffix(".session-journal")):
        if path.exists():
            os.chmod(path, SESSION_MODE)


# --------------------------------------------------------------------------- media


def media_placeholder(message) -> str:  # noqa: ANN001
    """A text stand-in for a media-only message, composed from metadata already fetched.

    Ported from the predecessor's observer, which had it right: no bytes are downloaded, every
    read here is attribute access on the object already in hand, and anything without a typed
    accessor still yields `[media]` rather than an empty body. A media-only message is never a
    blank row — "he sent a document called X" is context.
    """
    if message.media is None:
        return ""
    file = message.file
    if message.photo:
        return "[photo]"
    if message.voice:
        secs = getattr(file, "duration", None)
        return f"[voice {secs}s]" if secs is not None else "[voice]"
    if message.video_note:
        return "[video note]"
    if message.video or message.gif:
        return "[gif]" if message.gif else "[video]"
    if message.audio:
        name = getattr(file, "name", None)
        return f"[audio: {name}]" if name else "[audio]"
    if message.sticker:
        emoji = getattr(file, "emoji", None)
        return f"[sticker {emoji}]" if emoji else "[sticker]"
    if message.document:
        name = getattr(file, "name", None)
        return f"[document: {name}]" if name else "[document]"
    if message.contact:
        return "[contact]"
    if message.geo or message.venue:
        return "[location]"
    if message.poll:
        return "[poll]"
    return "[media]"


def forwarded_from(message) -> str | None:  # noqa: ANN001
    """Who wrote a forwarded message, or None when it is not a forward.

    Every branch reads state already on the message. Resolving a bare id to a display name
    would be a network round-trip per row, and an id that cannot be resolved is still the fact
    that this is not his writing — which is the whole reason the column exists. A forward whose
    original author hides their account yields `(hidden)` rather than None, because "somebody
    else wrote this" is exactly what must not be lost.
    """
    fwd = getattr(message, "forward", None)
    if fwd is None:
        return None
    name = getattr(fwd, "from_name", None)
    if name:
        return str(name)
    for attr in ("sender_id", "chat_id", "channel_id"):
        value = getattr(fwd, attr, None)
        if value:
            return f"id:{value}"
    return "(hidden)"


def spoiler_spans(message) -> list[tuple[int, int]]:  # noqa: ANN001
    """Char spans the sender marked as a spoiler, converted out of Telegram's UTF-16 offsets.

    His request, 2026-08-21: *"Can it also redact the texts in the 'spoiler' format in any
    Telegram message? … let's properly redact that kind of thing moving forward"*.

    **This information was being thrown away.** `to_event` stored `message.message` — the plain
    text — and never looked at `message.entities`, where Telegram puts the formatting. So the
    fact that a span was hidden did not survive ingest, which is why no recogniser could ever
    have caught it: there was nothing in the stored text to recognise. It is not a filter gap,
    it is a capture gap, and it is the same shape as the Outlook `subject`/`toRecipients` loss
    that `neo-fetch enrich` had to re-fetch the mailbox to repair.

    Only applies when the body IS the message text: an empty-text message gets
    `media_placeholder()` instead, and an entity offset into a placeholder means nothing.
    """
    if not getattr(message, "message", None):
        return []
    # Deferred like every other import of this module: `neo_sensor.redaction` loads spaCy at
    # import time (~2s), and `to_event` is unit-tested without it.
    from neo_sensor.redaction import utf16_span_to_chars

    spans: list[tuple[int, int]] = []
    for entity in getattr(message, "entities", None) or []:
        if type(entity).__name__ == "MessageEntitySpoiler":
            spans.append(utf16_span_to_chars(message.message, entity.offset, entity.length))
    return spans


def to_event(message, chat_key: str, chat_display: str | None) -> IncomingEvent:  # noqa: ANN001
    """A Telethon message as a corpus row.

    `external_id` stays `<chat_id>:<message_id>` — the predecessor's composite, kept because
    `chat_key` is derived from it by splitting on the first colon and 86,182 existing rows use
    that shape. Changing it would orphan every channel disposition keyed on the result.
    """
    if message.sender_id is not None:
        direction = "outbound" if str(message.sender_id) == OPERATOR_ID else "inbound"
        source = "author"
    else:
        # An anonymous channel post has no author to compare against. The flag is all there is,
        # and the row records that a weaker test was used rather than pretending otherwise.
        direction = "outbound" if getattr(message, "out", False) else "inbound"
        source = "out-flag"

    return IncomingEvent(
        channel="telegram",
        external_id=f"{message.chat_id}:{message.id}",
        chat_key=chat_key,
        observed_at=message.date.isoformat(),
        sender=str(message.sender_id) if message.sender_id is not None else None,
        direction=direction,
        direction_source=source,
        body=message.message or media_placeholder(message),
        chat_display=chat_display,
        forwarded_from=forwarded_from(message),
        spoiler_spans=spoiler_spans(message),
    )


# --------------------------------------------------------------------------- verbs


def should_pull(disposition: str | None) -> bool:
    """Whether a dialog is walked at all.

    `None` — a chat with no row yet — is pulled: that is how a new conversation becomes visible
    to the operator as something to rule on. `work`, `mixed` and `undecided` are pulled.
    `personal` and `ignore` are not: he already ruled on those, and continuing to copy them into
    the store would make the fence responsible for hiding what should never have been fetched.
    """
    return disposition not in SKIP


def entity_identity(entity, dialog_name: str | None) -> tuple[str | None, str | None, str]:  # noqa: ANN001
    """`(display, username, kind)` for one dialog's entity.

    `dialog.name` is Telethon's `get_display_name`, which is a chat's `title` or a user's
    `first_name last_name` — and **nothing** when a user has neither. That is not a rare edge:
    a deleted account, a service account and some bots all present that way, and the corpus met
    one on 2026-08-24 (`9000000001`) which then reached the operator as a bare numeric id with
    no other signal. The `username` was sitting on the entity the whole time and nothing looked
    at it.

    `kind` is recorded because it changes the ruling: a bot in a 1:1 and a person in a 1:1 are
    the same shape in the old output and different decisions.
    """
    username = getattr(entity, "username", None) or None
    if entity is None:
        return dialog_name or None, username, "unknown"
    cls = type(entity).__name__
    if cls == "User":
        kind = "bot" if getattr(entity, "bot", False) else "user"
        if getattr(entity, "deleted", False):
            kind = "deleted-account"
    elif cls == "Chat":
        kind = "group"
    elif cls == "Channel":
        kind = "group" if getattr(entity, "megagroup", False) else "broadcast-channel"
    else:
        kind = cls.lower()
    return (dialog_name or None), username, kind


def resume_kwargs(
    *,
    is_new: bool,
    watermark: int | None,
    floor_dt: object | None,
    limit: int = 0,
) -> dict:
    """Where one dialog's walk should start.

    Two different resume points, because two different situations. A chat already in the corpus
    resumes from its own highest message id — exact, per-chat, and immune to clock skew or to a
    message whose date is not when it arrived. A chat never seen before has no such mark, so the
    date floor bounds it instead; without that bound, admitting a dialog today would drag its
    entire history in, which is neither wanted nor cheap.
    """
    kwargs: dict = {"reverse": True}
    if not is_new and watermark is not None:
        kwargs["min_id"] = watermark
    elif floor_dt is not None:
        kwargs["offset_date"] = floor_dt
    if limit:
        kwargs["limit"] = limit
    return kwargs


def cmd_login(args: argparse.Namespace) -> int:
    """Interactive sign-in. The operator runs this — it wants a phone number and an SMS code."""

    async def _go() -> int:
        client = _client(args)
        await client.start()
        _secure_session()
        me = await client.get_me()
        print(f"signed in as {me.first_name} (id {me.id}); session at {SESSION_PATH}")
        if str(me.id) != OPERATOR_ID:
            print(
                f"WARNING: expected operator id {OPERATOR_ID}. Every direction on every row "
                "pulled by this session would be decided against the wrong identity.",
            )
        await client.disconnect()
        return 0

    return asyncio.run(_go())


async def pull(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Walk every admitted dialog forward from where the corpus already ends."""
    from telethon import errors

    if args.commit:
        ensure_schema(conn)
    known = dispositions(conn)
    marks = telegram_watermarks(conn)
    pending = pending_floors(conn)
    floor = args.since or get_cursor(conn, "telegram") or corpus_high_water(conn, "telegram")
    dry = "" if args.commit else "  [DRY RUN]"
    print(f"resuming from {floor or 'the beginning of each dialog'}{dry}")

    client = _client(args)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit(
            f"no authorised Telegram session at {SESSION_PATH} — "
            "run `neo-fetch telegram-login` first (it needs a phone and a code)"
        )
    _secure_session()

    writer = Writer(conn, commit=args.commit)
    skipped = scanned = 0
    newest = floor

    try:
        async for dialog in client.iter_dialogs():
            chat_key = str(dialog.id)
            disposition = known.get(chat_key)
            if not should_pull(disposition):
                skipped += 1
                continue
            if args.chat and args.chat not in (chat_key, dialog.name):
                continue

            # A dialog he has never ruled on. `--include-new` stores it anyway; the default
            # counts it and leaves it alone. `pending_floor` is per-chat so that admitting it
            # later resumes from where THIS run stopped, not from a cursor that has moved on.
            #
            # `undecided` counts as never-ruled, and reading this as `is None` was a live defect
            # until 2026-08-24. `register_pending` writes an `undecided` row, so the FIRST pull
            # held a chat back correctly and the SECOND one — seeing `'undecided'` rather than
            # `None` — walked straight past this test and stored it. It survived because it
            # needs two pulls with no ruling in between, and until that day every registration
            # had been ruled on before the next pull. It then stored 14 messages from four
            # never-ruled conversations, one of them a family chat: `undecided` still withheld
            # them from being READ, which is exactly the "fence standing between his family chat
            # and a database" shape `register_pending` exists to refuse.
            unruled = disposition in (None, "undecided")
            start = pending.get(chat_key) or floor
            chat_floor = as_utc(start) if start else None
            kwargs = resume_kwargs(
                is_new=chat_key not in marks,
                watermark=marks.get(chat_key),
                floor_dt=chat_floor,
                limit=args.limit_per_chat,
            )

            found = 0
            # For an unruled dialog these three are the only things kept from the walk. The walk
            # is `reverse=True`, i.e. oldest-first, so the last assignment wins and holds the
            # NEWEST message — which is the one the operator asked to see. Nothing is stored;
            # the body is handed to `register_pending`, which redacts and truncates it there.
            #
            # `last_out` is the third, added 2026-09-07, and without it the other two make a
            # false claim. The newest message in a 1:1 is the operator's own about half the
            # time, and `channels-pending` was printing it as what the account said — the whole
            # point of the preview being that he can recognise a person by their words. Telethon's
            # `out` is author-derived (see `BACKFILL_SOURCE`), so it is the right flag and not an
            # inference from the folder a message sits in.
            last_at: object | None = None
            last_body: str | None = None
            last_out: bool | None = None
            try:
                async for message in client.iter_messages(dialog.entity, **kwargs):
                    scanned += 1
                    if unruled and not args.include_new:
                        found += 1
                        last_at = message.date
                        last_body = message.message or media_placeholder(message)
                        last_out = bool(message.out)
                        continue
                    event = to_event(message, chat_key, dialog.name or None)
                    if not found:
                        writer.note_channel(chat_key, "telegram", dialog.name or None)
                    if writer.write(event):
                        found += 1
                        if newest is None or event.observed_at > newest:
                            newest = event.observed_at
            except (errors.ChannelPrivateError, errors.ChatAdminRequiredError) as exc:
                print(f"  {dialog.name}: unreadable ({type(exc).__name__})")
                continue

            if not found:
                # No traffic in the window. Registering it would put a channel with nothing
                # behind it on the operator's decision list — 631 of those on the first run.
                continue
            label = (dialog.name or chat_key)[:44]
            if unruled and not args.include_new:
                display, username, kind = entity_identity(dialog.entity, dialog.name)
                if args.commit:
                    writer.register_pending(
                        chat_key, "telegram", display, found,
                        pending.get(chat_key, floor or ""),
                        username=username, kind=kind,
                        last_at=last_at, last_body=last_body, last_out=last_out,
                    )
                else:
                    writer.pending[chat_key] = PendingChannel(
                        channel="telegram", display=display, count=found,
                        username=username, kind=kind,
                        last_at=corpus_stamp(last_at) if last_at else None,
                        preview=writer.preview_of(last_body),
                        last_out=last_out,
                    )
                label = (display or (f"@{username}" if username else chat_key))[:44]
                print(f"  {label:<44} {found:>4} NOT STORED — awaiting your disposition")
            else:
                writer.flush()
                print(f"  {label:<44} +{found}")

        writer.flush()
    finally:
        await client.disconnect()

    print(f"\nscanned {scanned:,} messages across the admitted dialogs")
    print(f"skipped {skipped} dialog(s) ruled personal or ignore")
    report(writer, commit=args.commit)

    if args.commit:
        if newest:
            set_cursor(conn, "telegram", newest, f"pull; {writer.written} written")
        conn.commit()
        rebuild_index(conn)
    return 0


def cmd_pull(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    return asyncio.run(pull(conn, args))
