#!/usr/bin/env python3
"""Find prose references to files that resolve to nothing.

A pointer to a missing file reads as "nothing to read here", which is
indistinguishable from "there is nothing here". This repo has shipped two of them:
doctrine told every session to read `workspace/CLAUDE.md` (never existed), and
`operations/briefing-criteria.md` says "Run `neo-brief`" (never existed).

The hard part is NOT finding backtick-quoted paths — it is not crying wolf. The docs
write paths relative to several different roots (repo root, `workspace/`,
`workspace/knowledge/`, the citing file's own directory), so a naive existence check
reported 17 misses of which 17 were fine. A check that fires on correct behaviour is
worse than no check: it teaches you to skim the red.

So a reference counts as RESOLVED if it exists under any plausible root, or if its
basename exists anywhere in the repo. Only genuinely unresolvable references are
reported. Stdlib only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REF = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:md|py|sh|json|toml|db))`")

# Markdown links, `[text](path)`. Added 2026-09-22: `feedback/INDEX.md` carried
# `[F-007](resolved/F-007-….md)` for a file that had moved back out of `resolved/`, and this
# script reported the index clean — it never read a markdown link at all. And the loose
# basename fallback below would have excused it anyway, since the file exists one directory up.
#
# So links are resolved STRICTLY, against the citing file's own directory and nothing else.
# That is not a stricter taste; it is what a markdown link means — a renderer resolves it there,
# and a click on a link that "exists somewhere in the repo" still lands on nothing.
LINK = re.compile(r"\]\(([^)\s]+)\)")

# Roots a path in prose might be relative to, in the order a reader would try them.
ROOTS = ["", "workspace", "workspace/knowledge", "workspace/operations", "sensor", "harness"]

# The framework lives in a SEPARATE tree, and this repo's prose cites into it by design —
# `hooks/guard-banned-entities.py`, `skills/neo-draft/SKILL.md`. Without it those read as
# dangling, which is the cry-wolf failure this script exists to avoid. Resolved against the
# active config dir, so it follows CLAUDE_CONFIG_DIR rather than hardcoding one operator's path.
CONFIG_DIR = Path(
    os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".config" / "claude-neo")
)

# Referenced deliberately as non-existent, with the absence as the point.
KNOWN_ABSENT = {
    "workspace/CLAUDE.md",  # the corrected doctrine pointer; cited to explain the correction
    "neo_sensor/fetch.py",  # the F-001 defect (docs/lessons.md#f-001), cited in neo-close as the example it is
    # Add more as needed. CHANGELOG.md is a HISTORICAL log, so a path in it is a statement about
    # the past, not an instruction to read. A file that deliberately does not exist (a write the
    # banned-entities hook REFUSED, a note deleted on the operator's ruling) is silenced here by
    # name rather than by excusing CHANGELOG.md wholesale: a dangling pointer to a file that was
    # SUPPOSED to be written is still worth catching there.
}

# A project file cites into that project's OWN repo — `projects/active/<slug>/` may name
# `docs/roadmap.md`, which lives in that project's local repo. If the operator rules that local
# repos are a source (`knowledge/topics/local-project-repos.md`), so the pointer is
# correct and reporting it is the cry-wolf failure. Resolved only when a repo with the project's
# exact slug exists, so a citation into a repo that is not there is still caught.
# Colon-separated list of directories that hold the operator's local project repos.
PROJECT_REPO_ROOTS = [
    Path(p).expanduser()
    for p in os.environ.get("NEO_PROJECT_ROOTS", "~/projects").split(":")
    if p
]


def project_repo(citing: Path) -> Path | None:
    parts = citing.parts
    for i, part in enumerate(parts[:-1]):
        if part == "projects" and i + 2 < len(parts) and parts[i + 1] in ("active", "archive"):
            slug = parts[i + 2]
            for base in PROJECT_REPO_ROOTS:
                if (base / slug).is_dir():
                    return base / slug
    return None
# Every entry here is a reference whose POINT is that it does not exist. Adding to this set is
# how a deliberate citation is silenced — never by loosening the resolver, which is what would
# turn this back into the 17-false-positive check it started as.


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
    )
    return Path(out.stdout.strip())


def main(argv: list[str]) -> int:
    root = repo_root()
    targets = [Path(a) for a in argv[1:]] or sorted(root.glob("*.md"))

    # Every basename in the repo, so a shorthand reference can be matched loosely.
    #
    # This used to be `git ls-files` ALONE, and that made the check fire on files that are
    # sitting right there: `harness/heldout/SHINGLES.json` is deliberately gitignored, so an
    # accurate citation of it was reported as dangling (caught 2026-08-21). A file's tracking
    # status has nothing to do with whether a reader can open it, so walk the disk too.
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True, cwd=root
    ).stdout.split()
    basenames = {Path(p).name for p in tracked}
    SKIP = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        basenames.update(filenames)

    unresolved: list[tuple[str, str]] = []
    checked = 0

    for t in targets:
        path = t if t.is_absolute() else root / t
        if not path.exists():
            print(f"  !! target itself missing: {t}")
            continue
        text = path.read_text()
        # A link inside an inline code span is not a link: markdown renders it literally.
        prose = re.sub(r"`[^`\n]*`", "", text)
        for link in sorted(set(LINK.findall(prose))):
            target = link.split("#", 1)[0]
            if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target):
                continue  # an anchor, or a URL / mailto — not a file
            checked += 1
            if (path.parent / target).exists():
                continue
            unresolved.append((str(t), f"[link] {link}"))
        for ref in sorted(set(REF.findall(text))):
            checked += 1
            if ref in KNOWN_ABSENT:
                continue
            if any((root / r / ref).exists() for r in ROOTS):
                continue
            if (CONFIG_DIR / ref).exists():
                continue
            if (path.parent / ref).exists():
                continue
            repo = project_repo(path)
            if repo is not None and (repo / ref).exists():
                continue
            if Path(ref).name in basenames:
                continue
            unresolved.append((str(t), ref))

    print(f"{checked} file references and links checked across {len(targets)} document(s)")
    if not unresolved:
        print("0 unresolved.")
        return 0
    print(f"\n{len(unresolved)} UNRESOLVED — a pointer here reads as 'nothing to read':")
    for where, ref in unresolved:
        print(f"  {where}  ->  {ref}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
