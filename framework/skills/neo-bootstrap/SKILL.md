---
name: neo-bootstrap
description: >-
  Build Neo's knowledge base from the message corpus for the first time — who the people are,
  what the projects are, which organisations and recurring topics matter. Use whenever the
  operator says bootstrap, populate your knowledge, learn who everyone is, get up to speed,
  build out what you know, start from the corpus, or asks why the knowledge base is empty.
  Also the skill for topping it up later when new channels are admitted.
---

# Bootstrap

**Everything you write here will be read as fact by every future session.** That is the whole
weight of this skill. A wrong claim about a colleague does not stay in one file — it propagates
into every draft addressed to them, and it will not look wrong later, because it will look like
something you knew.

So: **missing beats wrong.** A thin file with three sourced facts is worth more than a rich one
with an inference in it.

**Files** (in `~/neo-workspace/workspace/`):
- `knowledge/people/`, `knowledge/projects/active/`, `knowledge/orgs/`, `knowledge/topics/` — what you write
- `knowledge/*/_TEMPLATE*` — copy these; don't invent a shape
- `elicitation/asked.md` — read before asking anything; append after
- `../CLAUDE.md` — update § Status when you finish

**Commands:** `neo-corpus chats · people · thread · outbound · search · channels-pending · stats`

---

## The gates

### 1. Every claim is sourced, or it is not written

| Wrong | Right |
|---|---|
| "Alex Rivera is the CTO." | "Alex Rivera — 5,721 messages, mostly infrastructure and deploys (`neo-corpus thread 'Alex Rivera'`). Role not stated anywhere I read." |
| "Morgan Lee handles HR." | "Morgan Lee — referred to as handling HR and admin questions in *Team General* on 2026-03-11." |
| "This project is behind schedule." | "Last activity 2026-04-02; no completion signal in the thread." |

*(Names are illustrative.)* If you cannot point at where it came from, it goes in
`open-questions.md` as a question, not in `README.md` as a fact.

### 2. Volume is not importance, and you must not treat it as such

The largest conversation is not the most important one, and a 200-message 1:1 with a decision
maker outranks a 5,000-message group of deploy notifications. Read enough of a thread to tell
which it is. **In the original corpus, the office-cleaning group had over a thousand messages.**

### 3. `chats` and `people` are different groupings — use the right one

One person appears in many conversations; one conversation contains many people. The previous
system conflated these and drafted a single sender who appeared in **29 different chats** as one
person, complete with a private 1:1 folded into a group's sample. When the question is *who is
this person*, use `people`. When it is *what is this conversation*, use `chats`. Never derive a
person from a chat's name.

### 4. Don't create an entity you cannot describe

A file per sender is not the goal. If after reading you can only say "appears in Team General
sometimes", write nothing — an empty `people/` is honest and a directory of stubs is noise that
future sessions must wade through.

### 5. Stop before spending, and show the input

Before the first bulk read, print your plan: which chats, which verbs, roughly how much you will
read, and in what order. Then wait.

This is where every real defect in the predecessor was caught, and none of them by its 2,034
tests: the 29-chat collapse above; a self-model built from 50 Telegram messages and 0 Outlook
ones; context fetched into a struct and never rendered into the prompt. All obvious to a human
looking at the actual input, all invisible to everything else.

---

## Operation: first bootstrap

**1. Orient.** `neo-corpus stats`, then `neo-corpus chats --min-messages 200`. Read
`../CLAUDE.md` § Status and `elicitation/asked.md`.

**2. Propose dispositions for what is withheld — first, because it changes your inputs.**
`neo-corpus channels-pending` gives you **metadata only**: name, volume, date range, group or
1:1. Propose `work` / `personal` / `ignore` for each with one line of reasoning, biggest first.

Write the proposals to `workspace/proposed-dispositions.sh` — one `neo-channels set` line per
channel, each with your reasoning as a trailing comment, ordered by volume:

```sh
neo-channels set -100111 work      # Team - General Matters · group · 5,040 msgs · to 2026-07-14
neo-channels set 222       work      # <colleague> · 1:1 · 3,246 msgs · appears in work threads
neo-channels set 333       personal  # <family member> · 1:1 · 2,256 msgs · no work signal in any group
```

Then stop and tell the operator the file is ready to review. `neo-channels` is **denied to you** —
a whole file they can read at once beats a hundred separate approvals, and reading it *is* the
review. They run it themselves: `! sh workspace/proposed-dispositions.sh`, or line by line.

Ingesting a chat into a pipeline once was **not** consent to show it to you. Treat those as
different decisions, because the operator does. If they admit some, re-run `stats` before
continuing.

**Then delete the script, and never commit it.** Its comments say things like *"<name> · 1:1 · no
work signal"* — a durable, named judgement that a specific person is personal. Committing that
would put exactly the material the fence exists to keep out of reach into git permanently, where
no later disposition change removes it. It is gitignored as a backstop; the rule is don't write
it anywhere else and delete it when it has served its purpose.

**3. Organisations and topics first, then projects, then people.** Deliberately outside-in: a
person's file is mostly *role in a project* and *register with the operator*, and both are
unwritable until the projects exist.

**4. Per entity:** copy the template, read enough of the thread to source each claim, fill only
what you can source, and put the rest in `open-questions.md`. For projects, `git mv` the whole
`_TEMPLATE` directory to `active/<slug>/`.

**5. Batch your questions.** Append to `elicitation/asked.md` as you go — question, date, answer
verbatim where the wording matters, and which file it landed in. Ask in one line with the
options in it. **Never ask twice**; that file is the only memory that survives the session.

**6. Update `../CLAUDE.md` § Status** — what you built, what you left open, what is still
withheld. Then commit, granularly: one commit per category reads far better in six months than
one commit called "bootstrap".

## Operation: top up after new channels are admitted

Same gates. Re-run `stats`, read only the newly-visible channels, and **update** existing files
rather than writing second ones. If a new channel contradicts something already recorded, say so
explicitly rather than silently overwriting — a contradiction is a finding.

---

## What to leave alone

- **`self/style.md` and `self/values.md`.** Those are `neo-voice`'s, and it reads outbound with
  per-channel coverage guarantees this skill does not apply. Writing a half-observed voice model
  here would poison the thing every drafting capability depends on.
- **`operations/urgency-criteria.md` and `briefing-criteria.md`.** Operator-authored. Their
  definition of urgent is not a pattern to be mined out of the corpus.
- **Judgment claims about people.** "They push back on X with her" is `knowledge_kind: judgment`
  and needs observed decisions, not vibes. Factual first; the judgment layer comes later.

## Traps worth naming (once)

- **The corpus ends at the last pull.** Nothing in it tells you about now. Don't write
  "currently" — write the date you observed.
- **`chat_display` can be missing on some rows** (about 5% in the original). A conversation may
  surface as a bare numeric id. Get the name from `neo-corpus chats`, and never put an opaque id in
  front of the operator.
- **Placeholders like `<CREDENTIAL_ASSIGNMENT>` are the fence working**, not corrupt data. Don't
  note them as anomalies and don't try to reconstruct what was there.
- **Two names, one operator.** The operator may use one name on Telegram and another on Outlook —
  see the identity table in the framework `CLAUDE.md`, including the lookalikes that are *not* them.
- **A group's name is not an org.** A group named after a website's tech support is a support
  channel, not a company.
