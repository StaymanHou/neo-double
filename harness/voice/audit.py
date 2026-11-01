#!/usr/bin/env python3
"""Prove that no held-out message reached the voice build.

Two jobs, run at two different moments:

    python3 harness/voice/audit.py --sample <dir>        # BEFORE reading — is the sample clean?
    python3 harness/voice/audit.py --files a.md b.md     # AFTER writing — did a quote leak in?

The shim is a clamp on the ordinary read path, not a proof. This is the measurement, and it is
deliberately independent of the shim: it goes to the REAL binary, pulls the held-out bodies, and
looks for them in whatever it was pointed at. If the shim ever goes inert the way its `search`
path silently did, this is what says so.

A hit voids the build. It means a held-out message reached the voice model by some route the
clamp does not cover, and step 8's score against that model would be measuring nothing.

No message bodies are printed — a hit is reported by hash, date, chat and length.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys

REAL = os.environ.get("NEO_CORPUS_BIN", os.path.expanduser("~/.local/bin/neo-corpus"))
MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "heldout" / "MANIFEST.json"

# Measured, not chosen by feel. In this corpus the innocent maximum — a boilerplate paragraph
# he reuses — is 30%, and a genuine re-send is 79% or above. Anything between is reported and
# not acted on, which is where a new failure shape would first show itself.
#
# The window length matters more than the threshold. At k=8 this measure reports the English
# language: containment across a boundary where the true answer must be zero — a Graph message
# inside a Telegram-only file — read 0.51. At k=32 the same impossible comparison reads 0.03.
# The first version of this audit used short substrings and declared three leaks, two of which
# were reused boilerplate; it would have voided a sound build.
K = 32
LEAK = 0.30
NOTE = 0.15


def shingles(text: str, k: int = K) -> set[str]:
    return {t[i:i + k] for t in [" ".join(text.split())] for i in range(max(1, len(t) - k + 1))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", help="a directory of assembled sample files")
    ap.add_argument("--files", nargs="*", default=[], help="specific files to check")
    a = ap.parse_args()

    targets = [pathlib.Path(f) for f in a.files]
    if a.sample:
        targets += sorted(pathlib.Path(a.sample).glob("*.md"))
    if not targets:
        sys.exit("audit: nothing to check — pass --sample or --files.")

    held = {e["body_sha256"]: e for e in json.loads(MANIFEST.read_text())["entries"]}

    # Straight to the real binary, on purpose: an audit that trusted the shim would be a
    # tautology. `--min-length 0` because the shortest held-out message is 120 characters and
    # the default of 80 is not the point — the population must be complete.
    p = subprocess.run([REAL, "outbound", "--json", "--per-channel", "1000000",
                        "--min-length", "0"], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"audit: could not read the corpus:\n{p.stderr}")
    bodies = {}
    for r in json.loads(p.stdout):
        h = hashlib.sha256(r["text"].encode("utf-8")).hexdigest()
        if h in held:
            bodies[h] = r["text"]

    missing = set(held) - set(bodies)
    if missing:
        print(f"note: {len(missing)} held-out message(s) are no longer reachable through "
              f"`outbound` — they cannot leak through it, but they are also unverifiable here.")
        for h in sorted(missing):
            e = held[h]
            print(f"      {e['date']}  {e['chat']}  {e['length']}ch  {h[:12]}")

    # CONTAINMENT, not "does any long run appear".
    #
    # The first version of this check fired on any 160-character run and reported three leaks,
    # of which two were him reusing a boilerplate paragraph — 28% and 30% of the message. A
    # check that fails on correct behaviour is worse than no check, because it teaches you to
    # skim past red. What matters is how much of a withheld message is recoverable from what
    # was read, so that is what is measured.
    #
    # Note `find_longest_match` is NOT used: SequenceMatcher's autojunk heuristic treats common
    # characters as junk on sequences over 200 elements, which silently under-reports overlap on
    # prose by an order of magnitude. It reported these same two cases as 3% and 1%.
    hits, near, checked = [], [], 0
    for t in targets:
        text = t.read_text(encoding="utf-8", errors="replace")
        grams = shingles(text)
        checked += 1
        for h, body in bodies.items():
            hs = shingles(body)
            c = len(hs & grams) / len(hs)
            if c >= LEAK:
                hits.append((t, h, held[h], c))
            elif c >= NOTE:
                near.append((t, h, held[h], c))

    print(f"\nchecked {checked} file(s) against {len(bodies)} reachable held-out bodies")
    for path, h, e, c in sorted(near, reverse=True, key=lambda x: x[3]):
        print(f"  note: {c:.0%} of {e['date']} {e['chat']} ({e['length']}ch) also appears in "
              f"{path.name} — reused phrasing, below the {LEAK:.0%} leak threshold")
    if not hits:
        print(f"CLEAN — no held-out message is {LEAK:.0%}-or-more recoverable from what was "
              f"checked.")
        return 0
    print(f"\nVOID — {len(hits)} held-out message(s) present:")
    for path, h, e, c in sorted(hits, reverse=True, key=lambda x: x[3]):
        print(f"  {c:.0%} recoverable  {path}  <-  {e['stratum']}  {e['date']}  {e['chat']}  "
              f"{e['length']}ch  {h[:12]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
