# Feedback — the backlog index

His corrections, and what became of them. **One line per pattern**; the detail lives in
`<id>-<slug>.md` beside this file (open) or in `resolved/` (closed).

**Read this before drafting anything.** It is the standing record of what he has already told
this project to stop doing. `elicitation/asked.md` is its sibling and answers a different
question: that file holds **questions the agent asked and his answers**; this one holds
**corrections he volunteered**.

## Open

| id | pattern | layer | seen | last | fix lands in |
|---|---|---|---|---|---|
| F-001 | (synthetic) routine work handed back as a command for him to run | framework | 1 | YYYY-MM-DD | doctrine: which commands are his |

## Closed

**`how` is a column on purpose.** A `wont-fix` listed under *Fixed* reads as a solved problem.
Say which kind of closed it is.

| id | pattern | layer | seen | how | landed in | when |
|---|---|---|---|---|---|---|

---

<a id="meta-lessons"></a>
## Meta-lessons (from the reference system, across all its corrections)

1. **A fix written for one form of a failure rarely reaches the same failure in another form.**
   Several corrections came back looking different, and the first fix's wording didn't cover them.
   Phrase rules around the *move* the agent made, not the *object* it made it on.
2. **A tool that reports absence is making a claim.** More than once, the agent faithfully
   repeated a silence that its own sensor had manufactured: a truncated read, or a field it
   never captured.
3. **One data point is not doctrine.** Record a correction in the operator's workspace first, and
   generalize it into the framework only after a second instance, or when the operator says so.
   Rules built from a single case, or from the operator merely accepting a proposal, went wrong.

For what these corrections looked like in practice, see `docs/example-corrections.md`. It's
illustrative only, so don't copy those entries here.

## How this is kept

- **One file per pattern, not per utterance.** When it recurs, bump `times_seen` and
  `last_seen` and add a dated line to its § *Recurrence*. Do not open a second file.
- **A recurrence reopens the file; it does not get a new id.** If the fix written for the first
  surface missed a second surface, that is one problem the first fix missed, not two problems.
- **One data point is not doctrine.** Record it in the world layer and wait for a second
  instance, or his word, before generalising into the framework.
