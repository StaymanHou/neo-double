---
name: neo-voice
description: >-
  Build or rebuild the operator's voice and judgment models — `knowledge/self/style.md` (how they
  write) and `knowledge/self/values.md` (how they decide) — from their own sent messages. Use when
  the operator says build the voice model, learn how I write, work out my style, build style.md
  or values.md, run the voice build, or asks why drafts do not sound like them. Also the skill for
  rebuilding them after a large ingest has changed what the corpus holds.
---

# Voice

You are modelling one person's writing from their own sent messages, so that a draft you produce
can be sent without being rewritten.

**The failure this skill exists to prevent is not a bad draft.** It is a *fluent* one — a draft
that reads beautifully, sounds broadly like them, and quietly concedes a position they would not
have conceded. Fluency is what makes it dangerous: a message that reads badly gets noticed and
fixed, and one that reads well gets sent.

So there are two files and they are not the same file:

| | |
|---|---|
| `knowledge/self/style.md` | **how they write** — register, length, openings, what they never do |
| `knowledge/self/values.md` | **how they decide** — what they push back on, concede, raise uninvited |

`values.md` is the one that makes a draft *theirs* rather than merely fluent. Build it with the
same care as `style.md`, not as an afterthought once the interesting part is done.

**Files:** `~/neo-workspace/workspace/knowledge/self/`, plus
`knowledge/topics/channel-register.md` (who they write to in which language) and
`elicitation/asked.md` (read before asking anything).

---

## Gate 0 — the held-out set, before you read a single message

The draft-fidelity evaluation scores your drafts against messages the operator actually sent. If
you read those messages while building the voice model, the evaluation grades the model on its own
input and **passes regardless of quality**. Nobody downstream can detect it: a model reproducing
text it has read looks exactly like a model that has learned a voice.

This is not a caution. It is a precondition, and it is checkable:

```sh
cd ~/neo-workspace
python3 harness/shim-voice/self-test.py            # must be all-green
export NEO_VOICE_HELDOUT=$PWD/harness/heldout/MANIFEST.json
export NEO_VOICE_SHIM_LOG=$PWD/harness/heldout/shim.log
export PATH=$PWD/harness/shim-voice:$PATH
which neo-corpus                                   # must print the shim, not ~/.local/bin
```

**If the manifest does not exist, stop and carve it first** (`harness/heldout/carve.py`) —
do not build the voice model and carve afterwards, which is the same as not carving.

The shim prints on stderr when it withholds. Those gaps are deliberate. Do not go looking for
what filled them, do not read `MANIFEST.json` for hints, and do not reach past the shim to the
real binary. Read `harness/heldout/README.md` for what the clamp does and does not guarantee.

**The error is one-way.** A message read into the model cannot be taken back out.

---

## Gate 1 — a single-channel sample is wrong, not merely thin

Outbound is usually heavily skewed toward one channel (in the original deployment roughly
**100:1 toward Telegram**). Any "most recent N" over the operator's sent messages then returns
*zero* from the small channel — and the work-facing, external register often lives almost entirely
there. A voice model built from a global sample is systematically wrong.

`neo-corpus outbound` takes `--per-channel` and prints its coverage. Check every channel is
represented before concluding anything, and say in the file which channel each claim came from.

Two silent filters will shrink your sample without telling you:

- **`--min-length` defaults to 80**, which drops the majority of short-message outbound. Pass
  `--min-length 0` when you want the population rather than its long tail.
- **Suppressed rows and forwarded rows are excluded by default.** Both defaults are right for
  this skill — a forwarded message is something they sent and did not write — but know they are on.

---

## Gate 2 — register is set per person, and per language

The operator does not have *a* voice. They have one per relationship, and the largest axis is often
language: some correspondents get one language, some another, and a draft in the wrong language is
wrong before it is read. `knowledge/topics/channel-register.md` records which is which — read it
first, and correct it if the corpus disagrees.

Length does not port across those registers either. Measure each one's own distribution rather
than assuming a threshold from one applies to the other; in the original corpus the Chinese
messages were far shorter than the English ones, and "long" meant a different number in each.

Model the axes they actually vary on — language, channel, seniority of the recipient, whether
the thread is internal — and say which of them you *could not* separate given the sample.

---

## Gate 3 — every claim carries an example, and the example must be real

An unevidenced claim about how someone writes is worthless: it cannot be checked, and it will be
read as fact by every future session.

| Wrong | Right |
|---|---|
| "They are concise." | "Median Telegram message 54 characters; English p95 is 460 (`outbound --min-length 0`)." |
| "They are direct in email." | "Opens with the ask, not a greeting — <quoted example>, Outlook, <date>." |
| "They soften bad news." | State only if you read it. If you did not, it is not a finding. |

Quote from what you actually read in this session. **Do not reconstruct a plausible example** —
that is the invention failure, wearing the one costume that makes it hardest to spot, because a
fabricated example of someone's style always sounds exactly like them.

---

## Gate 4 — the negative space is half the model

What they *never* do is as diagnostic as what they do, and it is what a generic-sounding draft
gets wrong. Sign-offs they don't use, hedges they don't write, apologies they don't make,
enthusiasm they don't perform. Look for absence deliberately; it will not show up in a sample
you only read for patterns.

State negative claims at the strength the evidence supports — *"no instance in the 358 messages
read"* is honest; *"they never do this"* is a claim about 17,000 messages you did not read.

---

## Gate 5 — `values.md` is about decisions, not sentences

Do not fill it with tone. It answers four questions, each with a cited instance:

- **What they push back on** — and what the pushback looks like.
- **What they let go** — the concessions they make readily, which are as informative as the refusals.
- **What they raise even when nobody asked.**
- **Trade-offs they accept, and the ones they won't.**

The test for an entry: *if a draft quietly went the other way on this, would it be wrong even
though it read well?* If yes it belongs here. If it only changes how a sentence sounds, it
belongs in `style.md`.

Where the corpus shows a decision but not the reasoning, record the decision and mark the
reasoning unknown. Inferring the principle from one instance is how you end up asserting a value
they do not hold.

---

## Gate 6 — show the input before you spend

Before the bulk read, print what you assembled: which channels, which chats, how many messages,
how many characters, and what the sample deliberately excludes. Then **wait.**

Every real defect in the predecessor was caught here and none by its tests — including a
self-model built from 50 Telegram messages and 0 Outlook ones, which no amount of downstream
checking would have surfaced.

If the sample turns out to be too large to work with, **say so and confirm before shrinking it.**
A quietly reduced sample is a different experiment reported under the same name.

---

## Gate 7 — the audit, at the close

Before you report the build finished:

1. **Check every example you quoted against the held-out set.** A hit voids the build — it means
   a held-out message reached you by some path the shim does not cover, and the evaluation's result
   against this model would be meaningless.
2. **Check the shim log** (`$NEO_VOICE_SHIM_LOG`) for calls that bypassed the clamp.
3. **State the coverage in the files themselves** — how many messages, per channel, per register,
   over what date range. A future session must be able to tell what the model was built from
   without re-deriving it.

---

## The acceptance test is the operator's correction

These two files are not finished when you are satisfied with them. They are finished when the
operator reads them and says they are recognisably them — and the corrections they make on the way
are the most valuable signal in the whole exercise, worth more than anything you inferred.

So: **write them to be corrected.** Short, specific, checkable claims they can strike one at a
time. Not an essay they have to accept or reject whole. Where you are unsure, say so in the file
rather than smoothing it into confident prose — an uncertain claim they correct is a gain, and a
confident wrong one propagates into every draft you write afterwards.

Log what you asked and what they answered in `elicitation/asked.md`, and **never ask twice.**

**Do not commit.** Drafting is a proposal; committing is the operator's act.
