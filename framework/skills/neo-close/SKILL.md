---
name: neo-close
description: >-
  Run the end-of-session audit before closing out — what was learned but never written down,
  entities appearing in new traffic with no knowledge file, claims in the docs that were never
  re-measured, pointers that resolve to nothing, and feedback/elicitation bookkeeping that has
  drifted. Use when the operator says close out, wrap up, we're done, anything to record before
  we close, or invokes it by name. Also the skill to run before a handoff.
---

# Close out

**The session is about to end and everything not written down is about to stop existing.** This
skill is the last chance to catch that, and it exists because a close-out done by hand once found
**seven** things — three of them false claims already sitting in the documentation, one of them a
security finding that had been mentioned in conversation and nowhere else.

**Two rules govern every gate below.**

1. **A gate that reports "clean" must have run something.** Name the command and what came back.
   This project's whole failure history is absences that read as results — a credential filter
   that published the secret and reported itself clean, two sensor verbs that said "nothing" when
   they meant "filtered to nothing", a pointer to a file that has never existed. **"I checked and
   it's fine" is not a result. "`X` returned `Y`" is.**
2. **Fix what is yours; ask about what is the operator's.** Bookkeeping, stale numbers and
   dangling pointers are yours — fix them. An unresolved identity, a disposition, a ruling: theirs.
   **Never write a fact about a person to close a gap.**

---

## Gate 1 — What did you learn this session that never got written down?

**Run this first, because it is the only gate whose input disappears when the session ends.**

Re-read your own turns. For every one of these, find the file it landed in — or write it now:

- a **finding** (a defect, a gap, a measurement)
- a **ruling the operator gave**, in their words
- a **correction they made** to how you work
- something you **flagged in conversation** and moved on from
- a number you **measured** for the first time
- a **question you asked them** and their answer

**The miss that created this gate:** a live-looking one-time login code reached the agent through
`neo-corpus`, was reported to the operator plainly — and was written **nowhere**. It survived only
because they asked "anything to record?" a second time. A recogniser gap that nobody records is a
recogniser gap that gets rediscovered.

| what it was | where it goes |
|---|---|
| their correction to how you work | `workspace/feedback/` — **classify `layer:` first** |
| their answer to a question you asked | `workspace/elicitation/asked.md` |
| a fact about a person, org, project | the entity's file under `workspace/knowledge/` |
| a term they defined | project-root `CLAUDE.md` § *Their vocabulary* |
| a sensor/filter gap | project-root `CLAUDE.md`, with the reasoning for fixing or not |
| a measurement of a tracked command | `runtimes.md` |
| what changed today | `CHANGELOG.md` |

---

## Gate 2 — Entities in new traffic with no knowledge file

**The operator's explicit ask, and the gate most likely to produce work rather than reassurance.**

New traffic arrived since the last close. Some of it names people, organisations and projects the
knowledge base has never heard of. Find them:

```
neo-corpus recent --since <last close date>
neo-corpus people                      # senders aggregated ACROSS conversations
neo-corpus chats                       # one row per conversation
```

Then diff against what exists:

```
ls workspace/knowledge/people/ workspace/knowledge/orgs/ workspace/knowledge/projects/active/
grep -rl "<identifier>" workspace/knowledge/
```

**`people` and `chats` are different groupings and neither substitutes for the other** — one
person appears in many conversations, one conversation contains many people. The predecessor
conflated them and invented a single fictional sender out of 29 separate chats.

**What to do with each unknown, and this is the whole point of the gate:**

| | |
|---|---|
| a **sender id** with no person file | **ask the operator.** Say what the corpus shows about their *behaviour*, say the base has no record of who they are, put the question in your reply (and its answer in `elicitation/asked.md`). **Do not name them.** |
| a **name in a message body** with no file | same. Adjacency is not evidence — the operator's ruling: *"I will not infer names from how people address each other in-thread."* |
| a **project or org** referred to as established | a file **can** be written, because it is not a person — but every claim carries its verb, chat and date, and anything you are inferring is marked as inference |
| a **person file that exists but has nothing new** | leave it. This gate is for gaps, not for topping up every file every session. |

**The original corpus held five near-miss name pairs** (two people with near-identical names
spelled differently; two addresses that might or might not be one person; same-first-name pairs)
and they resolved **both ways** on identical evidence shapes. That is the argument for asking
rather than for judgement.

**Report the count even when it is zero** — *"N senders in the window, all N have files"* is a
result; "no new entities" is not.

---

## Gate 3 — Re-measure every number the docs assert

**Documentation rots silently, and a stale number reads exactly like a fresh one.**

The project-root `CLAUDE.md` and `HANDOFF.md` assert measurable facts. Each has a command that
settles it. Run them and diff:

| claim | command |
|---|---|
| corpus size, visible range, dispositions, pending | `neo-corpus stats` |
| test suite result | the suite — **read `runtimes.md` for the timeout first**, then update it |
| questions asked / open | `grep -c "^## Q-" workspace/elicitation/asked.md` and count open ones |
| feedback open / closed | `workspace/feedback/INDEX.md` against the files |
| anything phrased "as of \<date\>" | is that date still today? |

**The finding that made this gate non-optional.** The status header said the fence suite was
`87/87` and that a test was *"failing deliberately"* pending a re-redaction the operator had run
**two days earlier**. Every session in between was told a test was red on purpose when it was
green. The same bullet warned, one sentence later, that *"a bullet in a status note is exactly the
thing that goes stale first"* — and had gone stale in the sentence describing its own habit.

**Prefer a fresh measurement over a repeated one, and say why it is better.** Re-running the suite
after an ingest is not a repeat: it is the first check that the new rows are clean.

---

## Gate 4 — Pointers that resolve to nothing

**This project's most-repeated failure shape.** A pointer to a missing file reads as *"nothing to
read here"*, which is indistinguishable from *"there is nothing here"*.

Two on record: doctrine told every session to read `workspace/CLAUDE.md`, which **had never
existed**; and `operations/briefing-criteria.md` said *"Run `neo-brief`"*, a command that had
never existed.

```
python3 tools/check-pointers.py CLAUDE.md HANDOFF.md    # or any files you touched
```

**Use the script; do not hand-roll the grep.** The first draft of this gate was a one-liner that
tested every backtick-quoted path for existence. It reported **17 misses, all 17 spurious** —
the docs write paths relative to several different roots (repo root, `workspace/`,
`workspace/knowledge/`, the citing file's own directory), and a bare existence test sees shorthand
as absence. **A check that fires on correct behaviour is worse than no check: it teaches you to
skim the red.** Ten defects were found in this project's own checks and five were of exactly that
species. The script resolves against every plausible root before reporting.

**It bites, and that was verified rather than assumed** — on a probe file it catches
`neo_sensor/fetch.py`, which is precisely the [F-001](../../../docs/example-corrections.md#f-001) defect: the
module name that does not exist, handed to the operator in a command they were expected to type.
**A command you cite is a command you check**, and this is the mechanical form of that rule.

**It reads markdown `[text](path)` links too**, resolved strictly against the citing file's folder
— before that it skipped them and passed `feedback/INDEX.md` with a dead link. Run it over every
file you **moved or linked**, not just `CLAUDE.md` and `HANDOFF.md`.

**Commands are not covered by the script** — check those by running `--help`, or by running the
command. `neo-channels` is denied to the agent; read its argument parser instead.

**And a command you GENERATED is not covered either.** `check-pointers` validates paths cited in
prose; nothing validates a command the agent wrote for the operator, and `neo-channels` is denied
to the agent so a proposal script can never be smoke-tested by running it.

```
python3 tools/check-proposal-script.py --pre-handover workspace/proposed-*.sh
```

**`--pre-handover` is the strict mode and it is the only one to use before writing the `!` line.**
Afterwards, run it plain: a real disposition sitting where a `DECIDE` placeholder was **is what the
file looks like once the operator has decided** — they edit the one word in place. Flagging that is
a check firing on correct behaviour, and [F-009](../../../docs/example-corrections.md#f-009) is the record of
doing exactly that: a defect was diagnosed, filed, wired in here and committed, and it was the
operator's edit. **When two explanations fit and one of them is "they did it on purpose", ask.**

---

## Gate 5 — Bookkeeping matches reality

- **`workspace/feedback/`** — is every `status:` true? A pattern fixed this session must move to
  `resolved/` **and** its row must move out of *Open*. **A `wont-fix` is not a `fixed`** — say
  which kind of closed it is, because a `wont-fix` filed under a heading that says *Fixed* reads
  as a solved problem.
- **`workspace/elicitation/asked.md`** — every question you asked this session is in it, with the
  operator's answer verbatim. Counts in `CLAUDE.md` match.
- **`CHANGELOG.md`** — today's entry exists and covers what actually happened, including the
  things that went wrong.
- **`HANDOFF.md`** — rewritten, not appended to. It is a snapshot of *this* boundary.
  - **If a recommendation in it was overtaken, record the collapse rather than quietly
    rewriting it.** Once a handoff argued for building a skill on a premise that was disproved an
    hour later; the reversal is more useful than a clean file.
- **Lifecycle is a directory move**, never a status edit alone. **Never delete; archive.**

---

## Gate 6 — Layer, and then reachability

Two separate questions, and getting the first right is not enough.

**Layer decides which repository.** *Would this still be true for a different operator at a
different company?* Yes → `~/.config/claude-neo/`. No → `workspace/`. Getting it wrong is
expensive in both directions: doctrine written into `workspace/` is invisible to a fresh session,
and the operator's world written into the config dir is wrong for the next operator and leaks into
every fixture.

**Frequency decides which file within it.** A convention consulted on *every* instruction has to
sit where it is read **without being sought** — `CLAUDE.md` is loaded every session, a knowledge
file is not. The operator's vocabulary table was first written to `knowledge/self/preferences.md`:
layer-correct, reachability-wrong. Their ruling: *"I don't want you to have to load self/pref to
figure that out."*

**And never leave two copies.** Two copies of a convention drift, and the stale one is
indistinguishable from the live one until it has already misled somebody. Move it and leave a
pointer.

---

## Gate 7 — Commit, and check the leak scan first

```
python3 harness/step8/leak-scan.py     # BEFORE committing anything under workspace/
git add -A && git status --short        # read this; know what you are committing
```

**Commit granularly with a message that says what was wrong**, not just what changed — the git
history *is* the audit trail of how this knowledge base was built, and that is worth more than a
tidy log.

**What you do not commit.** A draft the operator has not sent is theirs to send, not yours to
record as done.

**And the config dir is its own repo — commit there too.** `~/.config/claude-neo/` is a
**local-only** git repo tracking the doctrine, `settings.json`, the hooks and the `neo-*` skills. A
session that edits doctrine and does not commit it leaves the same gap the workspace repo exists to
close.

**Two repos, two commits, and do not cross them.** The layer rule already decides which tree an
edit belongs in (§ Gate 6); this just means each tree gets its own commit. **`projects/`, `jobs/`,
`file-history/`, `history.jsonl` and `shell-snapshots/` are ignored there and must stay ignored** —
transcripts carry real message bodies that came through `neo-corpus`, and committing them would put
the corpus into git history permanently. **Never `git add -f` in that tree.**

**It has no remote by the operator's ruling, and adding one is not a neutral change** — it would
need `statusline.sh` and the hooks re-read line by line rather than grepped. Raise it; do not just
run `git remote add`.

---

## What to hand the operator at the end

**One short report. Per gate: what you ran, what came back, what you fixed, what needs them.**

Then, explicitly:

- **the questions** — batched, one line each, options included
- **anything you could not verify**, named as unverified
- **what you deliberately left alone**, and why

**Do not assert completeness.** "All clean" after a close-out that ran two of seven gates is the
failure this skill exists to prevent. If a gate did not run, say which and why.

## The acceptance test

**Nothing the operator learns next session was already knowable at the end of this one.** A
close-out that reports clean and is followed by them asking *"anything to record?"* and getting a
real answer has failed — that happened twice in one day, and is why this exists.
