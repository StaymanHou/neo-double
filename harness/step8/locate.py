#!/usr/bin/env python3
"""Find the held-out messages in the corpus, and report what a scenario could be built on.

`heldout/MANIFEST.json` records a hash, a date, a chat and a length — deliberately not a body,
and deliberately not a timestamp. Step 8 needs both of the things it does not record:

  * the **minute** the message was sent, because a draft scenario's cutoff has to sit just
    before it. A date-only cutoff includes the whole day, which includes the answer.
  * the **context** in front of it, because a scenario with no preceding thread is not a
    drafting task, it is a guess.

So this walks the corpus through the real `neo-corpus`, matches each held-out row by sha256 of
its body, and reports what it found. Two modes:

    python3 harness/step8/locate.py                      # the candidate table — METADATA ONLY
    python3 harness/step8/locate.py --materialise <hash8> --id S-001

The table prints no message text. `--materialise` writes the ground truth and its context to
`harness/step8/ground-truth/`, which is **gitignored**: a held-out body is corpus content, and
`.gitignore` says corpus content never enters git. It is regenerable from the manifest plus the
corpus, which is the same posture `heldout/SHINGLES.json` already has.

Why it goes to the real binary and not the shim: the shim exists to clamp a *graded session*.
This is the harness, and the harness is allowed to see the answer — it is the thing holding it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys

REAL = os.environ.get("NEO_CORPUS_BIN", os.path.expanduser("~/.local/bin/neo-corpus"))
HERE = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE.parent / "heldout" / "MANIFEST.json"
GROUND_TRUTH = HERE / "ground-truth"

# How far back to look for context, and how much of it to show a scenario author.
CONTEXT_DAYS = 21
CONTEXT_LIMIT = 60


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def corpus(*args: str) -> list[dict]:
    p = subprocess.run([REAL, *args], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"locate: `neo-corpus {' '.join(args)}` failed:\n{p.stderr}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        sys.exit(f"locate: `neo-corpus {' '.join(args)}` returned no JSON:\n{p.stdout[:400]}")


def minus_a_minute(at: str) -> str:
    """The cutoff for a message sent at `at` — one minute before it.

    `--until` is inclusive to the minute, so the target's own minute must be excluded. Anything
    else in that same minute goes with it; the table flags when that costs real context.
    """
    stamp = dt.datetime.strptime(at, "%Y-%m-%dT%H:%M") - dt.timedelta(minutes=1)
    return stamp.strftime("%Y-%m-%dT%H:%M")


def load_manifest() -> tuple[list[dict], dict[str, list[dict]]]:
    m = json.loads(MANIFEST.read_text())
    entries = m["entries"]
    twins: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("twin_of"):
            twins.setdefault(e["twin_of"], []).append(e)
    return entries, twins


def locate_one(entry: dict) -> dict | None:
    """Find a held-out row in its own chat on its own date. Returns the corpus row plus `at`."""
    rows = corpus("thread", entry["chat"], "--json",
                  "--since", entry["date"], "--until", entry["date"],
                  "--direction", "outbound")
    for r in rows:
        if sha(r["text"]) == entry["body_sha256"]:
            return r
    return None


def all_copies(entry: dict) -> list[dict]:
    """Every time he sent this exact body, in send order.

    **The manifest records one date and one chat per held-out message, and that is not the same
    thing as one send.** He re-sends: the same 2,221-character budget request went to
    a leadership room and, byte-identically a week later, to a 1:1 chat. One
    hash, one manifest entry, two sends a week apart.

    That is invisible to everything else in this rig. The near-duplicate machinery covers messages
    that are *similar* and carries a `twin_of`; the shim drops every copy of a held-out hash, so
    the voice build was never at risk. But a **date cutoff** placed a minute before the later send
    leaves the earlier one fully readable, and a session that finds it is reading the answer
    verbatim under another chat's name. The first slate hit exactly that and the leak check read
    100%.

    So a scenario anchors on the **earliest** send. Anything later is a re-send he could have
    copy-pasted, and cannot be what a draft is scored against.

    Outbound only, deliberately. This catches him re-sending; it does not catch somebody quoting
    him back, which has no fixed shape and is caught after the fact by `answer-not-in-context`
    over the whole transcript.
    """
    population = corpus("outbound", "--json", "--per-channel", "1000000", "--min-length", "0")
    where = {(r["chat"], r["date"]) for r in population
             if sha(r["text"]) == entry["body_sha256"]}
    copies = []
    for chat, date in sorted(where):
        for r in corpus("thread", chat, "--json", "--since", date, "--until", date,
                        "--direction", "outbound"):
            if sha(r["text"]) == entry["body_sha256"]:
                copies.append({"chat": chat, "date": date, "at": r["at"],
                               "subject": r.get("subject", ""), "to": r.get("to", "")})
    return sorted(copies, key=lambda c: c["at"])


def context_for(entry: dict, at: str, row: dict | None = None) -> list[dict]:
    """The thread as it stood one minute before he sent.

    **Outlook does not go through `thread`, and the reason is a trap.** `chat_key` is keyed by
    sender, so `thread '<operator display name>'` holds his own sent mail and nothing else — it returns zero
    inbound for every Graph row, which reads as "he was broadcasting" when it means "the other
    half of the conversation is filed under the other party". The first version of this tool
    reported exactly that for all twelve Outlook items. Graph context comes from
    `mail --conversation`, which is grouped by `conversation_id` and holds both directions.
    """
    cutoff = minus_a_minute(at)
    if entry["channel"] == "graph":
        conv = mail_conversation(entry, (row or {}).get("subject"))
        if not conv:
            return []
        return corpus("mail", "--conversation", conv, "--json", "--until", cutoff)
    since = (dt.date.fromisoformat(entry["date"]) - dt.timedelta(days=CONTEXT_DAYS)).isoformat()
    return corpus("thread", entry["chat"], "--json",
                  "--since", since, "--until", cutoff,
                  "--limit", str(CONTEXT_LIMIT))


def mail_conversation(entry: dict, subject: str | None = None) -> str | None:
    """The Graph conversation a held-out mail belongs to — found by hashing, not by subject.

    The first version matched the message's subject against the conversation's. It failed on
    eleven of twelve Outlook items and reported them as having **no context at all**, which is
    the shape of wrongness this codebase is built to distrust: a plausible absence. The cause is
    that a conversation's subject is not its messages' subject — Graph keeps the longest form,
    so `Re: … | Workshop` sits inside `Re: … | Workshop (Tentative)` and an equality
    test says no.

    Reading each candidate conversation and hashing its rows is exact, and there are only ever a
    handful of conversations on one day. `subject` is accepted and ignored so the caller reads
    the same either way.
    """
    for c in corpus("mail", "--json", "--since", entry["date"], "--until", entry["date"]):
        rows = corpus("mail", "--conversation", c["conversation"], "--json")
        if any(sha(r["text"]) == entry["body_sha256"] for r in rows):
            return c["conversation"]
    return None


def cmd_table(args) -> int:
    entries, twins = load_manifest()
    selected = [e for e in entries if e["stratum"] != "near-duplicate"]
    if args.stratum:
        selected = [e for e in selected if e["stratum"] in args.stratum]
    selected.sort(key=lambda e: (e["stratum"], e["date"]))

    print(f"{len(selected)} selected held-out message(s)"
          f"{' in ' + ', '.join(args.stratum) if args.stratum else ''}\n")
    print(f"{'hash':9} {'stratum':17} {'sent':17} {'chat':26} {'ch':>5} {'lg':2} "
          f"{'ctx':>4} {'in':>3} {'twin':>4}  notes")

    unlocated = []
    for e in selected:
        row = locate_one(e)
        if row is None:
            unlocated.append(e)
            print(f"{e['body_sha256'][:8]}  {e['stratum']:17} {'NOT FOUND':17} "
                  f"{e['chat'][:26]:26} {e['length']:5} {e['lang']:2}")
            continue
        at = row["at"]
        ctx = context_for(e, at, row)
        inbound = [c for c in ctx if c.get("dir") == "inbound"]
        notes = []

        tw = twins.get(e["body_sha256"], [])
        if tw:
            earliest = min(t["date"] for t in tw)
            if earliest <= e["date"]:
                notes.append(f"TWIN inside window ({earliest})")
            else:
                notes.append(f"twin after ({earliest})")
        if ctx and ctx[-1]["at"] == minus_a_minute(at):
            notes.append("context in the excluded minute")
        if not ctx:
            notes.append("NO CONTEXT — not promptable")
        elif not inbound:
            notes.append("no inbound — he is broadcasting, not replying")
        if row.get("subject"):
            notes.append(f"subj: {row['subject'][:44]}")

        print(f"{e['body_sha256'][:8]}  {e['stratum']:17} {at:17} {e['chat'][:26]:26} "
              f"{e['length']:5} {e['lang']:2} {len(ctx):4} {len(inbound):3} {len(tw):4}  "
              f"{' · '.join(notes)}")

    if unlocated:
        print(f"\n{len(unlocated)} not located — a held-out row that `thread` cannot return is "
              f"one a scenario cannot be built on. Report, do not work around.")
    return 0


def cmd_materialise(args) -> int:
    entries, twins = load_manifest()
    match = [e for e in entries if e["body_sha256"].startswith(args.materialise)]
    if len(match) != 1:
        sys.exit(f"locate: {args.materialise!r} matches {len(match)} held-out entries")
    e = match[0]
    row = locate_one(e)
    if row is None:
        sys.exit(f"locate: {e['date']} {e['chat']} is not returnable through `thread`")

    # Anchor on the EARLIEST send of this body, not on the date the manifest happens to record.
    # See `all_copies`: the manifest holds one (date, chat) per hash and he re-sends, so the
    # recorded date can be the second or third time the text went out — and a cutoff placed
    # before *that* leaves an identical copy readable.
    copies = all_copies(e)
    anchor = copies[0] if copies else {"chat": e["chat"], "date": e["date"], "at": row["at"],
                                       "subject": row.get("subject", ""), "to": row.get("to", "")}
    if anchor["at"] != row["at"] or anchor["chat"] != e["chat"]:
        row = next((r for r in corpus("thread", anchor["chat"], "--json",
                                      "--since", anchor["date"], "--until", anchor["date"],
                                      "--direction", "outbound")
                    if sha(r["text"]) == e["body_sha256"]), row)
        e = {**e, "chat": anchor["chat"], "date": anchor["date"]}

    at = anchor["at"]
    cutoff = minus_a_minute(at)
    ctx = context_for(e, at, row)
    tw = twins.get(e["body_sha256"], [])

    GROUND_TRUTH.mkdir(exist_ok=True)
    head = {
        "id": args.id,
        "body_sha256": e["body_sha256"],
        "stratum": e["stratum"],
        "channel": e["channel"],
        "lang": e["lang"],
        "chat": e["chat"],
        "sent_at": at,
        "cutoff": cutoff,
        "length": e["length"],
        "subject": row.get("subject", ""),
        "to": row.get("to", ""),
        "twins": [{"date": t["date"], "chat": t["chat"], "length": t["length"]} for t in tw],
        # Every send of this exact body, earliest first. The scenario is anchored on copies[0];
        # anything else here is a re-send, and a cutoff that does not exclude all of them makes
        # the answer readable verbatim under another chat's name.
        "copies": copies,
        "context_messages": len(ctx),
    }
    body = row["text"]
    (GROUND_TRUTH / f"{args.id}.md").write_text(
        "<!-- MATERIALISED, NOT COMMITTED. A held-out body is corpus content and corpus content\n"
        "     never enters git. Rebuild with:\n"
        f"       python3 harness/step8/locate.py --materialise {e['body_sha256'][:8]} "
        f"--id {args.id}\n"
        "     The judge reads this. A graded session must never see it. -->\n"
        "```json\n" + json.dumps(head, indent=2, ensure_ascii=False) + "\n```\n\n"
        "## The message he actually sent\n\n" + body + "\n"
    )
    (GROUND_TRUTH / f"{args.id}-context.md").write_text(
        f"<!-- The thread as it stood at {cutoff}: what the graded session can reach, and\n"
        "     nothing after. Same posture as the ground truth — gitignored, regenerable. -->\n\n"
        + "\n\n".join(
            f"**{c['at']}  {c.get('dir', '?')}  {c.get('from', '')}**"
            + (f"  _{c['subject']}_" if c.get("subject") else "")
            + "\n\n" + c["text"]
            for c in ctx
        ) + "\n"
    )
    print(f"wrote ground-truth/{args.id}.md  ({e['length']}ch, sent {at} in {e['chat']}, "
          f"cutoff {cutoff})")
    print(f"wrote ground-truth/{args.id}-context.md  ({len(ctx)} message(s))")
    if len(copies) > 1:
        print(f"  SENT {len(copies)} TIMES — anchored on the earliest. Later re-sends:")
        for c in copies[1:]:
            print(f"    {c['at']}  {c['chat']}")
        print("  The scenario cutoff must exclude every one of them, which anchoring on the "
              "earliest does automatically.")
    if tw:
        print(f"  {len(tw)} twin(s): " + ", ".join(f"{t['chat']}@{t['date']}" for t in tw)
              + "  — check the cutoff sits before the earliest, or drop this item.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stratum", nargs="*", help="restrict the table to these strata")
    ap.add_argument("--materialise", metavar="HASH8",
                    help="write one ground truth + its context to ground-truth/")
    ap.add_argument("--id", help="scenario id to file the materialised ground truth under")
    a = ap.parse_args()
    if a.materialise:
        if not a.id:
            sys.exit("locate: --materialise needs --id")
        return cmd_materialise(a)
    return cmd_table(a)


if __name__ == "__main__":
    raise SystemExit(main())
