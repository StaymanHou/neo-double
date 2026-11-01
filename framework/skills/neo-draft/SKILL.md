---
name: neo-draft
description: >-
  Draft or rewrite a message in the operator's voice for a specific recipient — the
  translate-on-request capability this whole project exists for. Consumes
  `knowledge/self/style.md` and `values.md` and fills in `workspace/drafts/<date>-<slug>/`. Use
  whenever the operator hands you a blunt draft to rewrite, asks you to write to someone, says draft
  this, soften this, make this sendable, reply to them, put this in my voice, or hands you a
  decision and asks for the message that carries it. Also the skill for a message they are
  composing from scratch.
---

# Draft

The operator sends. You draft. Nothing you write leaves this machine without their approval in the
turn.

**The failure this skill exists to prevent is the fluent draft that concedes something they would
not concede.** A message that reads badly gets noticed and fixed; one that reads well gets sent. So
fidelity to their *decision* outranks quality of prose, every time, and where the two conflict you
surface the trade-off rather than choosing for them.

`neo-voice` builds the model. **This skill is the only thing that consumes it** — `style.md` and
`values.md` exist to be read here, at drafting time, before a word is written.

**Read, every time, before drafting:**

| | |
|---|---|
| `knowledge/self/style.md` | how they write — register, length, openings, what they never do |
| `knowledge/self/values.md` | how they decide — what they push back on, concede, raise uninvited |
| `knowledge/people/<recipient>.md` | **open the file, do not just list the directory** — see Gate 3 |
| `knowledge/topics/channel-register.md` | who they write to in which language |
| `feedback/INDEX.md` | what they have already told you to stop doing |
| `elicitation/asked.md` | read before asking them anything |

---

## Gate 0 — fix the envelope before you write a word

**This is the gate that exists because of measurement, and it is the one most likely to be skipped
because it feels like preamble.** Graded runs found one paragraph rewritten as a four-bullet agenda,
a three-sentence acknowledgement rewritten as three paragraphs, and a terse two-block list rewritten
as a headed essay at 4.4× the length. In every case the content was defensible and the *shape* was
not theirs.

The cause is ordering: shape and length get chosen *after* the content exists, which means they get
chosen by what the content seems to need rather than by what the operator would have sent.

**Before any of that, one question outranks the whole gate: is this ONE message?** Everything below
fixes the envelope of the artefact you were asked for, and that is the wrong unit when a piece of the
content belongs in a different message addressed to a different person.

*(A real case: asked for a weekly department update, two candidates were offered that differed on
whether the end of a contractor's engagement should be stated in it. The operator answered a
question neither candidate had asked: **"The note on <the contractor> should be a separate note."**
A departure is not a line item in a status update — it is addressed to the person, and the person
was in the room. So the A/B was a choice between two wrong envelopes, and the gate had done its job
perfectly one level too low.)*

The test is **who each part is addressed to.** A status update is addressed to a room; a thank-you,
a farewell, an apology, a correction, a decision that lands on one person are addressed to somebody.
When those are the same message, the personal half reads as an agenda item and the room reads over
it. Split it, then run the gate below **once per message** — they will not share a length band or a
register.

And when you split, say so and say why, because the operator may want it folded back. **Do not
silently expand one request into three deliverables** — that is the § *Match the scope of the
request* failure with better intentions.

So decide these **first**, write them into `context.md`, and only then draft:

| | fix it from |
|---|---|
| **language** | `channel-register.md` and the person file. A draft in the wrong language is wrong before it is read |
| **medium and register** | Telegram short reply vs substantive Outlook — they are different registers, not different lengths of one register |
| **length band** | **measured, not guessed** — see below |
| **structure** | prose · one paragraph · bare `- ` bullets · headed sections. Match what they do *with this person on this kind of thing* |
| **greeting / sign-off** | from `style.md`. In the original, a sign-off appeared on substantive email **only** — never on chat, never on a short reply |
| **opening move** | **anything carrying an instruction or a decision opens with one line of context** — see below |

**The opening move is element 1 of their own shape, and it is the one most often dropped.** In the
original deployment, `style.md` recorded the decision message as **1. one line of context;
2. options with their downsides; 3. the operator's recommendation marked as theirs; 4. the decision
handed back** — and called it the most characteristic thing they write. A draft that opens on the
instruction has skipped step one of the thing being modelled. Find the equivalent shape in your
operator's `style.md`.

It belongs in Gate 0 rather than in a gate of its own because it fails for the Gate 0 reason:
content gets written first, and the opening gets chosen by what the content seems to need. A message
whose job is *correct a misunderstanding* reads as though it should start with the correction. Their
shape says otherwise. **Name the sentence that supplies the context before you draft the rest** — if
you cannot, the message does not have one. This is not a licence to lengthen: the fix is one line,
and rewriting a short message into an essay is the opposite failure, already on record.
*([F-010](../../../docs/lessons.md#f-010) — all three candidates for a message to a contractor who
had been silent three weeks opened on the instruction; the correction was "don't we need to give
them some context first?")*

**Measure the length band from their own comparable messages**, not from what the content needs:

```
neo-corpus thread "<person>" --direction outbound          # everything they have sent them
neo-corpus thread "<person>" --direction outbound --since <date> --until <date>
```

`thread` takes no length filter, so this is the whole population — which is what you want. **If you
reach for `neo-corpus outbound` instead, pass `--min-length 0`:** that verb defaults to 80 and
silently drops most short-message outbound (71% of Telegram in the original), leaving you the long
tail only. That is exactly the bias that produces an essay where they sent two lines.

Then: **the draft fits the envelope, or the envelope was wrong and you say so.** Do not quietly
expand it. A draft that overruns by 4× is not a thorough draft, it is a different message.

## Gate 1 — carry the decision, unchanged, in both directions

`values.md` § *The shape of a decision* should already say both of these are failures. Graded runs
found both.

- **Do not soften.** A refusal, a price, a deadline, a *"that's your call"* — if the input holds a
  position, the draft holds it. Adding `right now`, `for the moment`, or `What do you think?` to a
  flat position changes it.
- **Do not harden.** Removing their hedge is not tightening the prose, it is upgrading a guess into a
  claim. `I think some changes are needed` is not `Changes are needed`.
- **Do not move decision rights.** If they hand the decision to someone — *"I'd like your
  thoughts"* — the draft hands it over too. A draft that takes the decision back reads more decisive
  and is wrong.

**Where a better message would require giving up a position, say so and let them choose.** That is
the judgment-preservation rule and it outranks fluency.

## Gate 2 — do not assert what you did not read

Every specific — a date, a time, an amount, an entitlement, a commitment, a state of affairs — comes
from the knowledge base, from the corpus you actually read this session, or from the operator. Not
from what would plausibly be true given the rest.

**The tell is that it is easy.** A draft flows better with a concrete detail in it than with a gap,
and the gap is the honest output. **Name the gap in the draft** — leave the field empty and flag it.
A message with a hole they fill in ten seconds is a success. A message with a confident wrong time
in it is the failure this whole system exists to avoid, because they will send it.

**A stranger gets a question, not a name.** An unresolved identity is raised with the operator in
your reply and its answer logged in `elicitation/asked.md`. Adjacency is not evidence — their own
words: *"I will not infer names from how people address each other in-thread."*

**And a link or an attachment is a specific too — OPEN IT, and check the recipient can.** A message
carrying a link asserts two things: that it points at what the operator says it points at, and that
the person receiving it can do what the message asks them to do with it. Neither is visible in the
draft, both are checkable in seconds, and the failure is invisible until it is the recipient's.

*(A real case: a questionnaire went to a colleague as a shared document they were asked to fill in
and send back. The link was shared read-only: they could read it and could not type a word. Raised
before sending, the operator fixed it, and the send waited — but nothing in this skill had asked the
question, and the draft was otherwise finished. The first artefact of a program pitched as saving
that colleague time would have cost them a round trip.)*

Concretely, before the send: **read the document** — it may not say what the covering message
claims, and it may contradict it (that one's closing line said *"send it back whenever you get a
chance"* under a message asking for Friday). **Check its sharing.** And where the ask is *fill this
in*, check the recipient can write, not merely open. What you cannot check, say you could not check.

## Gate 3 — their grounds usually live one file deeper

A graded run failed because the session listed `knowledge/people/` and never opened the recipient's
file — and half the operator's actual reasons were in there.

Listing a directory is not reading it. **Open the person file, the org file, and any project file the
message touches.** When their position rests on four grounds and the draft carries two, the draft is
weaker than the message they would have sent, and the two you dropped are usually the ones about the
*other* person rather than about the work.

## Gate 4 — keep the judgment they volunteer

The operator routinely says something they were not asked for: a caveat about how a number should
be read, an objection nobody raised, a piece of context that costs them something to state. Drafts
drop these, because nothing in the request asked for them.

**They are the least conventional and most characteristic part of what they send.** If the input
carries one, it survives into the draft. If you cut it, say why in the handover.

## Gate 5 — the mechanical rules, which are not matters of taste

The operator ruled on each of these; they are machine-checked, and getting one wrong is
unambiguous. Replace them with your operator's own rulings as they arrive.

- **Do not reproduce their typos.** *"Those were not intentional"* — frequency is not intent.
- **Chinese punctuation is always full-width.** `。，！？：；（）` — never `,` `.` against CJK.
  (Generalise: whatever the operator's languages require, typographically.)
- **A decline to a vendor need not carry the literal reason** (`values.md` § *Declining a vendor*).
  That licences the **framing** of an external decline. It does **not** licence an invented
  checkable fact, and **nothing** about a person.

## Gate 6 — the directory is the deliverable, and the handover names its own gaps

**The message body is emitted UNWRAPPED — one unbroken line per paragraph.** Do not hard-wrap it
to the repo's ~95-column markdown convention. A candidate is not prose *about* a message, it **is**
the message sitting in a file, and every wrap becomes a real line break the moment it is pasted.
Blank lines appear only where the operator would actually have a paragraph break in what they send.
Delimit each block plainly (`---- BLOCK 1 ----`) so it is obvious where one message ends and the next
begins, and keep the surrounding commentary outside those delimiters.

The general rule this is an instance of: **content destined to be copied verbatim into another
system is never reformatted to suit the file it is stored in** — no wrapping, no smart quotes, no
markdown emphasis added, no list renumbering. *(Caught on the skill's first real use: "Why your
draft candidate always have new lines that break the sentences?" —
[F-003](../../../docs/lessons.md#f-003). It is the worst-shaped kind of defect: it passes every
content check and damages the message at the last step, when it is sent.)*

Copy `workspace/drafts/_TEMPLATE/` to `workspace/drafts/YYYY-MM-DD-<slug>/` and fill it in:
`request.md` (their blunt input, verbatim) · `context.md` (**the envelope from Gate 0**, plus every
file and corpus verb you read) · `candidate-a.md`, `-b.md` · `ratings.md` · `sent.md` once it is sent.

Offer **two candidates when they differ in a way the operator would actually choose between** —
register, or how much ground to give. Two candidates that differ in wording are one candidate.
**Recommend one and say why**, so they are choosing, not sorting.

**Then name what you did not know.** Graded runs failed a check here repeatedly, and always the same
way: the draft was handed over as ready to send, with the gaps unmentioned. Say plainly:

- where you left a field empty, and what it needs;
- where you chose between readings of their input, and which you took;
- what you deliberately left out;
- anything you could not verify.

**Do not assert completeness.** "Draft's ready" when three specifics are missing is the failure — the
draft being incomplete is fine and expected.

## Gate 7 — you send only what they approved in the turn, and you do not commit

`neo-send` exists for Telegram (and `neo-mail` for Outlook reply-all, below), and the operator's
ruling is that running it is the agent's job behind an in-conversation approval gate: *"the sends
should also become yours but there must be an in-conversation approval gate."* So the sequence is:
**preview the composed messages back to them in the turn → get their words →
`--approve --quote '<their words>'` → `--commit`.** Handing them a `!` line for a send they have
already approved is [F-001](../../../docs/lessons.md#f-001).

The gate cannot prove a human approved anything — you run both halves. What it makes impossible is
drift: approved-this-sent-that, a message added or reordered, the right text in the wrong room, a
stale approval, a double post. The weight rests on one doctrine sentence: **never write a quote they
did not say.** An unapproved send is not an oversight, it is a fabricated record.

**Approval is per message, never standing**, and the thing sent is the **file they read** — never a
message reconstructed from memory into a new file.

**Outlook: `neo-mail reply-all`**, same gate. Put the message body in its own file (`msg-1.txt`, no
block delimiters), then run
`sensor/.venv/bin/neo-mail reply-all --chat <sender> --subject '<exact subject>' [--at <UTC>] <file>`.
The preview prints **every recipient** — show them along with the text, because the approval is
pinned to them. Then ask *"Send this to <who>?"* and **wait**. A request to send made before they
have seen the preview is a request to preview, not an approval. `hooks/guard-send-approval.py`
refuses a quote that is not in their latest message.

**And committing is still the operator's act, not yours** — a drafting session once committed
without being asked and it cost a revert. Write the files, send what they approved, tell them what
you wrote, stop.

---

## The acceptance test

**They send it without rewriting it** — or they change one thing, and that thing was a fact only
they held, which you flagged.

A draft they have to re-argue is a failure even if it reads well. When they correct the same pattern
twice, that is not a bad day — open a `workspace/feedback/` file and classify its layer, because a
pattern they have named once must not need naming again.
