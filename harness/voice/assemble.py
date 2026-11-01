#!/usr/bin/env python3
"""Assemble the reading sample for a voice build, and print its composition.

This is the input `neo-voice` § Gate 6 says to show before spending. It is a separate script
rather than a handful of ad-hoc commands so that the sample is reproducible: the same script on
the same corpus reads the same messages, and what the voice model was built from is a fact
rather than a recollection.

    export NEO_VOICE_HELDOUT=$PWD/harness/heldout/MANIFEST.json
    export PATH=$PWD/harness/shim-voice:$PATH
    python3 harness/voice/assemble.py --out "$CLAUDE_JOB_DIR/tmp/voice-sample"

**It refuses to run unless `neo-corpus` resolves to the voice-build shim.** Assembling this
sample from the real binary would put step 8's held-out messages into the voice model, which is
the one mistake in this whole sequence that cannot be undone afterwards.

Output goes OUTSIDE the repo. A rendered sample is a corpus dump — hundreds of message bodies —
and the rule is that those do not enter git.

THE THREE PARTS, AND WHY THEY ARE SHAPED DIFFERENTLY

  Outlook      whole threads, his own messages in full and everything else clipped.
               `chat_key` is keyed by sender, so every mail he has ever sent sits in one bucket
               named after him — the thread is the ONLY way to see who he was writing to, and
               register without an addressee is unreadable.

  Telegram     his outbound only, grouped by chat. The chat name already identifies the
               correspondent, so the surrounding thread buys much less per token than it does
               on Outlook.

  Each Telegram chat contributes its longest messages AND a hash-sampled set of ordinary ones.
  Long-only would model a register he uses a few hundred times out of seventeen thousand.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

CJK_RATIO = 0.15


def die(msg: str) -> None:
    sys.exit(f"assemble: {msg}")


def corpus(args: list[str]) -> list[dict]:
    p = subprocess.run(["neo-corpus", *args], capture_output=True, text=True)
    if p.returncode != 0:
        die(f"`neo-corpus {' '.join(args)}` failed:\n{p.stderr}")
    if p.stderr:
        sys.stderr.write(p.stderr)
    i = p.stdout.find("[")
    return json.loads(p.stdout[i:]) if i >= 0 else []


def is_zh(t: str) -> bool:
    n = sum(1 for c in t if "一" <= c <= "鿿" or "぀" <= c <= "ヿ")
    return n / max(1, len(t)) >= CJK_RATIO


def hkey(r: dict) -> str:
    raw = "\x00".join((r.get("channel", ""), r.get("date", ""), r.get("chat", ""), r["text"]))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clip(t: str, n: int) -> str:
    t = t.strip()
    return t if len(t) <= n else t[:n].rstrip() + " […]"


def outlook(out: pathlib.Path, context_chars: int) -> tuple[int, int]:
    """Whole conversations that contain at least one message from him."""
    convs = [c for c in corpus(["mail", "--json", "--limit", "100000"]) if c["sent"] > 0]
    convs.sort(key=lambda c: c["first"])
    lines, his, total = [], 0, 0
    lines.append("# Outlook — every thread containing a message he sent\n")
    lines.append(f"His own messages are reproduced in full. Everything else is clipped to "
                 f"{context_chars} characters: it is here to show what he was answering and "
                 f"who was in the room, not to be modelled.\n")
    for c in convs:
        rows = corpus(["mail", "--conversation", c["conversation"], "--json"])
        if not rows:
            continue
        lines.append(f"\n\n---\n\n## {c['subject'] or '(no subject)'}")
        lines.append(f"*{c['messages']} messages · {c['first']} .. {c['last']} · "
                     f"{c['people']} people · {c['sent']} from him*\n")
        for r in rows:
            text = (r.get("text") or "").strip()
            if not text:
                continue
            to = r.get("to") or ""
            try:
                to = ", ".join(json.loads(to)) if to.startswith("[") else to
            except json.JSONDecodeError:
                pass
            total += 1
            if r.get("dir") == "outbound":
                his += 1
                lines.append(f"\n**>>> HIM — {r.get('at','')} → {to}**\n")
                lines.append(text)
            else:
                lines.append(f"\n*{r.get('at','')} — {r.get('from','')} → {to}*\n")
                lines.append(clip(text, context_chars))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return his, total


def telegram(out: pathlib.Path, want_zh: bool, top_chats: int,
             n_long: int, n_mid: int, mid_lo: int, mid_hi: int) -> tuple[int, list[str]]:
    rows = corpus(["outbound", "--json", "--per-channel", "1000000", "--min-length", "0"])
    pool = [r for r in rows if r["channel"] == "telegram" and is_zh(r["text"]) == want_zh]
    by: dict[str, list[dict]] = collections.defaultdict(list)
    for r in pool:
        by[r["chat"]].append(r)
    chats = sorted(by, key=lambda c: (-len(by[c]), c))[:top_chats]

    label = "Mandarin" if want_zh else "English"
    lines = [f"# Telegram — his own messages, {label} register\n",
             f"Top {top_chats} chats by outbound volume. Each contributes its {n_long} longest "
             f"messages and {n_mid} ordinary ones ({mid_lo}–{mid_hi} chars) chosen by content "
             f"hash, so the sample is not only his long-form register.\n"]
    n = 0
    for c in chats:
        rs = by[c]
        picked = sorted(rs, key=lambda r: -len(r["text"]))[:n_long]
        mid = [r for r in rs if mid_lo <= len(r["text"]) < mid_hi]
        picked += sorted(mid, key=hkey)[:n_mid]
        seen, uniq = set(), []
        for r in picked:
            k = hkey(r)
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        uniq.sort(key=lambda r: r["date"])
        lines.append(f"\n\n---\n\n## {c}")
        lines.append(f"*{len(rs)} of his messages in this chat; {len(uniq)} shown*\n")
        for r in uniq:
            n += 1
            lines.append(f"\n**{r['date']}** ({len(r['text'])} chars)\n")
            lines.append(r["text"].strip())
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n, chats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output DIRECTORY, outside the repo")
    ap.add_argument("--context-chars", type=int, default=300)
    ap.add_argument("--en-chats", type=int, default=30)
    ap.add_argument("--zh-chats", type=int, default=15)
    ap.add_argument("--per-chat-long", type=int, default=6)
    ap.add_argument("--per-chat-mid", type=int, default=6)
    a = ap.parse_args()

    resolved = shutil.which("neo-corpus") or ""
    if "shim-voice" not in resolved:
        die(f"`neo-corpus` resolves to {resolved or '(nothing)'}, not the voice-build shim.\n"
            f"Assembling this sample from the real binary would read step 8's held-out "
            f"messages into the voice model — the one mistake here that cannot be undone.\n"
            f"  export NEO_VOICE_HELDOUT=$PWD/harness/heldout/MANIFEST.json\n"
            f"  export PATH=$PWD/harness/shim-voice:$PATH")
    if not os.environ.get("NEO_VOICE_HELDOUT"):
        die("NEO_VOICE_HELDOUT is unset.")

    out = pathlib.Path(a.out).expanduser().resolve()
    repo = pathlib.Path(__file__).resolve().parents[2]
    if repo in out.parents or out == repo:
        die(f"--out is inside the repo ({out}). A rendered sample is a corpus dump; it does "
            f"not enter git. Write it to $CLAUDE_JOB_DIR/tmp or similar.")
    out.mkdir(parents=True, exist_ok=True)

    print("assembling — held-out rows are removed by the shim, which reports on stderr\n")
    his, total = outlook(out / "01-outlook.md", a.context_chars)
    en, en_chats = telegram(out / "02-telegram-en.md", False, a.en_chats,
                            a.per_chat_long, a.per_chat_mid, 40, 200)
    zh, zh_chats = telegram(out / "03-telegram-zh.md", True, a.zh_chats,
                            a.per_chat_long, a.per_chat_mid, 20, 120)

    print("\nSAMPLE COMPOSITION\n")
    rows = [("Outlook threads", f"{his} his / {total} total", out / "01-outlook.md"),
            (f"Telegram English ({len(en_chats)} chats)", f"{en} his", out / "02-telegram-en.md"),
            (f"Telegram Mandarin ({len(zh_chats)} chats)", f"{zh} his", out / "03-telegram-zh.md")]
    grand = 0
    for label, count, path in rows:
        size = path.stat().st_size
        grand += size
        print(f"  {label:<34} {count:<18} {size:>9,} bytes  ~{size//4:>7,} tok")
    print(f"  {'TOTAL':<34} {'':<18} {grand:>9,} bytes  ~{grand//4:>7,} tok")
    print(f"\n  written to {out}")
    print(f"  his own messages in the sample: {his + en + zh}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
