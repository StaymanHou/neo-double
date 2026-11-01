---
name: neo-prune
description: >-
  Compact the project-root CLAUDE.md when the harness warns that instruction files exceed the
  150k-char total limit (or it has visibly grown). Moves dated narrative, per-pull counts and
  decision logs to workspace/history/ verbatim and leaves current-state summaries plus pointers.
  Use when the operator says prune, compact CLAUDE.md, the instruction files are too big, or
  the session-start warning about instruction-file size appears.
argument-hint: "<optional mode: 1=step-by-step | 2=batch-approve | 3=autopilot | 4=dry-run>"
---

# Neo prune

Adapted from a generic CLAUDE.md-pruning skill. **This file is the copy; do not read the default
profile (`~/.claude/`) to run it.** First real run: project `CLAUDE.md` 118.0k → 36.9k chars.

## Scope guard (hard)

- Edits **`~/neo-workspace/CLAUDE.md` only** (confirm with `git rev-parse --show-toplevel`).
- **Never edit `~/.config/claude-neo/CLAUDE.md`** (doctrine; its own lifecycle). If it is the file
  over budget, report that and stop. The warning counts both files together, so the project file
  is where the room is.
- **Never delete.** Everything removed is moved verbatim.
- Never write anything into `workspace/knowledge/`. Destination is **`workspace/history/`**:
  `harness freeze` snapshots `knowledge/`, and run results there become the next run's answer key
  (this happened once; see the harness README).

## Procedure

1. **Measure.** `wc -c` both CLAUDE.md files. The warning's limit is **150k combined**; the
   skill's own target is project file **≤ 40k** for headroom.
2. **Inventory.** Rank `##`/`###` sections by size:
   `awk '/^#{2,3} /{if(h)printf "%7d  %s\n",n,h; h=$0; n=0} {n+=length($0)+1} END{printf "%7d  %s\n",n,h}' CLAUDE.md | sort -rn | head -15`
3. **Triage** each large section:
   - **EXTERNALIZE** — dated narrative, per-pull/per-sweep counts, question-closure chronology,
     per-use send logs, recogniser decision logs, completed-sequence tables. Move to
     `workspace/history/<slug>.md`, leave a **current-state summary + pointer**.
   - **KEEP** — doctrine, the operator's vocabulary table, layout, the fence, gate designs, **open
     defects and uncovered gaps**, standing facts, conventions, anything they have to see every
     session.
   - **DELETE** is not available. Superseded text is moved like the rest.
   - A pointer alone is wrong when the section holds a **rule** or an **open item**: carry that
     into the summary (first run: footnote rule, chat_key rule, unfixed approval-per-directory
     defect, uncovered OTP shapes).
4. **Modes** (arg or menu): 1 step-by-step, 2 batch-approve (recommended), 3 autopilot (applies
   nothing here — there is no HISTORICAL marking to key on; say so), 4 dry-run. Present the
   candidate table with sizes and verdicts, and **wait** for the operator's mode and approval.
5. **Apply mechanically.** Copy the original aside to the scratchpad first. Cut by line range
   with a script, bottom-up so earlier ranges stay valid. History file header: title, one-line
   scope, "moved verbatim on <date>, figures as of the date each paragraph was written, re-measure
   before relying", and the freeze/answer-key reason. Summaries are hand-written, so say plainly in
   the report that they are the only new wording.
6. **Verify.** Re-run `wc -c`; run `python3 tools/check-pointers.py` (must be 0 unresolved);
   `git status`. Do not commit — committing is the operator's act unless they ask.
7. **Report**: before/after chars, combined total vs 150k, files created, which summaries are new
   wording, and the commit question.

## Pitfalls

- Summaries must not repeat the staleness problem: stamp every number with its date and say
  "recount before trusting".
- Don't write run results or scenario names into the summaries; `CLAUDE.md` is copied into
  fixtures (`freeze` refuses if it names a scenario id).
- A pointer to a file that does not exist reads as "nothing to read here". Check-pointers it.
- Re-running is cheap: already-pointered sections fall into KEEP.
