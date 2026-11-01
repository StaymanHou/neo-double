# `neo-eval` — the evaluation harness

**Two suites, one rig.** `knowledge` asks what the knowledge base knows; `draft` asks whether a
draft is one the operator would actually send. `--suite` picks; they are reported separately and
share every mechanism below.

Testing an assistant by hand works once: you open a cold session, type questions at it, and read
the transcript. It finds real defects. What it cannot do is tell you next month whether the base
got **worse**, because nothing about it is repeatable — not the questions, not the corpus underneath,
not the grader. This makes it repeatable.

```
harness/neo-eval freeze --cutoff <YYYY-MM-DD>   # pin the base; it stops moving
harness/neo-eval run    --fixture <id>          # real assistant sessions against the pinned copy
harness/neo-eval judge  --run <id>              # grade the transcripts
harness/neo-eval report --run <id>              # the table
harness/neo-eval list                           # fixtures, scenarios, runs

harness/neo-eval judge  --run <id> --tag b      # re-judge WITHOUT overwriting the verdicts
python3 harness/judge-stability.py --run <id>   # diff the re-judges: what moved on its own
```

Stdlib Python only. No venv, deliberately — the harness must keep working when `sensor/`'s
environment does not.

**Configuration.** Paths come from the environment, with defaults:

| variable | default | what |
|---|---|---|
| `NEO_CONFIG_DIR` | `~/.config/claude-neo` | the framework config dir the graded session runs under |
| `NEO_CORPUS_BIN` | `~/.local/bin/neo-corpus` | the real `neo-corpus` the shims wrap |
| `NEO_STATE_DIR` | `~/.local/state/neo` | where the fenced store lives (denied to graded sessions) |

`config/graded-settings.json` is a **template**: permission rules need literal paths, so it carries
`{WORKSPACE}`, `{STATE_DIR}`, `{CORPUS_BIN}` and `{SENSOR_BIN}` tokens and each run writes its own
filled-in copy beside the transcript.

---

## Why it lives here and not in `sensor/`

`sensor/` is the fenced layer, and its whole claim is that the fence is enforced *by construction* —
its fence tests parse that source and fail if a read path so much as names the raw column. A test rig
that wraps `neo-corpus` and rewrites its arguments has no business inside the thing it wraps; putting
it there would dilute exactly the property that makes the sensor trustworthy.

`workspace/` is the operator's world — what the assistant knows. A harness is machinery, not
knowledge. So it is a sibling top-level directory.

---

## The three parts

### Freeze

A fixture is a copy of `CLAUDE.md` + the whole of `workspace/`, taken at a named commit, with a
sha256 of every file recorded in `MANIFEST.json`. `run` recomputes those hashes and refuses to start
if anything moved: a fixture that drifts makes two runs incomparable, which is the only reason it
exists.

`HANDOFF.md` is deliberately excluded — it is a note *about the work*, and may name the harness and
what it is testing.

**Fixtures are never edited and never re-frozen under the same label.** Freeze a new one.

### Run

For each scenario, one real `claude -p` session:

- **cwd** is a throwaway copy of the fixture in `$TMPDIR`, outside the repo.
- **`CLAUDE_CONFIG_DIR`** is the operator's own. The subject under test is the assistant they
  actually run, doctrine and all — not a reconstruction of it.
- **PATH** is prefixed with `harness/shim/`, which clamps the corpus (below) and refuses
  `neo-fetch` and `neo-channels`.
- **permissions** come from the rendered `config/graded-settings.json`.
- the full event stream is captured to `stream.jsonl` and rendered to `transcript.md`.

Writes are **not** blocked. The session may edit its copy freely; the runner diffs the copy against
the fixture afterwards and reports any change as a failed mechanical check. A blocked write is an
error message; an attempted write is a finding.

### Judge

A second `claude -p`, run with **`--safe-mode`** so none of the assistant's doctrine loads — a
grader carrying the subject's own priors is grading itself. It gets the transcript, the ground truth
the graded session never saw, and the check list, and returns one verdict per check with a verbatim
quote as evidence.

---

## Scoring: checks, not a score

A run produces a table of named checks, each `pass` / `fail` / `unclear`. There is no number.

A number tells you something moved. A failed check called `never-guesses-a-person` tells you what —
and in practice every defect worth catching is caught by someone looking at one specific claim, not
at an aggregate.

| | |
|---|---|
| `MUST`, `MUST-NOT` | gating — a scenario holds only if all of them pass |
| `SHOULD` | reported, does not gate |
| `unclear` | the judge could not find evidence either way. **Treat as a defect in the check**, not a result. |

Every check must be falsifiable against the ground truth. *"Answers well"* is not a check; *"names
<colleague> as the person driving it"* is.

### Switching a check off

Append `:off` to the severity — `- C4 [MUST-NOT:off] …`, or in `neo_eval.py` for an inherited one.
The check is not sent to the judge and does not gate; it keeps its text and its real severity, prints
as `off` in the detail, rides in `SCORES.json` as `"enabled": false`, and the report opens with a
warning naming every disabled check. Deleting four characters turns it back on.

**Not commented out, and not deleted, deliberately.** A check that vanishes is invisible to the
report, and an absence that reads as a result is exactly how harnesses mislead. A run with the
never-guess check switched off must not be mistakable for a run that found no invention.

Some checks are expensive: the provenance checks (`U1`, and any scenario check asking "was this
specific reachable?") need the judge to see **every tool result the session read**, inline. Switching
them off shrinks the judge prompt by roughly a fifth. It is a legitimate lever while iterating on a
fix; **turn them back on for any baseline you intend to compare against** — and keep them on for a
weaker subject model, which (measured, below) invents more.

### Mechanical checks

Applied to every scenario automatically, need no judge, and perfectly stable. They cover the failure
modes a language grader is worst at noticing:

| check | fails when |
|---|---|
| `base-unmodified` | any change to knowledge, operations or `CLAUDE.md` in the session's copy. Writes to `workspace/drafts/` (filling one in is the job) and `workspace/elicitation/` (logging a question asked is correct) are permitted but listed |
| `no-corpus-bypass` | the session reached the corpus other than through `neo-corpus` |
| `stayed-in-sandbox` | a tool call referenced the live repo or the harness |
| `no-denied-attempts` | warns on every permission denial — denials say what it tried |

Anything a session wrote is copied to `wrote/` beside its transcript before the sandbox is destroyed.

**`neo-eval recheck --run <id>` re-derives all mechanical verdicts from stored artefacts**, without
re-running a session. The checks are code and code has bugs — expect several to fire on *correct*
behaviour before they settle — and re-running a whole slate to fix a substring would be absurd.

---

## The cutoff, and what isolation actually means

**The cutoff belongs to the scenario, not the fixture.** It is the last corpus moment the graded
session may reach, and it sits just before the moment the ground truth records — so the session sees
exactly what the operator saw, and nothing of what they did next. Too late and it reads its own
answer; too early and you are testing clairvoyance instead of judgment.

`shim/neo-corpus` appends `--until $NEO_EVAL_CUTOFF` to every row-emitting verb, last, so a session's
own `--until` cannot widen the window — and only when it would actually narrow it. (An earlier
version always appended; a session asking for an earlier `--until` silently got the cutoff, concluded
the *sensor* ignored its filter, and filed a false bug. A clamp that makes a tool lie about its own
filters manufactures false findings.)

**The clamp is measured, not trusted.** Before every scenario the runner asks, through the shim, for
traffic after the cutoff, and aborts the run if any comes back. This is not paranoia: a date bound
compared lexically with no validation turns `--until garbage` into a filter that silently stops
existing and returns the whole corpus looking windowed. A harness that trusted the flag would grade
every scenario against the answer key and report a clean run. Validate the bound in the sensor *and*
keep the probe, because a gate you have to remember to use is not a gate.

**What isolation guarantees here, honestly stated.** Two things, both structural:

1. The graded session **cannot reach the live knowledge base.** It works on a copy in a temp
   directory; the original is untouched no matter what it does.
2. The graded session **cannot read past its cutoff**, verified live before each run. The cutoff
   rides on an environment variable, so it holds even if the session calls the shim by absolute path.

What it does **not** guarantee: the session runs in `auto` permission mode, so arbitrary Bash is
available, and a session that went looking could `cat` a scenario file out of this repo. `Read` of the
repo is denied and the sandbox is elsewhere, but a blacklist of shell commands would be convention
rather than construction. So it is **detected rather than prevented**: `stayed-in-sandbox` fails the
scenario if any tool call references the repo or the harness, and a run with that check red is void.

Two refinements learned the hard way: a session calling the clamped shim **by its absolute path** is
correct behaviour (the clamp still holds) and should not trip `stayed-in-sandbox`; and whether a
**denied** escape attempt should gate (it is only a warning under `no-denied-attempts`) is an
instrument decision for the operator, not the agent — it can move a headline a lot.

`stats` and `channels-pending` take no date filter and pass through unclamped, so `stats` will print
the corpus's true end date. That tells a session a cutoff is in force. Nothing here tries to hide that
it is a harness — only to stop it reading the answers.

---

## Keeping the answer key out of the base

A fixture is a copy of `CLAUDE.md` and `workspace/`. That is the world the graded session reads as
its own. **Anything written there about what a scenario tests is the answer key**, and `freeze`
refuses to build a fixture whose base names a scenario id (`--allow-leak` overrides; don't).

The leak arrives by the most ordinary route available: someone writes up the results of a run in the
current-state note — naming each scenario and what it failed — and the next fixture is cut from the
working tree. The next graded session then opens its reply by naming the scenario it is sitting in.
Documenting a finding in `CLAUDE.md` is the right instinct; that file being the fixture's own contents
is the trap.

**So run results, scenario descriptions and per-scenario findings live in this directory and in
`CHANGELOG.md`.** Neither is copied into a fixture. `CLAUDE.md` gets design decisions and a pointer,
nothing scenario-shaped. `workspace/operations/` is inside the fixture too — anything there that
describes the harness leaks the same way.

## Adding a scenario

Copy `scenarios/_TEMPLATE.md`. The fields that matter are `cutoff` (above) and `generated_from`,
which must name the `neo-corpus` call the scenario came out of. A check that cannot be traced back to
a corpus row will rot silently.

**Draw knowledge scenarios from traffic the base has never seen** — ingested after the base was
built. Anything drawn from what the base was built on asks it to recite; test *use*, not recall.

Write traps. A scenario whose correct answer is *"I don't know"*, *"you ruled that out"*, or *"I can't
see that channel"* is the highest-signal kind, because a degrading base confabulates there first.

Scenario files are the operator's own and are **not** shipped with this framework — they are made
from real traffic.

---

---

# The `draft` suite

A draft scenario does not ask what the base knows. It puts a session where the operator stood **one
minute before they sent a real message**, asks it to draft, and compares. The message is one of a set
withheld from the voice build (`heldout/README.md`), so the model being scored has never read it.

```
harness/neo-eval run   --fixture <id> --suite draft
harness/neo-eval judge --run <id>
harness/neo-eval report --run <id>
python3 harness/step8/review.py --run <id>     # every draft beside the real message, for reading
```

`report` answers *which checks passed*. **`review.py` answers what it actually wrote** — the prompt,
the real message, and each candidate with its title and notes stripped, one file per run. That is the
question the operator actually asks. `REVIEW.md` is gitignored: it is the densest copy of the answer
key in the repo.

## Scoring a draft

**Fidelity gates; divergence is reported.** A red means *"not what they would send"*. Where a draft
departs from the real message, the judge names the departure in its note — including when the
departure is an improvement, and including when it passes. The report collects those into a
**Divergences** section, so a draft that beats the real one reads as a difference rather than a
defect, and a run six months from now still means the same thing.

**Collect divergences regardless of verdict.** A draft can pass every check and still name several
divergences in its notes; a collector keyed on failure reports none of it and the run looks like an
empty clean sweep.

Every draft scenario inherits **D1–D6** on top of `U1`/`U2`:

| | |
|---|---|
| D1 | same decision |
| D2 | same language |
| D3 | no softened position |
| D4 | matching structure |
| D5 | matching sign-off |
| D6 | divergence named |
| U1 | no invented specific (everything asserted was reachable) |
| U2 | names the gap rather than filling it |

Its own checks carry what is specific to that message — the position it holds, the number it quotes,
the person it hands the decision to.

## Six mechanical checks, and only one of them gates

| check | gates | what it measures |
|---|---|---|
| `answer-not-in-context` | **yes** | containment of the real message in everything the session read |
| `draft-is-not-a-copy` | no | containment of the real message in the closest candidate |
| `language-match` | no | CJK characters per candidate against the real message's script |
| `full-width-punctuation` | no | half-width `,` `.` against a CJK character, if the operator writes full-width |
| `typos-not-reproduced` | no | the operator's known, unintentional misspellings |
| `length-band` | no | closest candidate's length against the real message's |

`answer-not-in-context` is the after-the-fact analogue of the clamp probe, taken on the artefact
rather than on the tool. It catches what the pre-run gates cannot: a paraphrase reached by another
route, a quotation of the message inside a later reply, a cutoff that was right in the file and wrong
in the environment. Containment is measured over 32-character windows, threshold 0.30, against a
measured floor of ~0.03 for unrelated prose — the calibration is in `heldout/README.md`; don't redo
it by feel.

The other five **measure and warn**, deliberately. Each can be legitimately off — a session answering
in chat wraps an English explanation around a Mandarin draft — and a mechanical check that fails
correct behaviour is worse than none. The gate is the judge's D-checks; these are the numbers under
them.

**They read the candidate, not the file.** A candidate file is a message plus a title plus notes on
the choices — measured whole it can be ten times the real message. `_message_body` strips the
scaffolding, and each check takes the best candidate rather than the concatenation, because a set of
alternatives is a menu and not a message.

**The judge grades the recommended candidate; the mechanical checks grade the best one.** Tell the
judge which candidate is the recommendation and to name, in the note, when another would have passed.
A session whose second option was right did something different from one where every option was
wrong; the asymmetry is deliberate and disclosed.

## Three refusals before a scenario runs

1. **A cutoff that does not exclude the message.** `--until` is inclusive to the minute and date-only
   means the whole day, which means the answer. `locate.py` prints the right value.
2. **A twin inside the window.** People broadcast — the same long message to several recipients with
   only the greeting changed, ~97% similar, different hashes. A cutoff just before the ground truth can
   still leave a near-identical copy readable under another name, and a session that finds it scores
   near-perfectly for the wrong reason. That *inflates* a score rather than leaking a body, which is
   why it is a gate and not a note. A held-out message whose twin precedes it can't be excluded by any
   cutoff and is simply not usable. Exact re-sends are tracked too: one hash, several (date, chat)
   copies, and the cutoff must precede the earliest.
3. **A prompt that reuses the message's wording.** Five consecutive shared words and the scenario is
   refused. Runs made only of proper nouns and numbers (an event title) are exempt — a name is not
   phrasing.

## Writing the prompt

Write it as **the operator** would type it to the assistant: *"My boss wants me to tell a vendor we'll
commit by end of month. I don't think we can. Draft the email."* Blunt, one or two lines.

**It may say what they wanted. It may not say how they said it.** The decision, the goal, and any
fact only they hold are fair; the reasoning, the structure, the numbers they derived, and any phrasing
from the real message are not. A prompt that carries the message's argument produces a transcription
and measures nothing.

Facts the operator genuinely held and the corpus does not — a phone call, what a colleague said in a
room — belong **in the prompt**. That is not a concession; it is the actual product. They hand over the
facts, the assistant writes it as them. (When a check fails a draft for not knowing such a fact, fix
the prompt, not the check.)

## Ground truth is materialised, not committed

`step8/ground-truth/` is **gitignored**, and rebuilt in one command:

```
python3 harness/step8/locate.py                          # the candidate table — metadata only
python3 harness/step8/locate.py --materialise <hash8> --id S-0NN
```

A held-out body is corpus content, and corpus content never enters git. More importantly, these
bodies are the **answer key for every future draft run**, while `harness/scenarios/` is readable by
any ordinary session — including the next voice build, which `shim-voice` cannot protect from a plain
`Read`. So the scenario file carries a citation and the text lives outside git.

`locate.py` also recovers what `heldout/MANIFEST.json` deliberately does not record: the **minute** a
message was sent, and the **thread in front of it**.

> **Email context may not come from `thread`.** If mail chats are keyed by sender, the operator's own
> sent mail is one "chat" with zero inbound rows — which reads as *"they were broadcasting"* when it
> means *"the other half is filed under the other party"*. Get email context from the conversation,
> and find the conversation by **hashing its rows**, not by matching its subject: mail systems keep the
> longest subject form, so `Re: … | Workshop` is not equal to `Re: … | Workshop (Tentative)`. Both
> are plausible absences, which is the shape to distrust.

## Mid-length controls

Hold out some short messages (120–300 characters) as **controls**, scored and reported on their own
line, never folded into the headline. They tell *"it can write long"* apart from *"it can write like
them"*: a green control against red long strata is a finding about length, and the reverse is a
finding about voice. A control is not a soft scenario — a short message can hold a hard position.

## Keeping the answer key out of git

`python3 harness/step8/leak-scan.py` measures every held-out message against everything git tracks
**or would track on the next `git add -A`**. Run it before committing anything in this directory. The
routes it exists to catch:

| route | why it leaks | fix |
|---|---|---|
| the knowledge base | it may quote a held-out message, if it was written before the holdout was carved | measured per scenario on every run (`base-does-not-quote-the-answer`: warn at 15%, fail at 30%) |
| scenario files | an author quotes a short message in full "because it's short" | characterise, never quote |
| judge verdicts and reports | a judge's evidence must be verbatim, so on a draft scenario it quotes the answer | keep `verdict.json` / `REPORT.md` untracked; `SCORES.json` carries verdict-per-check with no prose |
| `runs/**/wrote/` | a good draft of a withheld message can approach 100% containment | untracked |

**Carve the holdout before writing the knowledge base**, or measure the base against it afterwards.
The carve protects the voice build; it does not protect the fixture.

---

# What the measurements taught

General findings from running both suites on a real base. They are about the instrument and about
drafting assistants, not about any particular message.

## Judge stability

Every verdict is a language model reading a fixed transcript, so run-to-run comparisons are
unreadable until you know the noise underneath. Measure it: judge the **same stored transcripts**
several times (`judge --tag b|c|d`), everything else held constant, and diff with
`judge-stability.py`. It needs no new sessions.

What a strong grader showed, re-judged four times over a twelve-scenario slate:

- **The headline did not move** (spread 0), and every scenario's hold/no-hold outcome was unanimous.
- **About one check in eleven disagreed with itself** across samples.
- The two facts are compatible only because every gating check that flipped sat in a scenario already
  failing on a stable check. **Fix the stable check and the unstable one becomes load-bearing** —
  exactly when a fix session starts reading noise as progress.

Reading rules that follow:

- A one-scenario difference between runs is meaningful; a difference in *which checks* failed inside
  a scenario is within variance. **Re-judge before believing a check-level change.**
- **Only aim fixes at checks that fail in every sample.** A fix that targets an unstable check cannot
  tell its own success from a re-roll.
- **Two samples is not a stability read.** Opposite flips can cancel to a perfect-looking spread of 0,
  and a conclusion drawn at two samples reversed at four. `judge-stability.py` reports the per-scenario
  flip list, not just the spread, for that reason.
- **The judge sometimes drops a check entirely** (returns a shorter list). The report renders a missing
  check like an `unclear`, but the defect is in the judge, not the check — look at `verdict.json`.
- **Pin the grader.** A model alias can move underneath you; record the resolved model id in each run.

## Subject and grader are different choices

- **A cheaper/faster model is usable as the subject** (the model that drafts day to day) — its
  scenario-level verdicts were about as stable, and it ran ~2.5× faster.
- **It is not usable as the grader.** As a judge it roughly doubled check-level noise and, critically,
  flipped the hold/no-hold outcome of several scenarios on a single re-judge — a grader like that cannot
  measure a one-scenario fix, which is the whole point. It was also systematically harsher, so headline
  numbers across graders are not comparable at all.
- **So: the model you will actually run as the subject, the strongest model as the grader**, settled
  once and not re-litigated per run. Re-baseline on the subject you intend to harden *before* working
  the fix queue, so the fixes harden that model.
- **A weaker subject invents more and flags gaps less** — roughly twice the `U1` failure rate across
  more scenarios. Keep the provenance checks on for it.

## What drafting assistants get wrong

None of the recurring failures were wrong facts. They were places the draft is more conventional than
the person:

1. **Shape and length are chosen after the content, not before it.** One paragraph becomes a bulleted
   agenda; a three-sentence acknowledgement becomes three paragraphs; a terse standing format becomes
   prose under headings at several times the length. *It writes like an assistant, not like them.*
2. **Hedges get added to flat positions, and decision rights move.** A flat refusal grows an agreeing
   preamble, "right now", and "What do you think?"; a request that hands a decision to a superior comes
   back as a decision already made. Both directions of one defect: taking a decision they handed away,
   handing away one they took.
3. **The judgment they volunteer gets dropped** — the unasked-for caveat that costs them something, the
   reason they corrected their own number. It tends to live one file deeper than the session chose to
   read.

Reading the voice model is not the same as applying it: sessions that had read `style.md` still
failed on shape. And a non-native register (in the measured base, Mandarin) can be the *strongest*
stratum, not the weakest — test it rather than assuming.

**Negative claims in the voice model don't generalise from the sample they came from.** "Never signs
off with X" is honestly scoped to the messages read — and the held-out set is precisely what can show
it's false. Don't patch the voice model from a held-out finding while that scenario is live; doing so
informs the next run of the scenario that produced it.

**Checks fail too.** Expect to find defects in your own checks: a check demanding a format that, before
the cutoff, exists nowhere in the corpus is testing clairvoyance; a check that requires reasoning the
prompt withheld should be fixed in the prompt; a compound check ("casual register with an emoji where
the vendor's pressure is mentioned") should be split so an omission fails its own check.

---

## Cost

Each scenario is one full assistant session plus one judge session, so a 12-scenario run is 24
sessions — roughly an hour of wall clock for a draft slate on a strong subject, about 70 seconds per
judge. Run a single scenario with `--scenarios <id>` while iterating on a check; run the slate when you
want the table. `recheck` re-derives every mechanical verdict and re-renders every transcript in about
a second, and needs no sessions at all — use it whenever a check changes. A re-judge pass over stored
transcripts is cheap next to a slate of sessions; spend it.
