---
id: F-000
slug: <stable-kebab-slug>
opened: YYYY-MM-DD
last_seen: YYYY-MM-DD
times_seen: 1
layer: framework | world | harness | unclear
status: open | fixed | wont-fix | superseded
fix_landed: <path, or blank>
related: []
---

# <One line naming the PATTERN, not the incident>

## What he said

> Verbatim. His words, not a paraphrase.

Quote him rather than summarising. A paraphrase of a correction loses the judgment underneath it,
which is the half that matters — the same failure the whole project exists to avoid. If he said it
across several turns, quote each and date them.

## What the agent actually did

The specific artefact. Which draft, which file, which claim. Enough that someone can go and look at
it, and no more.

## What it should have done

Concrete. If the right behaviour depends on something only he knows, say so and put the question in
`elicitation/asked.md` rather than guessing here.

## Layer — where the fix goes

The project's central test: *would this still be true for a different operator at a different
company?*

| `layer:` | Means | Fix lands in |
|---|---|---|
| `framework` | any operator would want this | the framework config dir: doctrine or a skill |
| `world` | specific to him, his people, his orgs | `workspace/knowledge/` or `workspace/operations/` |
| `harness` | the rig measured it wrong; the agent was fine | `harness/` |
| `unclear` | not yet decided — say what makes it ambiguous | — |

**Getting this wrong is the expensive error**, in both directions: doctrine written into `workspace/`
is invisible to a fresh session, and his world written into the config dir is wrong for the next
operator and leaks into every fixture.

## Recurrence

One file per **pattern**. When it happens again, bump `times_seen`, update `last_seen`, and add a
dated line here — do **not** open a second file. Repetition is the signal he asked to be tracked, and
it is only visible if it accumulates in one place.

- YYYY-MM-DD — <where it recurred, one line>

## Is it measurable?

If this pattern can be reproduced against a held-out message, it should become a scenario or a check
in `harness/` — that is the bridge from *"he complained"* to *"it is measured and cannot regress
silently"*. Record the decision either way:

- **Reachable at a cutoff?** A check cannot need something the session could not have read.
- **Stable?** A check that flips on its own cannot verify a fix.
- **Scenario id / check id, once it exists:** written in `harness/`, **never here** — see below.

---

> **Two things must never be written in this directory.** `workspace/` is copied verbatim into every
> evaluation fixture, so anything here becomes the graded session's own world:
>
> 1. **No scenario ids and no scenario detail.** `freeze` refuses outright if the base names one.
> 2. **No held-out message content.** `harness/step8/leak-scan.py` is the check; run it before
>    committing.
>
> Name the *pattern* here and keep the *instance* in `harness/`.
