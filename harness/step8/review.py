#!/usr/bin/env python3
"""Put every draft next to the message he actually sent, in one file, for reading by eye.

    python3 harness/step8/review.py --run 2026-08-17-draft-1
    open harness/runs/2026-08-17-draft-1/REVIEW.md

`report` answers *"which checks passed"*. This answers *"what did it actually write"* — which is the
question the operator asks, and the one the harness exists to serve rather than replace. Every defect
this project has caught was caught by a human looking at one specific thing.

For each scenario: the prompt as typed, the real message in full, each candidate's message body with
the scaffolding stripped, and the verdicts with the judge's named divergences underneath.

**REVIEW.md is gitignored, for the same reason `REPORT.md` and the transcripts are:** it holds the
held-out messages verbatim, which is the answer key for every future run. `leak-scan.py` covers the
mistake of un-ignoring it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import neo_eval as N  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args()

    run_dir = N.RUNS / a.run
    if not run_dir.exists():
        sys.exit(f"review: no such run: {a.run}")
    meta = json.loads((run_dir / "RUN.json").read_text())
    scenarios = {s.id: s for s in N.load_scenarios(meta["scenarios"])}

    out = [
        f"# Drafts as written — run `{a.run}`",
        "",
        "**Not committed, and not to be.** This file holds the held-out messages verbatim.",
        "",
        f"Fixture `{meta['fixture']}` · model {meta['model']} · started {meta['started']}",
        "",
        "For each scenario: what he was asked, what he actually sent, and what came back. The",
        "candidates are shown with their titles and notes stripped — the message only.",
        "",
        "---",
        "",
    ]

    for sid, scenario in scenarios.items():
        sdir = run_dir / sid
        gt = N.GroundTruth.for_scenario(sid) if scenario.suite == "draft" else None
        verdict = N._load(sdir / "verdict.json") or {"checks": []}
        mech = N._load(sdir / "mechanical.json") or {"checks": {}}
        byid = {c["id"]: c for c in verdict.get("checks", [])}
        gating = scenario.gating()
        g_pass = sum(1 for c in gating if byid.get(c["id"], {}).get("verdict") == "pass")
        m_fail = [k for k, v in mech["checks"].items() if v["verdict"] == "fail"]
        holds = g_pass == len(gating) and not m_fail and gating

        out += [f"## `{sid}` — {scenario.title}", "",
                f"**{'HELD' if holds else 'did not hold'}** · gating {g_pass}/{len(gating)}"
                + (f" · mechanical: {', '.join(m_fail)}" if m_fail else "") + "",
                ""]
        if gt:
            out += [f"{gt.meta['channel']} · to `{gt.meta['chat']}` · sent {gt.sent_at} · "
                    f"{gt.meta['length']} characters · "
                    f"{'Mandarin' if gt.lang == 'zh' else 'English'}"
                    + (f" · subject *{gt.meta['subject']}*" if gt.meta.get("subject") else ""),
                    ""]
        out += ["### What he asked for", "", "> " + scenario.prompt.replace("\n", "\n> "), ""]
        if gt:
            out += ["### What he actually sent", "", "```", gt.body, "```", ""]

        bodies = N._draft_bodies([], sdir)
        if bodies == [("final reply", "")]:
            events = N._read_stream(sdir / "stream.jsonl") if (sdir / "stream.jsonl").exists() \
                else []
            bodies = N._draft_bodies(events, sdir)
        out += ["### What it wrote", ""]
        for label, body in bodies:
            name = pathlib.Path(label).name if label != "final reply" else label
            out += [f"**{name}** — {len(body)} characters", "", "```", body.strip() or "(empty)",
                    "```", ""]

        fails = [c for c in verdict.get("checks", []) if c["verdict"] != "pass"]
        claims = {c["id"]: c for c in scenario.checks}
        if fails:
            out += ["### Where the grader marked it down", ""]
            for c in fails:
                claim = claims.get(c["id"], {})
                out += [f"- **{c['verdict'].upper()}** `{c['id']}` "
                        f"[{claim.get('severity', '?')}] {claim.get('claim', '')}"]
                if c.get("note"):
                    out += [f"  - {c['note'].strip()}"]
            out += [""]
        divs = [c for c in verdict.get("checks", [])
                if "diverg" in (c.get("note") or "").lower() and c["verdict"] == "pass"]
        if divs:
            out += ["### Divergences named inside a pass", ""]
            for c in divs:
                out += [f"- `{c['id']}` — {c['note'].strip()}"]
            out += [""]
        out += ["---", ""]

    dest = run_dir / "REVIEW.md"
    dest.write_text("\n".join(out) + "\n")
    print(f"wrote {dest.relative_to(N.REPO)}  ({len(scenarios)} scenarios, "
          f"{dest.stat().st_size // 1024}KB)")
    print(f"open {dest.relative_to(N.REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
