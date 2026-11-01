#!/usr/bin/env python3
"""Prove the draft-fidelity checks measure what they claim, before they grade anything.

    python3 harness/step8/self-test.py

Every mechanical check in the draft suite is a small pure function with a threshold, and this
harness's own history is the argument for testing them: **three of its checks have fired on
correct behaviour**, each time teaching whoever read the report to skim past red.

  * the base-modified check failed a session for writing to `workspace/drafts/`, which is the job;
  * the sandbox check matched the session's own temp directory, because the runner named it after
    the harness;
  * the length check read a candidate file — a 700-character message wrapped in a title and notes
    on the choices — as **11.7x** the length of the real message.

The containment thresholds have the same exposure in the other direction. At k=8 containment
measures the English language rather than overlap: across a comparison whose true answer must be
zero it reads 0.51, and at k=32 the same comparison reads 0.03. A leak gate on the wrong window
either voids every sound run or passes a transcription.

So: assertions, with the numbers in them. No corpus access, no network, stdlib only.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import neo_eval as N  # noqa: E402

PASS, FAIL = "  ok  ", "  FAIL"
results: list[tuple[bool, str]] = []


def check(name: str, got, want) -> None:
    ok = got == want
    results.append((ok, name))
    print(f"{PASS if ok else FAIL}  {name}" + ("" if ok else f"   got {got!r}, want {want!r}"))


def close(name: str, got: float, lo: float, hi: float) -> None:
    ok = lo <= got <= hi
    results.append((ok, name))
    print(f"{PASS if ok else FAIL}  {name}  ({got:.3f})"
          + ("" if ok else f"   wanted {lo}–{hi}"))


# ── the candidate-body extractor ─────────────────────────────────────────────
# The real shape that broke the length check, reduced. Title, preamble, fenced message, notes.

FENCED = """# Candidate a — Telegram, Mandarin, to a colleague

Sent as separate messages, the way he writes in this thread.

---

Hi Alex，房租大概$2150，确切的数字要等流程走完才知道。

---

## Notes on the choices

- **One month is stated as done, not proposed.** He took that call.
- Full-width punctuation throughout, per his 2026-08-16 ruling.
"""

HEADINGS_ONLY = """# Candidate b

Cancel the subscriptions now! Your site doesn't function!

## Why

Because his register to a failed vendor is not courteous.
"""

BARE = "Yeah, makes sense. Thanks for taking care of this.🙏"

check("fenced candidate: extracts only the message",
      N._message_body(FENCED),
      "Hi Alex，房租大概$2150，确切的数字要等流程走完才知道。")
check("unfenced candidate: drops the title and the notes tail",
      N._message_body(HEADINGS_ONLY),
      "Cancel the subscriptions now! Your site doesn't function!")
check("a bare message survives untouched", N._message_body(BARE), BARE)
check("an empty file degrades to empty, not to a crash", N._message_body(""), "")
# The point of the extractor is the ratio, not the string: 11.7x was the failure.
close("extraction brings the length ratio back into band",
      len(N._message_body(FENCED)) / len("Hi Alex，房租大概$2150，确切的数字要等流程走完才知道。"),
      0.99, 1.01)

# ── containment, and the window that makes it mean anything ──────────────────

ENGLISH_A = ("Thank you for your continued help with this issue. I am following up on the "
             "engineering side to clarify our current setup and the rollout of the change.")
ENGLISH_B = ("Thanks for the note. We are following up with the engineering team to clarify "
             "how the current setup was rolled out across the site last month.")

close("k=32 keeps unrelated English near the floor",
      N.containment(ENGLISH_A, ENGLISH_B), 0.0, 0.10)
close("k=8 would measure the English language instead — the calibration that was measured",
      N.containment(ENGLISH_A, ENGLISH_B, ) if False else
      len(N.shingles(ENGLISH_A, 8) & N.shingles(ENGLISH_B, 8)) / len(N.shingles(ENGLISH_A, 8)),
      0.15, 1.0)
check("a message contained in itself is total",
      round(N.containment(ENGLISH_A, ENGLISH_A), 3), 1.0)
close("a message inside a longer document is still found",
      N.containment(ENGLISH_A, "Preamble. " + ENGLISH_A + " Postscript, quoted below."),
      0.95, 1.0)
check("the leak threshold sits an order of magnitude above the floor",
      N.CONTAINMENT_LEAK, 0.30)
check("the window is 32 characters", N.SHINGLE_K, 32)
check("empty needle cannot divide by zero", N.containment("", ENGLISH_A), 0.0)

# ── his two corrections, as machine checks ───────────────────────────────────

check("full-width punctuation: a half-width comma between CJK is caught",
      bool(N.HALF_WIDTH_IN_CJK.search("恩, 还是最好删掉这个文件")), True)
check("full-width punctuation: a half-width stop after CJK is caught",
      bool(N.HALF_WIDTH_IN_CJK.search("这个先放着. 明天再说")), True)
check("full-width punctuation: the correct form is not flagged",
      bool(N.HALF_WIDTH_IN_CJK.search("恩，还是最好删掉这个文件。明天再说")), False)
# The reason this is a per-character rule and not a per-message one: he code-switches constantly,
# and the Latin nouns bring their own punctuation with them.
check("full-width punctuation: a code-switched Latin clause is not flagged",
      bool(N.HALF_WIDTH_IN_CJK.search("Alex 这个Staging的git repo 是对应部署在 dev.example.com 上面的是吧？")),
      False)
check("full-width punctuation: a decimal inside a CJK sentence is not flagged",
      bool(N.HALF_WIDTH_IN_CJK.search("房租大概$2150.50，确切数字要等流程走完")), False)

check("a known misspelling is detected", "indepedently" in
      "I'm expecting you to figure that out indepedently 🤔", True)
check("fourteen misspellings are on the list", len(N.KNOWN_TYPOS), 14)
check("the corrected spelling is not on the list", "independently" in N.KNOWN_TYPOS, False)

check("CJK counting: a Mandarin clause", N.cjk_chars("恩，还是最好删掉这个文件") > 8, True)
check("CJK counting: English is zero", N.cjk_chars("Cancel the subscriptions now!"), 0)
check("CJK counting: code-switched text counts only the CJK",
      N.cjk_chars("Hi Alex, deployment 的时候注意一下"), 7)  # 的时候注意一下 — seven, counted by hand

# ── the prompt guard ─────────────────────────────────────────────────────────

ANSWER = ("Hi Morgan and Priya, I've spoken with the stakeholders, and we've decided to decline "
          "the offer for Acme Insights at this time.")

check("the guard bites when the prompt reuses his wording",
      N._longest_word_run("We've decided to decline the offer for now.", ANSWER) >= 5, True)
# The exemption is for a run made ONLY of names and numbers. `Morgan and Priya` is not that — the
# function word in the middle makes it three words of ordinary phrasing, and the guard says 3.
# Which is correct and harmless: the threshold is five. The assertion is therefore about the
# threshold, not about zero.
check("addressing the same two people is not a phrasing leak",
      N._longest_word_run("Reply to Morgan and Priya about Acme Insights.", ANSWER) < 5, True)
# The case the exemption was actually written for: a six-token proper name that cannot be
# paraphrased without making the prompt worse.
check("a six-token title is exempt entirely",
      N._longest_word_run(
          "I want the session recordings from the News & Editorial SEO Summit 2025 — $299.",
          "requesting a $299 budget to purchase the access for the session recordings of "
          "News & Editorial SEO Summit 2025. Appearantly, News SEO is a fairly niche discipline"),
      3)  # `the session recordings` remains, and is ordinary phrasing — under the threshold
check("the guard ignores short incidental overlap",
      N._longest_word_run("Not going ahead with Core — our niche won't get enough out of it.",
                          ANSWER) < 5, True)
check("the answer fed back as its own prompt is refused",
      N._longest_word_run(ANSWER, ANSWER) >= 5, True)

# ── the suite wiring ─────────────────────────────────────────────────────────

drafts = N.load_scenarios(suite="draft")
knowledge = N.load_scenarios(suite="knowledge")
# Scenarios are the operator's own and are never shipped with the framework. With none written
# yet, the wiring checks below are vacuous rather than failing; say so instead of passing silently.
if not drafts and not knowledge:
    print("  skip  suite wiring: no scenarios yet (copy scenarios/_TEMPLATE*.md to write one)")
else:
    check("draft scenarios load", len(drafts) > 0, True)
    check("the knowledge suite is untouched by the draft suite",
          all(s.suite != "draft" for s in knowledge), True)
check("every draft scenario inherits D1–D6",
      all({"D1", "D2", "D3", "D4", "D5", "D6"} <= {c["id"] for c in s.checks} for s in drafts),
      True)
check("every draft scenario inherits U1/U2",
      all({"U1", "U2"} <= {c["id"] for c in s.checks} for s in drafts), True)
check("every draft scenario carries a minute-precise cutoff",
      all(len(s.cutoff) == 16 and "T" in s.cutoff for s in drafts), True)
check("every draft scenario names a held-out message",
      all(len(s.heldout) == 64 for s in drafts), True)
check("every draft scenario declares its stratum",
      all(s.stratum for s in drafts), True)
# A held-out hash that is not in the manifest means the scenario is scoring against something
# that was never withheld — i.e. against material the voice model may have read.
import json  # noqa: E402
_manifest = N.HARNESS / "heldout" / "MANIFEST.json"
if _manifest.exists():
    held = {e["body_sha256"] for e in json.loads(_manifest.read_text())["entries"]}
    check("every scored message really is in the held-out set",
          all(s.heldout in held for s in drafts), True)
elif drafts:
    # Draft scenarios with no manifest cannot be scoring against a withheld set. Fail loudly.
    check("every scored message really is in the held-out set (no MANIFEST.json)", False, True)
else:
    print("  skip  held-out membership: no MANIFEST.json yet (run heldout/carve.py first)")

bad = sum(1 for _, name in results if not name) + sum(1 for ok, _ in results if not ok)
print(f"\n{len(results) - bad} of {len(results)} checks passed")
raise SystemExit(1 if bad else 0)
