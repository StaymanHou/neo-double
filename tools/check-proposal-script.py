#!/usr/bin/env python3
"""Validate a `workspace/proposed-*.sh` disposition script before handing it to the operator.

**Why this exists — and the first version of this paragraph was wrong, which is the point.** A live
script was once found carrying `neo-channels set '-100123' ignore` directly under
a comment block headed *"yours to decide"* whose four lines said there were no grounds to propose a
value. That was diagnosed as a generator bug: the intended token was `DECIDE` and it had apparently
been lost. **It had not.** He had edited the word himself — *"It's because I made the decision and
changed DECIDE to ignore. It's not a problem here"* — which is exactly what the placeholder is for.
No defect existed. See `docs/example-corrections.md#f-009`, which is the record of the misdiagnosis rather than of a
bug.

The tool survives that correction because the underlying gap is real: doctrine says *"a command you
hand over is a command you have not tested"* (`~/.config/claude-neo/CLAUDE.md`), and
`tools/check-pointers.py` mechanises only the *prose* half of it — paths cited in documents.
Nothing mechanised a command the agent **generates**, and `neo-channels` is denied to the agent so
such a script can never be smoke-tested by running it. The *file* has to be checked instead of the
*effect*. What the correction changed is **when** the strict form applies: only before handover,
because after handover a decided value in that slot is his answer, not a defect.

Four checks:

1. **Every `set` line's disposition is a real one, or the literal `DECIDE`.** A typo like `ignroe`
   would exit 2 at run time and be obvious; the dangerous case is a *valid* disposition in a slot
   that was meant to be undecided, which nothing at run time can catch.
2. **A block headed "proposed: X" contains only `X` lines.** The header is a claim about what is
   below it. Its mirror — a real disposition under a *yours to decide* header — is reported as a
   **note and not an error**, because that is exactly what the file looks like *after he has
   decided*: he edits the one word in place, which is the whole design of the placeholder. Firing
   on that would be a check that fires on correct behaviour, and this project has already been
   burned five times by checks of that shape. **The strict form is `--pre-handover`**, which is
   the only moment at which a value in that slot cannot be his.
3. **`DECIDE` blocks come last.** The scripts are `set -e`, so a halt at the top costs him every
   well-grounded ruling below it.
4. **No chat_key appears twice**, and each is quoted — a bare `-100123` is an option to `set`
   in some shells.

Exit 0 clean, 1 with errors. Notes never fail the run. Use `--pre-handover` before writing the
`!` line, and the plain form afterwards.

**Read the history here, because it is the reason for the note/error split.** The first version of
this file treated a real disposition under a *yours to decide* header as an error, on the strength
of finding exactly that in a live script — and the actual explanation was that **he had edited the
word himself, which is what he is supposed to do.** So a tool written to catch a fabricated defect
would then have flagged his own ruling as one, twice over. `docs/example-corrections.md#f-009`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Read from the source of truth rather than retyped — a second copy of this tuple would drift,
# and drifting in *this* file means the validator starts disagreeing with the command it validates.
_CLI = Path(__file__).resolve().parents[1] / "sensor" / "neo_sensor" / "corpus_cli.py"
_MATCH = re.search(r"DISPOSITIONS = \(([^)]*)\)", _CLI.read_text(encoding="utf-8"))
if not _MATCH:
    raise SystemExit(f"cannot find DISPOSITIONS in {_CLI} — refusing to validate against a guess")
DISPOSITIONS = tuple(x.strip().strip("\"'") for x in _MATCH.group(1).split(",") if x.strip())

PLACEHOLDER = "DECIDE"
SET_LINE = re.compile(r"^neo-channels\s+set\s+(\S+)\s+(\S+)\s*(?:#.*)?$")
HEADER = re.compile(r"^#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")


def check(path: Path, *, pre_handover: bool = False) -> tuple[list[str], list[str]]:
    """`(errors, notes)`. Notes never fail the run — see the module docstring."""
    findings: list[str] = []
    notes: list[str] = []
    section = None
    section_line = 0
    seen: dict[str, int] = {}
    decide_seen_at: int | None = None
    last_proposal_at: int | None = None

    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        header = HEADER.match(line)
        if header:
            section, section_line = header.group(1).lower(), n
            continue
        m = SET_LINE.match(line)
        if not m:
            continue
        key_raw, disposition = m.group(1), m.group(2)
        key = key_raw.strip("'\"")

        if key_raw == key and (key.startswith("-") or not key.isalnum()):
            findings.append(f"{path}:{n}: chat_key {key_raw} is unquoted — quote it")
        if key in seen:
            findings.append(f"{path}:{n}: chat_key {key!r} already set on line {seen[key]}")
        seen[key] = n

        if disposition == PLACEHOLDER:
            decide_seen_at = n
            if section and "decide" not in section:
                findings.append(
                    f"{path}:{n}: {PLACEHOLDER} sits under the header {section!r} "
                    f"(line {section_line}) — a placeholder belongs in a 'yours to decide' block"
                )
            continue

        if disposition not in DISPOSITIONS:
            findings.append(
                f"{path}:{n}: {disposition!r} is not a disposition {DISPOSITIONS} "
                f"and is not {PLACEHOLDER}"
            )
            continue

        last_proposal_at = n
        # THE check. A valid disposition under a "yours to decide" header is the one failure mode
        # that run time cannot see, because there is nothing wrong with the command itself.
        if section and "decide" in section:
            message = (
                f"{path}:{n}: {disposition!r} under the header {section!r} (line {section_line})"
            )
            if pre_handover:
                findings.append(
                    message + f" — before handover that header says there are no grounds to "
                    f"propose a value, so this must be {PLACEHOLDER}."
                )
            else:
                notes.append(
                    message + " — this is what the file looks like after HE decides, so it is a "
                    "note. Confirm it was his edit before treating it as a defect."
                )
        elif section and section.startswith("proposed:"):
            want = section.split(":", 1)[1].strip()
            # A "proposed: X" header is only a CLAIM ABOUT THE DISPOSITION when X *is* a
            # disposition. Scripts also group by channel — `proposed: telegram`,
            # `proposed: outlook` — and reading those as promised dispositions reported 8
            # errors against a correct, already-run file (2026-08-24). A check that fires on
            # correct behaviour teaches you to skim the red, which is the failure this whole
            # tool exists to avoid; a grouping label is not a promise, so it is skipped.
            if want in DISPOSITIONS and want != disposition:
                findings.append(
                    f"{path}:{n}: {disposition!r} under the header {section!r} "
                    f"(line {section_line}) — the header promises {want!r}"
                )

    if decide_seen_at is not None and last_proposal_at is not None:
        if decide_seen_at < last_proposal_at:
            findings.append(
                f"{path}:{decide_seen_at}: a {PLACEHOLDER} line precedes proposals on line "
                f"{last_proposal_at}. These scripts are `set -e`, so an unedited placeholder here "
                f"halts the run and costs him the rulings below it. Put DECIDE blocks last."
            )
    if not seen:
        findings.append(f"{path}: no `neo-channels set` lines found — is this the right file?")
    return findings, notes


def main(argv: list[str]) -> int:
    args = argv[1:]
    pre_handover = "--pre-handover" in args
    paths = [Path(a) for a in args if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 0
    findings: list[str] = []
    notes: list[str] = []
    for path in paths:
        if not path.exists():
            findings.append(f"{path}: does not exist")
            continue
        e, nn = check(path, pre_handover=pre_handover)
        findings.extend(e)
        notes.extend(nn)
    for f in findings:
        print(f"error: {f}")
    for f in notes:
        print(f"note:  {f}")
    n = sum(1 for p in paths if p.exists())
    mode = "pre-handover (strict)" if pre_handover else "post-handover"
    print(
        f"\n{len(findings)} error(s), {len(notes)} note(s) across {n} script(s); "
        f"mode={mode}; validated against DISPOSITIONS={DISPOSITIONS}"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
