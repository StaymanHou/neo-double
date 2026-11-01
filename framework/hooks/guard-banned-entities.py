#!/usr/bin/env python3
"""PreToolUse guard: refuse to write banned entities into the knowledge base.

**Framework, not world.** The mechanism lives here; the list of banned entities lives in the
workspace, at `workspace/operations/banned-entities.md`, because which entities a given
operator has ruled out is a fact about their world. This script would be equally correct for a
different operator with a different list.

**Why it exists.** `operations/read-priorities.md` recorded a ruling that a named network was
out of scope, and predicted in as many words that a future pass would notice the gap and write
the file a second time. On a real evaluation run, a session did exactly that — wrote a new
`knowledge/orgs/<banned>.md`, reasoned about the boundary, and offered to withdraw it only
after the write had landed. A warning that predicts its own violation and is then violated is
not a control.

**What it blocks:** writes to `workspace/knowledge/**` whose new content names a banned term.
**What it does not touch:** reading the corpus, searching for the term, discussing it in the
terminal, reporting it to the operator, or writing anywhere outside the knowledge base. The
fence is around the thing that persists and propagates into future drafts.

Fail-closed and loud. A guard that goes quiet when it cannot evaluate is the failure mode this
whole codebase keeps finding — see the `--until` bound that stopped being a filter without
saying so. Run `--self-test` to prove the matcher still bites.

stdlib only; no third-party imports, no network, no writes.
"""

from __future__ import annotations

import json
import os
import re
import sys

LIST_RELPATH = os.path.join("workspace", "operations", "banned-entities.md")
GUARDED_SEGMENT = os.path.join("workspace", "knowledge") + os.sep

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def allow() -> None:
    """Say nothing. An empty exit-0 leaves the tool call untouched."""
    sys.exit(0)


def deny(reason: str) -> None:
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout)
    sys.exit(0)


def parse_list(text: str) -> tuple[list[tuple[str, bool]], set[str]]:
    """Pull the ```banned and ```exempt blocks out of the operator's markdown.

    Fenced blocks rather than frontmatter so the file stays something the operator reads and edits, with
    the reasoning next to the rule. A term line may end in ` !cs` to demand a case-sensitive
    match — needed for short acronyms that would otherwise fire inside ordinary words.
    """
    def block(name: str) -> list[str]:
        m = re.search(rf"^```{name}\s*\n(.*?)^```", text, re.M | re.S)
        return [l.strip() for l in m.group(1).splitlines() if l.strip()] if m else []

    terms = []
    for line in block("banned"):
        cs = line.endswith("!cs")
        terms.append((line[:-3].strip() if cs else line, cs))
    return terms, {p.replace("/", os.sep) for p in block("exempt")}


def find_list(start: str) -> str | None:
    """Walk up from the target file looking for the workspace's ban list."""
    d = os.path.dirname(os.path.abspath(start))
    while True:
        candidate = os.path.join(d, LIST_RELPATH)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def hits(content: str, terms: list[tuple[str, bool]]) -> list[str]:
    found = []
    for term, cs in terms:
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", 0 if cs else re.I)
        if pattern.search(content):
            found.append(term)
    return found


def written_text(tool: str, ti: dict) -> str:
    """Only the text being ADDED. An Edit's `old_string` is what is already there — scanning it
    would refuse to let anyone remove a banned term, which is backwards."""
    if tool == "Write":
        return str(ti.get("content") or "")
    if tool == "Edit":
        return str(ti.get("new_string") or "")
    if tool == "MultiEdit":
        return "\n".join(str(e.get("new_string") or "") for e in ti.get("edits") or [])
    if tool == "NotebookEdit":
        return str(ti.get("new_source") or "")
    return ""


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        deny("banned-entity guard: could not parse the hook payload, so it cannot tell whether "
             "this write is permitted. Refusing rather than passing silently. Fix the guard at "
             "~/.config/claude-neo/hooks/guard-banned-entities.py, or run it with --self-test.")
        return

    tool = payload.get("tool_name") or ""
    if tool not in EDIT_TOOLS:
        allow()

    ti = payload.get("tool_input") or {}
    path = str(ti.get("file_path") or ti.get("notebook_path") or "")
    if not path:
        allow()

    norm = os.path.abspath(path)
    if GUARDED_SEGMENT not in norm + os.sep:
        allow()   # not the knowledge base — drafts, elicitation, code, anything else

    list_path = find_list(norm)
    if list_path is None:
        deny(f"banned-entity guard: this writes into workspace/knowledge/ but no "
             f"{LIST_RELPATH} was found above {path}. The list is the control; a missing list "
             "is not an empty list. Restore it before writing to the knowledge base.")
        return

    try:
        terms, exempt = parse_list(open(list_path, encoding="utf-8").read())
    except Exception as exc:
        deny(f"banned-entity guard: {LIST_RELPATH} could not be read ({exc}). Refusing.")
        return

    if not terms:
        allow()   # a genuinely empty list is a real state: nothing is banned

    root = list_path[: -len(LIST_RELPATH)]
    rel = os.path.relpath(norm, root) if norm.startswith(root) else norm
    if rel in exempt:
        allow()

    found = hits(written_text(tool, ti), terms)
    if not found:
        allow()

    deny(
        f"BLOCKED by the banned-entity guard: this write into the knowledge base names "
        f"{', '.join(repr(f) for f in found)}.\n\n"
        f"The operator has ruled these out of the knowledge base. See "
        f"workspace/operations/banned-entities.md — including § What to do when the guard "
        f"fires, which is the part to read now.\n\n"
        f"This is not an obstacle to route around. Do NOT edit the ban list to make the write "
        f"succeed, and do NOT rename the entity to slip past the match. Usually the right move "
        f"is to drop the material: noticing it again is the predicted failure, not a discovery. "
        f"If something about how the company actually runs genuinely depends on it, keep only "
        f"that consequence and leave the name out. If you think the ruling should change, ask "
        f"him and record it in workspace/elicitation/asked.md.\n\n"
        f"Reading, searching and reporting these entities are all still fine. Only recording "
        f"them in workspace/knowledge/ is blocked."
    )


SELF_TEST = [
    # (tool, path, text, should_block) — fictional entities throughout
    ("Write", "/r/workspace/knowledge/orgs/globex.md", "About Globex.", True),
    ("Write", "/r/workspace/knowledge/orgs/x.md", "the northwind network", True),
    ("Write", "/r/workspace/knowledge/topics/x.md", "NW shares the campus", True),
    ("Write", "/r/workspace/knowledge/topics/x.md", "she is a GX professor", True),
    # case-sensitive short acronyms must not fire on the lowercase word or inside longer words
    ("Write", "/r/workspace/knowledge/topics/x.md", "the gx and nw corners of the office", False),
    ("Write", "/r/workspace/knowledge/topics/x.md", "The NWOUTBOUND figure is 206.", False),
    # exempt files keep working
    ("Write", "/r/workspace/knowledge/orgs/nw.md", "NW = Northwind Academy; GX = ...", False),
    ("Write", "/r/workspace/knowledge/self/role.md", "I'm also a GX professor", False),
    # outside the knowledge base is untouched
    ("Write", "/r/workspace/drafts/d/candidate-a.md", "Globex sued the reporter.", False),
    ("Write", "/r/workspace/elicitation/asked.md", "Q: what is Globex to us?", False),
    ("Write", "/r/sensor/neo_sensor/x.py", "# northwind", False),
    # removing a banned term must not be blocked
    ("Edit", "/r/workspace/knowledge/orgs/x.md", "", False),
]


def self_test() -> int:
    """Prove the matcher bites, and that it does not bite what it must not.

    Exists because the alternative is trusting that a hook nobody has watched fire is still
    firing. Four of this project's checks have been wrong on first contact; this one gets to be
    checked in one command.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    fixture = os.path.join(here, "_self-test-list.md")
    with open(fixture, "w", encoding="utf-8") as fh:
        fh.write("```banned\nnorthwind\nglobex\nNW !cs\nGX !cs\n```\n"
                 "```exempt\nworkspace/knowledge/orgs/nw.md\n"
                 "workspace/knowledge/self/role.md\n```\n")
    terms, exempt = parse_list(open(fixture, encoding="utf-8").read())
    os.remove(fixture)

    failures = 0
    for tool, path, text, expect in SELF_TEST:
        guarded = GUARDED_SEGMENT in os.path.abspath(path) + os.sep
        rel = os.path.relpath(path, "/r")
        blocked = bool(guarded and rel not in exempt and hits(text, terms))
        ok = blocked == expect
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'}  block={blocked!s:5} want={expect!s:5}  "
              f"{path}  {text[:44]!r}")
    print(f"\n{len(SELF_TEST) - failures}/{len(SELF_TEST)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    main()
