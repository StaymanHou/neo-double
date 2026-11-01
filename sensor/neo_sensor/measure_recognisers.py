"""Measure what a redaction recogniser would do to the corpus, without printing secrets.

Why this exists
---------------
Unit tests cannot tell you whether a recogniser is safe. Redaction runs **at rest**, so a false
positive is permanent corpus damage rather than one degraded prompt, and the FP rate is a
property of 84,659 real bodies rather than of the examples someone thought to write down.
``CREDIT_CARD`` passed every unit test and then fired 80 times on the corpus with a **100%**
false-positive rate, masking an ad-account number the operator works with daily.

So: before committing a re-redaction that includes a new recogniser, measure it here.

What it prints, and what it deliberately does not
-------------------------------------------------
For every match it reports the **context** (what surrounds the secret — usually enough to judge
whether the hit is real) and the **shape** of the secret (length and character classes), never
its characters. That is the same discipline as the corpus sweep in ``tests/test_fence.py``:
a bare count tells you a recogniser fires but not whether it is over-firing, and judging that
is exactly the moment you must not print the match.

**Where that is not enough.** ``STANDALONE_SECRET`` matches a whole body, so there is no
surrounding context to show and the shape alone may not settle it. For those, eyeball the raw
rows with the existing dry run, which is the operator's command and prints raw by design::

    python -m neo_sensor.redact_corpus --sample 200 --redo

Usage
-----
    python -m neo_sensor.measure_recognisers                  # all new recognisers, whole corpus
    python -m neo_sensor.measure_recognisers --entity NETWORK_CREDENTIAL
    python -m neo_sensor.measure_recognisers --limit 20       # cap the evidence lines

This reads the raw ``body`` column, so it is the **operator's** command, not the agent's — the
same posture as ``redact_corpus``.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from neo_sensor.config import STATE_DIR
from neo_sensor.redaction import (
    _BANK_ACCOUNT_REGEX,
    _BANK_ROUTING_REGEX,
    _CONSOLE_LOGIN_REGEX,
    _CREDENTIAL_ASSIGNMENT_CJK_COPULA_REGEX,
    _CREDENTIAL_ASSIGNMENT_CJK_REGEX,
    _LOGIN_LABEL_PAIR_REGEX,
    _NETWORK_CREDENTIAL_REGEX,
    _ONE_TIME_CODE_IMPERATIVE_REGEX,
    _ONE_TIME_CODE_LABEL_REGEX,
    _STANDALONE_SECRET_REGEX,
)

DEFAULT_DB = STATE_DIR / "corpus.db"

# Recognisers this tool knows how to measure. Add a new one here when you add it to
# `redaction.py`, or it ships unmeasured — which is the failure this module exists to prevent.
PATTERNS: dict[str, str] = {
    "NETWORK_CREDENTIAL": _NETWORK_CREDENTIAL_REGEX,
    "STANDALONE_SECRET": _STANDALONE_SECRET_REGEX,
    # Added 2026-08-16 (Q-043). Measured over `body_redacted` before shipping — 11, 2 and 3
    # rows — which is a LOWER BOUND, because a row another recogniser already masked cannot
    # show its shape there. This tool reads the raw column and is the authoritative count.
    "CONSOLE_LOGIN": _CONSOLE_LOGIN_REGEX,
    "LOGIN_LABEL_PAIR": _LOGIN_LABEL_PAIR_REGEX,
    "CREDENTIAL_ASSIGNMENT_CJK": _CREDENTIAL_ASSIGNMENT_CJK_REGEX,
    # Added 2026-08-17 (Q-042). `BANK_ACCOUNT` is the half that cannot be judged by count alone:
    # the recogniser gates it on a payment anchor anywhere in the message, and this tool measures
    # one pattern at a time, so the number here is the UNGATED population — 138 rows over the
    # visible corpus, of which 133 are SendGrid customer ids, Meta ad-account numbers and Rackspace
    # accounts. Read the context lines, not the total. `BANK_ROUTING` needs no such care: measured
    # against `body_redacted` it fires on 5 rows in 4 chats, every one a real wire block.
    "BANK_ROUTING": _BANK_ROUTING_REGEX,
    "BANK_ACCOUNT": _BANK_ACCOUNT_REGEX,
    # Added 2026-08-31. Measured against `body_redacted` first — 70 matches over 66 rows, every one a
    # genuine one-time code from an automated sender, zero human and zero outbound. That is a LOWER
    # BOUND for the same reason the note above gives: a row another recogniser already masked cannot
    # show its shape there. This tool reads the raw column and is the authoritative count.
    #
    # Two entries because the recogniser has two arms and this tool measures one pattern at a time.
    # `OTP_LABEL` is the qualified-noun form; `OTP_IMPERATIVE` is the one that carries no OTP noun at
    # all (`enter the following code to authorize your device…`) and needs a 100-char gap, so its
    # ungated count is the one to read the context lines on rather than trust.
    "ONE_TIME_CODE_LABEL": _ONE_TIME_CODE_LABEL_REGEX,
    "ONE_TIME_CODE_IMPERATIVE": _ONE_TIME_CODE_IMPERATIVE_REGEX,
    # Added 2026-08-31. The natural-Chinese copula form the colon pattern above cannot see. Worth 2
    # rows over the visible corpus, of 6 that carry `密码是` — the other 4 are prose and one year.
    "CREDENTIAL_ASSIGNMENT_CJK_COPULA": _CREDENTIAL_ASSIGNMENT_CJK_COPULA_REGEX,
}


def shape(s: str) -> str:
    """Describe a secret without revealing it: length plus the classes present.

    `iBbAX~mG8-a+` -> `len=12 [aA9!]`. Enough to tell a password from a product code by
    density; not enough to reconstruct one character of it.
    """
    classes = ""
    if re.search(r"[a-z]", s):
        classes += "a"
    if re.search(r"[A-Z]", s):
        classes += "A"
    if re.search(r"\d", s):
        classes += "9"
    if re.search(r"[^A-Za-z0-9]", s):
        classes += "!"
    return f"len={len(s)} [{classes}]"


def context_of(body: str, start: int, end: int, width: int = 48) -> str:
    """The text around a match, with the match itself replaced by its shape.

    This is the half that makes a hit judgeable. `Wifi: <ssid> / <secret>` reads as a real
    credential; `Network: production / <secret>` reads as a false positive — and neither
    judgement needs the secret's characters.
    """
    left = body[max(0, start - width) : start].replace("\n", " ⏎ ")
    right = body[end : end + width].replace("\n", " ⏎ ")
    return f"…{left}«{shape(body[start:end])}»{right}…"


def run(db: Path, entity: str | None, limit: int) -> int:
    wanted = {entity: PATTERNS[entity]} if entity else PATTERNS
    compiled = {name: re.compile(rx) for name, rx in wanted.items()}

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT rowid, body FROM events WHERE body <> ''").fetchall()
    finally:
        conn.close()

    print(f"{len(rows):,} non-empty bodies\n")

    hits: Counter[str] = Counter()
    evidence: dict[str, list[str]] = {name: [] for name in compiled}

    for _rowid, body in rows:
        for name, rx in compiled.items():
            for m in rx.finditer(body):
                hits[name] += 1
                if len(evidence[name]) < limit:
                    evidence[name].append(context_of(body, m.start(), m.end()))

    for name in compiled:
        n = hits[name]
        pct = 100.0 * n / len(rows) if rows else 0.0
        print(f"=== {name}: {n:,} matches ({pct:.3f}% of bodies)")
        if not n:
            print("    no matches — the recogniser is inert on this corpus, which is either")
            print("    a clean corpus or a broken pattern. Check it fires on a known example.\n")
            continue
        for line in evidence[name]:
            print(f"    {line}")
        if n > len(evidence[name]):
            print(f"    … {n - len(evidence[name]):,} more not shown (--limit to raise)")
        print()

    print("Secrets are shown as «len=N [classes]» only — never their characters.")
    print("For STANDALONE_SECRET the whole body IS the match, so there is no context to show;")
    print("eyeball those with `python -m neo_sensor.redact_corpus --sample 200 --redo`.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--entity", choices=sorted(PATTERNS), help="measure just one")
    ap.add_argument("--limit", type=int, default=12, help="max evidence lines per entity")
    args = ap.parse_args()
    if not args.db.exists():
        print(f"no corpus at {args.db}", file=sys.stderr)
        return 2
    return run(args.db, args.entity, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
