# <Assistant name> — current state

This repo is the assistant's **state**: what it knows about the operator's world, plus the fenced
sensor that reads the message corpus. The *framework* (persona, doctrine, skills) lives in the
assistant's own Claude Code config dir. Run sessions here with that config dir active.

> **Never write framework doctrine into this repo, and never write the operator's world into
> the config dir.** The test: *would this still be true for a different operator?*
> Yes → config dir. No → `workspace/`.

**Picking up a new session?** Read this file first, then `HANDOFF.md`, the boundary note from the
last session, with the next step and what not to redo. This file wins if they disagree.

---

## His vocabulary — read this before acting on a short instruction

**These look ambiguous and are not.** They live here rather than in `knowledge/self/` because they
gate almost every instruction he gives, and this file is loaded every session.

| he says | it means | it does NOT mean |
|---|---|---|
| **"catch up"** *(example)* | **You** catch up: pull new traffic, **read** it, then print any pending decisions that block work | that *he* needs catching up. He reads his own messages. **No briefing.** |
| **"brief me"** *(example)* | Produce a briefing | — |

**The general rule this table serves (framework doctrine):** when an instruction could mean
*update yourself* or *produce a deliverable*, it means the first. Briefings, reports, comparisons
and summaries are produced on explicit request, in his words, not inferred from an ambiguous verb.
The cheap reading, if wrong, costs one sentence ("pulled; want a briefing?"); the expensive one,
if wrong, spends a long turn on something unwanted.

**Add to this table whenever he defines another term.**

---

## Status — YYYY-MM-DD

What exists, what works, what is next. Counts here go stale: say how to recount rather than
trusting the number. **Do not write evaluation results here** if this file is copied into
evaluation fixtures; a result written here becomes the next run's answer key.

## Standing facts

- His identities across channels, and the addresses that are NOT him.
- Channel skew: how his outbound volume splits by channel, and where his formal register lives.
- People with two names, already merged; people with the same name, kept apart on purpose.

## Layout

See `workspace/README.md`.

## Conventions

- ISO-8601 dates. Stable slugs. Lifecycle is a directory move. Never delete; archive.
- Commit granularly. Cite provenance. Never guess a fact about a person.
