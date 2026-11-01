#!/usr/bin/env python3
"""Is any held-out message readable from something git tracks, or would track?

    python3 harness/step8/leak-scan.py            # exits 1 if anything reaches 30%

`heldout/README.md` closes with the honest limit of the voice clamp: *"It hides 60 messages out of
85,701 from the ordinary read path. It does not stop a session reading `MANIFEST.json`, invoking the
real binary by absolute path, or reaching the store another way."* This is the measurement for one
of those other ways, and it is the one that actually happened.

**Three separate routes were open, and all three were found by running this rather than by
reading.**

  * **The knowledge base.** It was written on 2026-08-14, the holdout carved on 2026-08-16, both
    from the same corpus, and nothing compared them. 25% of one held-out message sits quoted in
    `topics/vendors-and-partners.md`. A graded session reads that file as its own world with no
    clamp in front of it, and a voice-build session reads it as fact.
  * **The scenario files.** Written by the author of this rule, who quoted two control messages in
    full because they were short. Measured: **71% and 70%** of a held-out message each, in
    git-tracked files. Short is not the same as safe.
  * **The verdicts.** A judge's evidence must be a verbatim quote, and on a draft scenario it is
    quoted from the message being scored — **49%** in one verdict, **50%** in one report. Both were
    tracked deliberately, because a harness whose history is not in git tells you nothing next
    month. That reason now lives in `SCORES.json`, which carries verdicts and no prose.

Anything at or above **30%** is a leak; **15%** is a note, and a note still matters when it lands on
the message a scenario is scored against. Thresholds and the 32-character window are the calibrated
ones from `heldout/README.md` — do not adjust them by feel.

No message text is printed. A hit is reported by date, chat and path.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import neo_eval as N  # noqa: E402

REAL = os.environ.get("NEO_CORPUS_BIN", os.path.expanduser("~/.local/bin/neo-corpus"))
REPO = N.REPO
SUFFIXES = (".md", ".py", ".json", ".sh", ".txt")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True).stdout


def main() -> int:
    manifest = json.loads((N.HARNESS / "heldout" / "MANIFEST.json").read_text())
    held = {e["body_sha256"]: e for e in manifest["entries"]}

    # Straight to the real binary, like `voice/audit.py`: a scan that trusted a clamp would be a
    # tautology. The harness is allowed to see the answer — it is the thing holding it.
    p = subprocess.run([REAL, "outbound", "--json", "--per-channel", "1000000",
                        "--min-length", "0"], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"leak-scan: could not read the corpus:\n{p.stderr}")
    bodies = {}
    for r in json.loads(p.stdout):
        h = hashlib.sha256(r["text"].encode("utf-8")).hexdigest()
        if h in held:
            bodies[h] = r["text"]

    # Tracked files AND untracked-but-not-ignored ones. The second half is the point: a file that
    # would be swept up by the next `git add -A` has not leaked yet, and that is exactly when it is
    # still cheap to fix.
    tracked = git("ls-files").split()
    pending = [line[3:] for line in git("status", "--porcelain", "-uall").splitlines()
               if line.startswith("?? ")]
    paths = [p for p in dict.fromkeys(tracked + pending) if p.endswith(SUFFIXES)]

    hits = []
    for rel in paths:
        f = REPO / rel
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for h, body in bodies.items():
            c = N.containment(body, text)
            if c >= 0.15:
                hits.append((c, held[h], rel))

    print(f"{len(bodies)} reachable held-out bodies vs {len(paths)} files that are, or would "
          f"become, git content\n")
    for c, e, rel in sorted(hits, reverse=True, key=lambda x: x[0]):
        tag = "LEAK" if c >= N.CONTAINMENT_LEAK else "note"
        print(f"{tag} {c:5.0%}  {e['stratum']:17} {e['date']}  {e['chat'][:22]:22} "
              f"{e['length']:5}ch  -> {rel}")
    leaks = [h for h in hits if h[0] >= N.CONTAINMENT_LEAK]
    print(f"\n{len(leaks)} at or above the {N.CONTAINMENT_LEAK:.0%} leak threshold; "
          f"{len(hits)} at or above 15%")
    if not hits:
        print("CLEAN — no held-out message is 15%-or-more recoverable from anything git holds.")
    return 1 if leaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
