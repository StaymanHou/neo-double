"""Content relevance — which rows are corpus noise. NOT the fence.

Ported from the predecessor's `core/content_relevance.py`, rules unchanged, because
86,182 rows in this corpus were already classified by them and a rule that is *nearly* the old
rule would make the same artefact suppressed before a date and visible after it. The point of
porting rather than rewriting is that the corpus answers a question the same way regardless of
when a row arrived.

**This is relevance, not safety, and the distinction is load-bearing.** `suppressed` is not part
of `EMITTABLE`; widening what counts as noise can never widen what counts as readable. Every
verb hides suppressed rows by default and every verb takes `--include-suppressed`, so an
inherited judgement can be seen rather than silently obeyed.

Why these four, kept as they were:

- **`daily_report`** — the automated `KM Daily Report 8/14 / Dashboard: … / == Traffic ==`
  template. Anchored to the start of the body so it matches the template and not a human
  message that merely mentions a daily report.
- **`stats_report`** — `[ORG Stats Aug 14]`, the same daily cadence in a different shape.
- **`separator`** — a body that is only a run of `=`.
- **`service_msg`** — an empty body. Media-only messages carry a placeholder, so a blank body
  is never media; it is a join, a pin, a leave.

**Weekly reports are deliberately kept.** The discriminant is daily-cadence volume, not
machine-versus-human authorship, and `07/20 - My weekly report` is the operator writing.

**Settled 2026-08-16, with evidence, rather than inherited on trust.** `CLAUDE.md` had recorded
that suppressing the daily report was "wrong if the question is how he writes", on the premise
that it is one of the two standing artefacts he authors. Live ingest falsified the premise: every
one of the 42 daily reports in the first pulled window carries `forwarded_from=id:9000000009` —
a bot, the same account that posts `[ORG Stats …]` into a partner group chat. He does not write the
daily report. He relays it. So suppressing it is right for reading traffic *and* right for the
voice model, and the two questions that looked in tension were only ever one.
"""

from __future__ import annotations

import re
from typing import Literal

SuppressedReason = Literal["daily_report", "stats_report", "separator", "service_msg"]

# An org prefix of 2-4 letters ("KM", "ORG") then "Daily Report", anchored to the start.
_DAILY_REPORT = re.compile(r"^\s*[A-Za-z]{2,4}\s+daily\s+report\b", re.IGNORECASE)

# A bracketed org + "Stats" opening. The bracket and the anchor are the precision: a blanket
# `dashboard`/`bit.ly` substring rule was tried in the predecessor and rejected — it would have
# suppressed 111 genuine human mentions.
_STATS_REPORT = re.compile(r"^\s*\[[A-Za-z][A-Za-z ]*\s+stats\b", re.IGNORECASE)

_SEPARATOR = re.compile(r"^\s*=+\s*$")


def classify(body: str) -> SuppressedReason | None:
    """The reason this row is noise, or None if it is signal. First match wins."""
    if _DAILY_REPORT.match(body):
        return "daily_report"
    if _STATS_REPORT.match(body):
        return "stats_report"
    if _SEPARATOR.match(body):
        return "separator"
    if body.strip() == "":
        return "service_msg"
    return None
