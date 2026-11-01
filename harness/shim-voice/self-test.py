#!/usr/bin/env python3
"""Proof that the voice-build shim actually withholds. Run it before every voice-build session.

A clamp nobody has watched fail is a clamp you are trusting rather than one you have measured,
and this project's whole history is gates that reported themselves clean while leaking. The
expensive case is not the shim erroring — that is loud. It is the shim running, printing
nothing unusual, and filtering zero rows because a body was truncated, decorated or reshaped
somewhere between the store and stdout.

So each check below is written to fail if the filter has gone inert.

    python3 harness/shim-voice/self-test.py

No bodies are printed. The test reads them — it has to, to prove one was removed — and reports
only hashes, lengths and counts.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE.parent / "heldout" / "MANIFEST.json"
SHIM = HERE / "neo-corpus"
REAL = os.environ.get("NEO_CORPUS_BIN", os.path.expanduser("~/.local/bin/neo-corpus"))

FULL = ["--json", "--per-channel", "1000000", "--min-length", "0"]

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def run(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.pop("NEO_VOICE_HELDOUT", None)
    if env:
        e.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, env=e)


def main() -> int:
    if not MANIFEST.exists():
        sys.exit("self-test: no manifest. Run harness/heldout/carve.py first.")
    held = {e["body_sha256"] for e in json.loads(MANIFEST.read_text())["entries"]}
    ENV = {"NEO_VOICE_HELDOUT": str(MANIFEST)}
    print(f"manifest: {len(held)} bodies held out\n")

    print("refusals — the shim must not run without a manifest, or emit prose as text")
    r = run([str(SHIM), "outbound", "--json"])
    check(r.returncode == 78 and "NEO_VOICE_HELDOUT is unset" in r.stderr,
          "refuses to run with NEO_VOICE_HELDOUT unset")
    r = run([str(SHIM), "outbound"], ENV)
    check(r.returncode == 78 and "only serves it as JSON" in r.stderr,
          "refuses `outbound` without --json")
    for verb in ("thread", "recent", "search", "mail"):
        r = run([str(SHIM), verb, "x"], ENV)
        check(r.returncode == 78 and "only serves it as JSON" in r.stderr,
              f"refuses `{verb}` without --json")

    print("\npass-through — metadata verbs must be untouched")
    for verb in ("stats", "chats", "people", "timeline"):
        a = run([REAL, verb], ENV).stdout
        b = run([str(SHIM), verb], ENV).stdout
        check(a == b and len(a) > 0, f"`{verb}` output identical to the real command")

    print("\nthe filter itself")
    real_rows = json.loads(run([REAL, "outbound", *FULL], ENV).stdout)
    shim_out = run([str(SHIM), "outbound", *FULL], ENV)
    shim_rows = json.loads(shim_out.stdout)
    present = sum(1 for r in real_rows if sha(r["text"]) in held)
    found = {sha(r["text"]) for r in real_rows if sha(r["text"]) in held}
    check(found == held,
          f"every one of the {len(held)} held-out bodies is reachable through the real "
          f"command (found {len(found)}) — otherwise this test proves nothing")
    # More rows than hashes means he sent the same text twice. Dropping both copies is right:
    # a twin left in the population is the held-out message, readable under another date.
    check(len(real_rows) - len(shim_rows) == present,
          f"shim removed exactly {present} rows ({len(real_rows)} -> {len(shim_rows)}); "
          f"{present - len(held)} of them are duplicate bodies of a held-out message")
    check(not any(sha(r["text"]) in held for r in shim_rows),
          "no held-out body survives in the shim's output")
    check(f"{present} row(s) withheld" in shim_out.stderr,
          "shim says on stderr how many rows it withheld")

    print("\n--preview must not defeat the hash (truncated bodies hash to something else)")
    pv = run([str(SHIM), "outbound", *FULL, "--preview", "40"], ENV)
    pv_rows = json.loads(pv.stdout)
    check(len(pv_rows) == len(shim_rows),
          f"same {len(shim_rows)} rows survive with --preview 40 as without")
    check(all(len(r["text"]) <= 42 for r in pv_rows),
          "--preview 40 was still applied to what remains")
    # Compare against the truncation applied to the FULL bodies, not to the real command's
    # already-previewed output: hashing a truncated body tells you nothing about whether it
    # was held out, which made the first version of this check vacuous.
    def preview(t: str, n: int) -> str:
        return t[:n].rstrip() + " …" if len(t) > n else t
    expected = sorted(preview(r["text"], 40) for r in real_rows if sha(r["text"]) not in held)
    check(sorted(r["text"] for r in pv_rows) == expected,
          "every surviving previewed body is byte-identical to the real command's truncation")

    print("\n«guillemets» must not defeat the hash (search decorates every match)")
    # Take a held-out row, pick a distinctive word from it, and search for that word. The row
    # must not come back. Its body is read here and never printed.
    victim = next(r for r in real_rows if sha(r["text"]) in held and r["channel"] == "telegram")
    words = [w for w in re.findall(r"[A-Za-z]{7,}", victim["text"])]
    if not words:
        check(False, "could not find a searchable token in a held-out row (test inconclusive)")
    else:
        token = max(words, key=len)
        raw = run([REAL, "search", token, "--json", "--limit", "200", "--full"], ENV).stdout
        raw_rows = json.loads(raw[raw.find("["):])
        hit = any(sha(r["text"].replace("«", "").replace("»", "")) in held for r in raw_rows)
        check(hit, f"the real `search {token} --full` does return a held-out row — "
                   f"otherwise this check proves nothing")
        # Pin the defect the shim works around: by default `search` returns an 18-token
        # snippet, which hashes to nothing in the manifest. If this ever starts matching,
        # `--full` is no longer needed — and if the shim stops forcing it, the filter silently
        # stops filtering, which is the failure mode worth a permanent check.
        snip = run([REAL, "search", token, "--json", "--limit", "200"], ENV).stdout
        snip_rows = json.loads(snip[snip.find("["):])
        check(not any(sha(r["text"].replace("«", "").replace("»", "")) in held
                      for r in snip_rows),
              "a default (snippet) search matches NO held-out hash — which is why the shim "
              "forces --full; without it the filter is inert")
        s = run([str(SHIM), "search", token, "--json", "--limit", "200"], ENV)
        s_rows = json.loads(s.stdout[s.stdout.find("["):])
        check(not any(sha(r["text"].replace("«", "").replace("»", "")) in held for r in s_rows),
              f"the shim's `search {token}` returns none of them")
        # Forcing --full costs the «» highlight markers — they are produced by FTS5's
        # snippet(), which is the thing being replaced. Worth pinning as a known trade-off
        # rather than discovering it mid-session and thinking search had broken.
        check(not any("«" in r.get("text", "") for r in s_rows),
              "surviving rows carry no «» markers — the cost of --full, not a fault")
        check(all(len(r["text"]) >= len(m["text"]) for r, m in
                  zip(sorted(s_rows, key=lambda x: x["date"]),
                      sorted([x for x in snip_rows
                              if sha(x["text"].replace("«", "").replace("»", "")) not in held],
                             key=lambda x: x["date"]))),
              "the shim's search returns whole bodies, not snippets")

    print("\n`thread` carries no chat or channel — body hash must still identify a row")
    chat = victim["chat"]
    t_real = json.loads(run([REAL, "thread", chat, "--json", "--limit", "100000"], ENV).stdout)
    t_shim = json.loads(run([str(SHIM), "thread", chat, "--json", "--limit", "100000"], ENV).stdout)
    n_held = sum(1 for r in t_real if sha(r.get("text", "")) in held)
    check(n_held > 0, f"`thread {chat}` really does contain held-out rows ({n_held})")
    check(len(t_real) - len(t_shim) == n_held,
          f"shim removed exactly those {n_held} from the thread")

    bad = sum(1 for ok, _ in results if not ok)
    print(f"\n{len(results) - bad}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
