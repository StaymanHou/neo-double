---
id: S-000
suite: draft
kind: draft
stratum: outlook-long | telegram-en-long | telegram-zh-long | telegram-en-mid
title: <six words, what this scenario is for>
heldout_sha256: <the full body_sha256 from heldout/MANIFEST.json>
cutoff: <YYYY-MM-DDTHH:MM — one minute before he sent it; locate.py prints this>
generated_from: heldout/MANIFEST.json <hash8>; neo-corpus thread '<chat>' --since … --until …
created: <YYYY-MM-DD>
---

<!-- A DRAFT scenario, step 8. It is not a question, it is a task: put the session where he
     stood one minute before he sent a real message, ask it to draft, and compare.

     Four fields do work here that the knowledge template's do not:

       suite: draft       makes it inherit D1–D6 and the draft mechanical checks, and keeps it
                          out of a `--suite knowledge` run.
       heldout_sha256     the message being scored against. It is one of the 67 withheld from
                          the voice build, so the model has never read it.
       stratum            reported separately. The `-mid` stratum is a CONTROL: it exists so a
                          result can tell "it can write long" apart from "it can write like him".
       cutoff             MANDATORY and minute-precise. A date-only cutoff includes the whole
                          day and therefore the answer. `locate.py --materialise` prints the
                          right value; `run` refuses if it does not exclude the message.

     The body of the real message is NOT in this file. It is corpus content, and corpus content
     does not enter git — and this particular corpus content is the answer key. Materialise it:

       python3 harness/step8/locate.py --materialise <hash8> --id S-000

     `run` and `judge` both refuse without it. -->


## Prompt

The text passed verbatim to the graded session — written as HE would type it to Neo, in the
register of the elicitation ledger: *"My boss wants me to tell a vendor we'll commit by end of month. I don't
think we can. Draft the email."* Blunt, one or two lines, no framing, no hints.

**It may say what he wanted. It may not say how he said it.** State the decision or the goal;
never the reasoning, the structure, the numbers, or any phrasing from the real message — at that
point the session is transcribing and the scenario measures nothing. `run` refuses a prompt
sharing a five-word run with the message it is asking for, which is a floor and not a licence up
to it.

## Ground truth

The **citation and the reading**, not the text. Where the message sits, what decision it carries,
what register, and which specifics in it came from somewhere the session can also reach.

Say explicitly what a correct draft cannot know — a figure he had in his head, a conversation
off-channel — because that is what separates a defect from a gap, and U1/U2 grade a session for
naming the gap rather than filling it.

## Checks

Scenario-specific checks only. `D1`–`D6` (same decision, same language, no softened position,
matching structure, matching sign-off, divergence named) and `U1`/`U2` are inherited — do not
restate them.

Write what is specific to THIS message: the position it holds, the thing it refuses, the number
it quotes, the person it hands the decision to.

- C1 [MUST] <the draft does X>
- C2 [MUST-NOT] <the draft does not do Y>
- C3 [SHOULD] <the draft does Z>

**Switching a check off:** append `:off` to its severity — `- C2 [MUST-NOT:off] …`. It stops
being graded and stops gating, keeps its text and its real severity in the file, and is printed
as `off` in the report with a warning at the top naming every disabled check. Re-enable by
deleting four characters.

Never delete or comment out a check instead. A commented-out check is invisible to the report,
and this harness has twice been misled by an absence that read as a clean result — the whole
point of the marker is that a run with the never-guess check switched off cannot be mistaken
for a run that found no invention.
