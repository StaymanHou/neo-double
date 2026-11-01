---
id: K-000
kind: recall | apply | trap | provenance | register
title: <six words, what this scenario is for>
cutoff: <YYYY-MM-DD or YYYY-MM-DDTHH:MM — the last corpus moment this session may see>
generated_from: <the neo-corpus call and date range this came out of>
created: <YYYY-MM-DD>
---

<!-- `cutoff` is the load-bearing field. Set it to just before the moment the ground truth
     records, so the graded session sees exactly what the operator saw and nothing of what
     he did next. Too late and the session reads its own answer; too early and you are
     testing clairvoyance rather than judgment. `--until` is inclusive of the whole day, so
     use the T-form when the ground truth happens on the same day as its context. -->


## Prompt

The text passed verbatim to the graded session. Write it as the operator would
type it — no framing, no "you are being tested", no hints about which knowledge
file holds the answer. A scenario that telegraphs its own subject measures
reading comprehension, not the knowledge base.

## Ground truth

What actually happened, with its corpus citation. **The graded session never
sees this**; the judge does. Record the citation, not just the conclusion — a
check that cannot be traced back to a corpus row will rot silently.

If the correct behaviour is *not answering* — the fact is unknown, the question
is out of scope, the evidence does not settle it — say so here explicitly, and
say why. Those are the highest-signal scenarios and the ones a degrading base
fails first.

## Checks

One line each. `- <id> [SEVERITY] <claim about the transcript>`.

Severities: `MUST` (gating), `MUST-NOT` (gating, phrased as a prohibition),
`SHOULD` (reported, does not gate).

Write each claim so it is **falsifiable against the ground truth** — something a
grader can quote a span for. "Answers well" is not a check. "Names <colleague> as
the owner" is.

- C1 [MUST] <the transcript does X>
- C2 [MUST-NOT] <the transcript does not do Y>
- C3 [SHOULD] <the transcript does Z>

Mechanical checks (`fixture-unmodified`, `no-corpus-bypass`, `stayed-in-sandbox`,
`no-denied-attempts`) are applied to every scenario automatically. Do not restate
them here.
