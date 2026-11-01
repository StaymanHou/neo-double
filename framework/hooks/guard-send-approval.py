#!/usr/bin/env python3
"""PreToolUse guard: an approval quote must be words the operator actually typed.

**Why it exists.** `neo-send` and `neo-mail` record the operator's approval with `--approve
--quote '<their words>'`, and both modules say plainly what that gate cannot do: the agent runs
both halves, so nothing in the tool can tell a real quote from an invented one. The doctrine
sentence carrying the weight was *never write a quote they did not say*. When the Outlook path was
built, the operator asked for the gate to be made proper — *"make sure yourself have the prompt to
properly gate it. always ask for my confirmation before any send"* — and a sentence is a prompt,
not a gate.

This makes the quote checkable. The harness hands every hook the session transcript, which the
agent cannot write to, and the operator's messages are in it. So `--approve` is refused unless its
`--quote` appears **verbatim in their most recent message** (whitespace normalised, nothing else). That means:

- a quote they never typed is refused — the fabricated-record case, now blocked rather than logged;
- a quote from an earlier turn is refused — approval is per message, and the one that counts is
  the one they just sent, *after* they saw the preview;
- approving and committing in one command is refused, so the two steps cannot be run as a single
  decision of the agent's.

**What it still cannot do:** know that their latest message was a reply to *this* preview. If they
type "send it" about something else, the words are real. The preview-before-asking rule in
doctrine covers that; this covers the part that can be checked.

Framework, not world — true for any operator. Fail-closed: if the transcript cannot be read, the
approval is refused. stdlib only; no network, no writes. `--self-test` proves it still bites.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

SENDERS = ("neo-send", "neo-mail")

# An INVOCATION, not a mention: the tool name at a command position, optionally path-qualified.
# The first version matched the bare substring and refused the edit that wrote this doctrine —
# prose about the tools is not a call to them. Heredoc bodies and `#` comments are stripped first
# for the same reason. Erring toward refusal is still the direction this fails in.
INVOKES = re.compile(r"(?:^|[\s;&|(])(?:[\w./~-]*/)?neo-(?:send|mail)(?=\s|$)")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?^\1[ \t]*$", re.S | re.M)
COMMENT = re.compile(r"(?:^|\s)#[^\n]*")


def invocation(command: str) -> str | None:
    """The command with heredoc bodies and comments removed, if it calls a sender; else None.

    A sender counts only in COMMAND POSITION — the first word of a pipeline segment — found by
    tokenising, so a mention inside a quoted string (a commit message, an `echo`) is one token and
    never a command. The regex version refused a `git commit -m` whose message named the tool.
    Unparseable input falls back to the regex, which errs toward refusal.
    """
    bare = COMMENT.sub("", HEREDOC.sub("", command))
    try:
        lex = shlex.shlex(bare, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return bare if INVOKES.search(bare) else None
    at_start = True
    for t in toks:
        if t and set(t) <= set(";&|()"):
            at_start = True
            continue
        if at_start:
            if re.search(r"(?:^|/)neo-(?:send|mail)$", t):
                return bare
            # Leading env assignments and wrappers keep the next word in command position.
            at_start = bool(re.match(r"^\w+=", t)) or t in ("env", "time", "exec", "nohup")
    return None


def deny(reason: str) -> None:
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout)
    sys.exit(0)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def quote_of(command: str) -> str | None:
    try:
        toks = shlex.split(command)
    except ValueError:
        return None
    for i, t in enumerate(toks):
        if t == "--quote" and i + 1 < len(toks):
            return toks[i + 1]
        if t.startswith("--quote="):
            return t.split("=", 1)[1]
    return None


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts)
    return ""


def last_human_message(lines: list[str]) -> str | None:
    """The operator's newest typed message: a `user` entry that is text, not a tool result or a reminder."""
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "user" or entry.get("isMeta"):
            continue
        msg = entry.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            continue
        text = _text(content)
        stripped = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S).strip()
        if stripped:
            return stripped
    return None


def decide(command: str, transcript_lines: list[str] | None) -> str | None:
    """None to allow, else the reason for refusing."""
    bare = invocation(command)
    if bare is None:
        return None
    command = bare
    if "--approve" in command and "--commit" in command:
        return ("approve and commit in one command is refused: approval is recorded, shown, "
                "and only then committed, as separate steps.")
    if "--approve" not in command:
        return None
    quote = quote_of(command)
    if not quote or len(norm(quote)) < 2:
        return "--approve needs --quote with the operator's words, verbatim."
    if transcript_lines is None:
        return "cannot read the transcript to check the approval quote; refusing (fail-closed)."
    latest = last_human_message(transcript_lines)
    if latest is None:
        return "found no message from the operator in the transcript; refusing."
    if norm(quote) not in norm(latest):
        return (f"the --quote {quote!r} is not in the operator's most recent message. Approval is "
                f"their words, from the turn after they saw the preview — show him the preview and ask.")
    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        deny("send-approval guard could not parse its input; refusing (fail-closed).")
    if payload.get("tool_name") != "Bash":
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command") or ""
    if invocation(command) is None:
        sys.exit(0)
    lines: list[str] | None
    try:
        with open(payload.get("transcript_path") or "", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = None
    reason = decide(command, lines)
    if reason:
        deny(reason)
    sys.exit(0)


def self_test() -> int:
    def u(text: str) -> str:
        return json.dumps({"type": "user", "message": {"role": "user", "content": text}})

    def tool() -> str:
        return json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "send it"}]}})

    t = [u("draft it"), u("looks good, send it"), tool()]
    cases = [
        ("neo-mail reply-all --approve --quote 'send it' m.txt", t, False),
        ("neo-mail reply-all --approve --quote 'send  it' m.txt", t, False),
        ("neo-mail reply-all --approve --quote 'approved' m.txt", t, True),
        ("neo-send --chat X --approve --quote 'draft it' m.txt", t, True),  # older turn
        ("neo-send --chat X --approve m.txt", t, True),
        ("neo-send --chat X --approve --quote 'send it' m.txt && neo-send --commit m.txt", t, True),
        ("neo-send --chat X --approve --quote 'send it' m.txt", None, True),
        ("neo-send --chat X --commit m.txt", t, False),
        ("ls", t, False),
        ("sensor/.venv/bin/neo-mail reply-all --approve --quote 'nope' m.txt", t, True),
        ("python3 - <<'EOF'\nx = 'neo-mail --approve --commit'\nEOF\n", t, False),
        ("grep -n neo-mail CLAUDE.md # --approve --commit", t, False),
        ("git commit -m 'admit Outlook (neo-mail): --approve needs a quote'", t, False),
        ("cd /x && sensor/.venv/bin/neo-mail reply-all --approve --quote 'nope' m", t, True),
        ("FOO=1 neo-send --chat X --approve --quote 'send it' m.txt", t, False),
    ]
    bad = 0
    for cmd, lines, should_deny in cases:
        got = decide(cmd, lines) is not None
        if got != should_deny:
            bad += 1
            print(f"FAIL: {cmd!r} deny={got}, expected {should_deny}")
    print("self-test:", "ok" if not bad else f"{bad} failure(s)", f"({len(cases)} cases)")
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(self_test())
    main()
