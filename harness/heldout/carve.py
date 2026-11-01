#!/usr/bin/env python3
"""Carve step 8's held-out evaluation set out of the operator's outbound messages.

Step 8 scores drafts against messages he actually sent. If step 7's voice build reads those
messages, step 8 grades the voice model on its own input and passes regardless of quality.
So the evaluation set is chosen and withheld BEFORE `self/style.md` exists — that ordering is
the whole point, and it is why this script runs first and once.

**The error is one-way.** Holding out too many messages is recoverable: step 8 scores on a
subset and the rest sit unused. Holding out too few is not — a message that has been read into
the voice model cannot be taken back out, and nothing downstream would notice. The strata below
are therefore sized toward over-holding.

WHAT THIS SCRIPT READS AND WHAT IT WRITES. It reads bodies, because it has to measure their
length and script. It never writes one: `MANIFEST.json` carries a sha256 and metadata per row
and nothing else. Reading the manifest does not tell you what he said.

    python3 harness/heldout/carve.py            # refuses if MANIFEST.json exists
    python3 harness/heldout/carve.py --force    # overwrite (you almost certainly do not want to)

Re-running on a LARGER corpus would select a different set, because the strata are relative to
the population. That is why the manifest is the artefact of record rather than this script:
`--force` is a way to void step 8, not a way to refresh it.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE / "MANIFEST.json"
SHINGLES = HERE / "SHINGLES.json"

# Sidecar to the manifest: a one-way digest of every 32-character window of every held-out
# message, so the shim can recognise held-out CONTENT without ever holding a held-out BODY.
#
# It exists because exact-hash filtering cannot see a quotation. On Outlook a reply quotes the
# message it answers, so a held-out message comes back through the thread inside somebody
# else's row, hashing to something entirely different. Measured: 44% of one held-out message
# was recoverable from its own thread after exact-hash filtering had run clean.
#
# 32 characters is calibrated, not chosen. Containment was measured across a boundary where the
# true answer is known to be zero — a Graph message cannot appear in a Telegram-only file — and
# that noise floor falls from 0.51 at k=8 (which is to say, English) to 0.03 at k=32. Below
# k=16 the measure reports background language and is worse than useless, because it fires
# hardest on the longest and most ordinary messages.
SHINGLE_K = 32
DIGEST_CHARS = 12

# The population. Default filters are deliberate and each excludes something that is not his
# prose: `--include-suppressed` would add corpus noise (a bot-relayed daily report among it),
# `--include-forwarded` would add messages he sent but did not write. `--min-length 0` is NOT a
# default — `outbound` ships with `--min-length 80`, which silently drops 71% of his Telegram
# outbound, and a population estimate built on it is a population estimate of his long third.
OUTBOUND_CMD = [
    "neo-corpus", "outbound", "--json",
    "--per-channel", "1000000",
    "--min-length", "0",
]

# A message counts as Mandarin when this fraction of its characters are CJK. The corpus has
# almost no mixed-script prose at the message level — the ratio is bimodal, clustering near 0
# and near 0.5 (Latin punctuation, product names and URLs sit inside otherwise-Chinese
# sentences) — so the threshold is not delicate.
CJK_RATIO = 0.15

# A message is held out as a near-duplicate when this fraction of a SELECTED message's content
# also appears in it. Set from measurement rather than taste: at 0.5 the three real broadcasts
# in this corpus are caught (0.99, 0.82, 0.79) and the highest innocent overlap — a reused
# boilerplate paragraph — sits at 0.30, so the gap either side of the line is wide.
TWIN_CONTAINMENT = 0.5

# Each register gets its own definition of "long", because a single global threshold does not
# survive contact with this corpus. His Mandarin median is 19 characters against English's 54,
# and only 28 Mandarin messages in 5,236 reach 400 characters. Thresholding at 400 globally
# would produce a held-out set that is ~96% English and would never test the register he uses
# with several correspondents — where a draft in the
# wrong language is wrong before it is read. The operator confirmed both registers are in
# scope on 2026-08-16.
#
# `spread` is the axis breadth is taken across. For Telegram that is the chat, because the
# thing step 8 measures is register calibration per correspondent. For Outlook it is the year:
# `chat_key` is keyed by sender, so every outbound mail he has ever sent sits in one bucket
# named after him, and spreading across it would select nothing.
STRATA = [
    {
        "name": "outlook-long",
        "channel": "graph", "lang": "en", "min_len": 400, "max_len": None,
        "n": 12, "spread": "year", "cap": 6,
        "why": "his work-facing register, and the scarcest material in the corpus",
    },
    {
        "name": "telegram-en-long",
        "channel": "telegram", "lang": "en", "min_len": 400, "max_len": None,
        "n": 30, "spread": "chat", "cap": 3,
        "why": "the bulk of his long-form internal writing",
    },
    {
        "name": "telegram-zh-long",
        "channel": "telegram", "lang": "zh", "min_len": 200, "max_len": None,
        "n": 12, "spread": "chat", "cap": 3,
        "why": "the Mandarin register at its own long tail; 200 chars is its p99",
    },
    {
        "name": "telegram-en-mid",
        "channel": "telegram", "lang": "en", "min_len": 120, "max_len": 300,
        "n": 6, "spread": "chat", "cap": 2,
        "why": "control group — if drafts score well here and badly on the long strata, "
               "the finding is about length rather than about voice",
    },
]


def cjk_ratio(s: str) -> float:
    n = sum(1 for c in s if "一" <= c <= "鿿" or "぀" <= c <= "ヿ")
    return n / max(1, len(s))


def body_hash(text: str) -> str:
    """Identity of a message body.

    The shim filters on this alone, because `thread` emits rows carrying neither a chat nor a
    channel — there is no other field common to every verb. Collision would need two distinct
    messages with byte-identical bodies; every row here is at least 120 characters, so a
    collision is a genuine duplicate and dropping both is correct.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def row_hash(r: dict) -> str:
    """Stable ordering key. Content-derived, so the selection is reproducible and carries no
    intent — it cannot be nudged toward messages that flatter the voice model."""
    raw = "\x00".join((r["channel"], r["date"], r["chat"], r["text"]))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def shingles(text: str, k: int = 8) -> set[str]:
    return {t[i:i + k] for t in [" ".join(text.split())] for i in range(max(1, len(t) - k + 1))}


def shingle_digests(text: str) -> set[str]:
    """Hashed 32-char windows — recognisable, not readable."""
    t = " ".join(text.split())
    return {hashlib.sha256(t[i:i + SHINGLE_K].encode("utf-8")).hexdigest()[:DIGEST_CHARS]
            for i in range(max(1, len(t) - SHINGLE_K + 1))}


def near_twins(picked: list[dict], population: list[dict], threshold: float) -> list[dict]:
    """Messages that reproduce most of a held-out message without being byte-identical to it.

    He broadcasts: the same 1,300-character message goes to three people on the same day with
    the greeting changed. Byte-identical dedup does not see that, so a held-out message can sit
    in the training sample under another correspondent's name at 97% similarity — and step 8
    would then score a draft against text the voice model had effectively read.

    Containment rather than similarity, and measured in the held-out message's direction: the
    question is *how much of the withheld message is available elsewhere*, not whether the two
    are alike overall. A 250-character passage he reuses across many messages is normal and
    lands far below the threshold; a re-sent message lands near 1.0.
    """
    held_shingles = [(r, shingles(r["text"])) for r in picked]
    held_hashes = {body_hash(r["text"]) for r in picked}
    twins: dict[str, dict] = {}
    for other in population:
        h = body_hash(other["text"])
        if h in held_hashes or h in twins or len(other["text"]) < 100:
            continue
        os_ = shingles(other["text"])
        for r, hs in held_shingles:
            if len(hs & os_) / len(hs) >= threshold:
                twins[h] = {**other, "_twin_of": body_hash(r["text"])}
                break
    return list(twins.values())


def fetch() -> list[dict]:
    proc = subprocess.run(OUTBOUND_CMD, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"carve: `{' '.join(OUTBOUND_CMD)}` failed:\n{proc.stderr}")
    rows = json.loads(proc.stdout)
    for r in rows:
        r["length"] = len(r["text"])
        r["lang"] = "zh" if cjk_ratio(r["text"]) >= CJK_RATIO else "en"
        r["year"] = r["date"][:4]
    return rows


def select(rows: list[dict], stratum: dict) -> list[dict]:
    """Breadth first, then depth, deterministically.

    Candidates are grouped by the stratum's spread axis, each group ordered by content hash.
    Selection then walks round-robin across groups: one from each before a second from any. For
    an evaluation set breadth beats proportionality — 30 messages to 30 different correspondents
    tests register calibration 30 times, where 30 messages to one team room tests it once.
    """
    pool = [
        r for r in rows
        if r["channel"] == stratum["channel"]
        and r["lang"] == stratum["lang"]
        and r["length"] >= stratum["min_len"]
        and (stratum["max_len"] is None or r["length"] < stratum["max_len"])
    ]
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in pool:
        groups[r[stratum["spread"]]].append(r)
    for g in groups.values():
        g.sort(key=row_hash)
    # Largest group first, name as tiebreak: entirely determined by the data, no clock, no seed.
    order = sorted(groups, key=lambda k: (-len(groups[k]), k))

    picked: list[dict] = []
    for depth in range(stratum["cap"]):
        for key in order:
            if len(picked) >= stratum["n"]:
                break
            if depth < len(groups[key]):
                picked.append(groups[key][depth])
        if len(picked) >= stratum["n"]:
            break
    return pool, picked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing manifest — voids every step-8 result taken "
                         "against the old one")
    ap.add_argument("--shingles-only", action="store_true",
                    help="rebuild SHINGLES.json from the existing manifest without reselecting. "
                         "The digests are derived from the corpus so they are not committed, "
                         "and re-running the full carve on a grown corpus would pick a "
                         "different set — this is how a fresh checkout gets them back.")
    ap.add_argument("--carved-on", required=True, metavar="YYYY-MM-DD",
                    help="today's date, passed in rather than read from the clock so a re-run "
                         "of this script is byte-reproducible")
    args = ap.parse_args()

    if args.shingles_only:
        entries = json.loads(MANIFEST.read_text())["entries"]
        by_hash = {body_hash(r["text"]): r["text"] for r in fetch()}
        missing = [e for e in entries if e["body_sha256"] not in by_hash]
        if missing:
            sys.exit(f"carve: {len(missing)} of {len(entries)} held-out messages are no longer "
                     f"reachable in the corpus. The digests would be incomplete, and an "
                     f"incomplete clamp is one that reports clean while leaking. Refusing.")
        SHINGLES.write_text(json.dumps({
            "what": "Regenerated from MANIFEST.json; selection untouched.",
            "k": SHINGLE_K, "digest_chars": DIGEST_CHARS, "carved_on": args.carved_on,
            "sets": {e["body_sha256"]: sorted(shingle_digests(by_hash[e["body_sha256"]]))
                     for e in entries},
        }) + "\n")
        print(f"wrote {SHINGLES} for {len(entries)} held-out messages (selection unchanged)")
        return 0

    if MANIFEST.exists() and not args.force:
        sys.exit(f"carve: {MANIFEST} already exists. The held-out set is carved once, before "
                 f"the voice build reads anything. Re-carving on a corpus that has since grown "
                 f"selects a different set and voids any step-8 result taken against the old "
                 f"one. `--force` if that is genuinely what you mean.")

    stats = subprocess.run(["neo-corpus", "stats"], capture_output=True, text=True).stdout

    rows = fetch()
    entries, report = [], []
    seen: set[str] = set()
    chosen: list[dict] = []
    for stratum in STRATA:
        pool, picked = select(rows, stratum)
        chosen.extend(picked)
        for r in picked:
            h = body_hash(r["text"])
            if h in seen:      # a body identical across two strata; hold it out once
                continue
            seen.add(h)
            entries.append({
                "stratum": stratum["name"],
                "channel": r["channel"],
                "date": r["date"],
                "chat": r["chat"],
                "length": r["length"],
                "lang": r["lang"],
                "body_sha256": h,
            })
        report.append({
            "stratum": stratum["name"],
            "population": len(pool),
            "requested": stratum["n"],
            "selected": len(picked),
            "distinct_groups_available": len({r[stratum["spread"]] for r in pool}),
            "distinct_groups_used": len({r[stratum["spread"]] for r in picked}),
            "spread": stratum["spread"],
        })

    twins = near_twins(chosen, rows, TWIN_CONTAINMENT)
    for t in twins:
        h = body_hash(t["text"])
        if h in seen:
            continue
        seen.add(h)
        entries.append({
            "stratum": "near-duplicate",
            "channel": t["channel"], "date": t["date"], "chat": t["chat"],
            "length": len(t["text"]), "lang": t["lang"],
            "body_sha256": h,
            "twin_of": t["_twin_of"],
        })

    manifest = {
        "what": "Step 8's held-out evaluation set. These messages are withheld from step 7's "
                "voice build so that step 8 does not grade the voice model on its own input.",
        "near_duplicates": f"{len(twins)} further message(s) are held out because at least "
                           f"{TWIN_CONTAINMENT:.0%} of a selected message's content appears in "
                           f"them. He broadcasts — the same message goes to several people with "
                           f"the greeting changed — and a byte-identical check does not see it.",
        "carved_on": args.carved_on,
        "carved_by": "harness/heldout/carve.py",
        "population_command": " ".join(OUTBOUND_CMD),
        "corpus_at_carve_time": stats.strip(),
        "cjk_ratio_threshold": CJK_RATIO,
        "strata": [{k: v for k, v in s.items()} for s in STRATA],
        "coverage": report,
        "total_held_out": len(entries),
        "bodies": "NOT RECORDED. Each row is identified by sha256 of its body. Reading this "
                  "file tells you that a message exists, not what it says.",
        "entries": sorted(entries, key=lambda e: (e["stratum"], e["date"], e["body_sha256"])),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    by_hash = {body_hash(r["text"]): r["text"] for r in rows}
    sidecar = {
        "what": "One-way 32-character shingle digests of every held-out message, so a reader "
                "can detect held-out CONTENT without being able to reconstruct it. This is what "
                "catches a held-out message quoted back inside somebody else's reply.",
        "k": SHINGLE_K,
        "digest_chars": DIGEST_CHARS,
        "carved_on": args.carved_on,
        "sets": {e["body_sha256"]: sorted(shingle_digests(by_hash[e["body_sha256"]]))
                 for e in entries if e["body_sha256"] in by_hash},
    }
    SHINGLES.write_text(json.dumps(sidecar) + "\n")

    print(f"wrote {MANIFEST}  —  {len(entries)} messages held out\n")
    for c in report:
        print(f"  {c['stratum']:<18} {c['selected']:>3} of {c['population']:>5} "
              f"({c['distinct_groups_used']}/{c['distinct_groups_available']} {c['spread']}s)")
    print(f"  {'near-duplicate':<18} {len(twins):>3}  (>={TWIN_CONTAINMENT:.0%} of a selected "
          f"message's content appears in them)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
