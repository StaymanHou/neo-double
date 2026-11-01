# Workspace template — the operator's world

This directory is the **state** half of the assistant: what it knows about one operator's
world. The **framework** half (persona, doctrine, skills, hooks) lives in its own Claude Code
config dir and is shared by any operator. The test that decides which half a piece of
knowledge belongs to:

> *Would this still be true for a different operator at a different company?*
> Yes → framework. No → here.

Copy this directory to `<your-repo>/workspace/`, put `CLAUDE.md` at the repo root, and keep the
repo in git. **Every directory ships a `_TEMPLATE`** carrying its own format spec; a fresh
session learns the schema by copying the template, not by inventing a shape.

## Layout

| Path | Holds |
|---|---|
| `knowledge/people/<slug>.md` | who someone is, how he writes to them, what he weights with them |
| `knowledge/projects/active/<slug>/` | one directory per project: `README.md`, `decisions.md`, `people.md`, `open-questions.md` |
| `knowledge/projects/archive/<slug>/` | finished projects, moved (never renamed) |
| `knowledge/orgs/`, `knowledge/topics/`, `knowledge/brands/` | organizational and recurring-subject context |
| `knowledge/judgment/<slug>.md` | cross-cutting trade-off heuristics that belong to no single person |
| `knowledge/self/style.md` | how he writes: the voice model |
| `knowledge/self/values.md` | how he decides: the judgment model |
| `knowledge/self/preferences.md` | working preferences and rules for his standing artefacts |
| `knowledge/self/role.md` | his own factual entry: identities, cadence, what he owns |
| `operations/urgency-criteria.md` | what counts as urgent, his definition |
| `operations/briefing-criteria.md` | what a briefing surfaces, when he asks for one |
| `operations/banned-entities.md` | entities ruled out of the knowledge base, enforced by a hook |
| `elicitation/asked.md` | the say-it-once ledger: **read before asking anything** |
| `feedback/INDEX.md` + `F-NNN-<slug>.md` | his corrections, one file per **pattern**: **read before drafting** |
| `drafts/YYYY-MM-DD-<slug>/` | one directory per drafting task: request, context, candidates, blind ratings, what was sent |

## Conventions

- **Stable slugs.** Other files link by slug; a rename breaks every cross-reference. Keep a
  stale slug rather than rename it.
- **Lifecycle is a directory move** (`active/` → `archive/` with `git mv`), never a status field
  alone.
- **Never delete. Archive.**
- **Commit granularly.** The git history is the audit trail of how the knowledge base was
  built, and that history is the point.
- **Provenance on every entry:** which corpus command and what was read, or "the operator said
  so on YYYY-MM-DD". An unsourced claim about a colleague is worse than a gap.
- **Missing beats wrong.** Never guess a fact about a person. A wrong fact propagates into every
  future draft addressed to them. Leave the field empty, and ask in the turn.
- **ISO-8601 dates.** `last_confirmed:` blank means unconfirmed.
- **Never commit generated scripts that name withheld channels.** Match them in `.gitignore`
  by glob (`workspace/proposed-*.sh`), not by filename; dated filenames walk past literal rules.
- **Keep evaluation answers out of this tree.** If you build an evaluation harness that copies
  `workspace/` into fixtures, then anything written here (scenario ids, held-out message
  content, run results) becomes the graded session's own answer key.
