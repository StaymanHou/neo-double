#!/usr/bin/env python3
"""Measure how much the judge moves when nothing else does.

Every verdict in this harness is produced by a language model reading a fixed transcript, and
the harness has already watched one gating verdict flip on text that did not change (`S-013`,
7/7 -> 6/7). That flip was attributed to a judge-prompt change, but **instruction-effect and
judge variance were never separated** — so no difference between two runs could be read
honestly, because nobody knew the size of the noise underneath.

This measures the noise. Judge the same stored transcripts N times with everything held
constant — same scenarios, same checks, same ground truth, same model — and diff the verdicts.
Whatever moves, moved for no reason at all.

    ./neo-eval judge --run <id> --tag b        # ...and c, and d. Each writes verdict-<tag>.json
    python3 harness/judge-stability.py --run <id>

`verdict.json` (the run's own, tracked as `SCORES.json`) counts as one sample, labelled `base`.

**The output carries no quotations** — check ids, severities, claims and verdict words only.
Claims already live in `harness/scenarios/`, which git tracks; the judge's evidence, which is
quoted from the held-out message, does not appear here at all. That is what makes this file
committable when `verdict.json` and `REPORT.md` are not.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from neo_eval import RUNS, load_scenarios  # noqa: E402

ORDER = ["pass", "fail", "unclear", "-"]
GLYPH = {"pass": "P", "fail": "F", "unclear": "?", "-": "-"}


def load_samples(run_dir: Path, sid: str) -> dict[str, dict]:
    """Every judging of one scenario, keyed by tag. `verdict.json` is `base`.

    A judging that errored is dropped rather than counted. `_judge_one` writes
    `{"checks": [], "error": ...}` when the judge process fails or returns unparseable
    output, and counting that as a verdict would manufacture a flip out of an API timeout —
    reporting instability that is really just a dropped call.
    """
    out: dict[str, dict] = {}
    for path in sorted((run_dir / sid).glob("verdict*.json")):
        tag = "base" if path.name == "verdict.json" else path.stem[len("verdict-"):]
        data = json.loads(path.read_text())
        if data.get("error") or not data.get("checks"):
            print(f"  ! {sid} sample {tag} errored, dropped: "
                  f"{data.get('error', 'no checks')}", file=sys.stderr)
            continue
        out[tag] = {c["id"]: c["verdict"] for c in data["checks"]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", help="write markdown here (default: <run>/JUDGE-STABILITY.md)")
    # Comparing samples from two different judge models in one pass would report the
    # difference BETWEEN the models as instability WITHIN one — the same arithmetic, a
    # completely different claim. Restricting the sample set is what keeps the two
    # questions apart.
    ap.add_argument("--tags", nargs="*",
                    help="only compare these samples (default: all). `base` is verdict.json")
    args = ap.parse_args()

    run_dir = RUNS / args.run
    if not run_dir.exists():
        sys.exit(f"no such run: {args.run}")
    meta = json.loads((run_dir / "RUN.json").read_text())
    scenarios = {s.id: s for s in load_scenarios(meta["scenarios"])}

    per_scenario: dict[str, dict] = {}
    all_tags: set[str] = set()
    for sid, scenario in scenarios.items():
        samples = load_samples(run_dir, sid)
        if args.tags:
            samples = {t: v for t, v in samples.items() if t in args.tags}
        all_tags |= set(samples)
        mech = json.loads((run_dir / sid / "mechanical.json").read_text())
        # Mechanical checks are code over stored artefacts and cannot move between judgings.
        # They are folded in anyway, because `holds` is defined over both and a scenario that
        # is already void mechanically must not read as "the judge is stable here".
        mech_fail = [k for k, v in mech["checks"].items() if v["verdict"] == "fail"]
        per_scenario[sid] = {"scenario": scenario, "samples": samples, "mech_fail": mech_fail}

    tags = sorted(all_tags, key=lambda t: (t != "base", t))
    if len(tags) < 2:
        sys.exit(f"need at least two judgings to compare; found {tags or 'none'}. "
                 f"Run: ./neo-eval judge --run {args.run} --tag b")

    # A scenario judged once cannot disagree with itself, and counting its checks as
    # "unanimous" would dilute the split rate with scenarios that were never re-judged —
    # reporting the instrument as more stable the less of it you measured.
    thin = [sid for sid, rec in per_scenario.items() if len(rec["samples"]) < 2]
    for sid in thin:
        del per_scenario[sid]
    if not per_scenario:
        sys.exit("no scenario has two judgings to compare")

    # `--model opus` is an alias and RUN.json records only the alias, so the resolved id is
    # recoverable from the judge's own output and nowhere else. A stability figure is about a
    # specific grader; naming it is what stops a future comparison spanning two silently.
    resolved = {}
    for tag in tags:
        for sid in per_scenario:
            raw = run_dir / sid / (f"judge-raw-{tag}.json" if tag != "base" else "judge-raw.json")
            if not raw.exists():
                continue
            try:
                used = json.loads(raw.read_text()).get("modelUsage", {})
            except (json.JSONDecodeError, OSError):
                continue
            # The harness itself bills small amounts to a helper model; the grader is the one
            # that actually generated the verdict, i.e. the one with output tokens.
            names = [m for m, u in used.items() if u.get("outputTokens", 0) > 100]
            if names:
                resolved[tag] = max(names, key=lambda m: used[m]["outputTokens"])
                break

    lines = [
        f"# Judge stability — run `{args.run}`",
        "",
        f"{len(tags)} judgings of the **same stored transcripts**, everything else held "
        "constant: same scenario files, same checks, same ground truth. Nothing about the "
        "graded sessions changed between them, so every difference below is the instrument "
        f"moving, not the subject. (The graded sessions themselves ran on `{meta['model']}` — "
        "that is the subject, not the grader; the grader is named per sample below.)",
        "",
        "Samples: "
        + ", ".join(f"`{t}`" + (f" ({resolved[t]})" if t in resolved else "") for t in tags)
        + " — `base` is the run's own `verdict.json`, the one `SCORES.json` was cut from.",
        "",
    ]
    if len(set(resolved.values())) > 1:
        lines += ["> **These samples span more than one judge model.** Differences below are "
                  "not self-disagreement — they mix model-to-model disagreement with it. "
                  "Restrict to one model with `--tags` to measure stability.", ""]
    if thin:
        lines += [f"> Excluded, judged only once so they cannot disagree with themselves: "
                  f"{', '.join(f'`{s}`' for s in thin)}.", ""]

    # ── headline per sample ──────────────────────────────────────────────────
    holds: dict[str, list[str]] = {t: [] for t in tags}
    for sid, rec in per_scenario.items():
        gating = [c["id"] for c in rec["scenario"].gating()]
        for tag in tags:
            got = rec["samples"].get(tag)
            if got is None:
                continue
            if all(got.get(cid) == "pass" for cid in gating) and not rec["mech_fail"]:
                holds[tag].append(sid)

    # Only a sample that judged every scenario has a headline comparable to the others. A tag
    # that covered one scenario would otherwise post a low count and read as the worst pass in
    # the set, when it simply graded less — a coverage artefact dressed as a result.
    full = [t for t in tags
            if all(t in rec["samples"] for rec in per_scenario.values())]
    partial = [t for t in tags if t not in full]
    if not full:
        sys.exit("no sample covers every scenario; headline would not be comparable")

    counts = [len(holds[t]) for t in full]
    lines += [
        "## The headline, re-judged",
        "",
        "| sample | scenarios held | of |",
        "|---|---|---|",
    ]
    for tag in full:
        lines.append(f"| `{tag}` | **{len(holds[tag])}** | {len(per_scenario)} |")
    if partial:
        lines += ["", "Not in the headline — they did not judge every scenario, so their "
                      "counts are not comparable: "
                      + ", ".join(f"`{t}` ({sum(1 for r in per_scenario.values() if t in r['samples'])}"
                                  f"/{len(per_scenario)} scenarios)" for t in partial)
                  + ". They still count in the per-check agreement below, where each check is "
                    "compared only against the samples that graded it."]
    spread = max(counts) - min(counts)
    lines += [
        "",
        f"**Spread: {spread}** — the headline ranges {min(counts)} to {max(counts)} of "
        f"{len(per_scenario)} with no change to a single draft.",
        "",
        "The spread is a floor, not the whole story: two scenarios flipping in opposite "
        "directions cancel, so a spread of 0 is consistent with a headline made of moving "
        "parts. Read the list below rather than the number above.",
        "",
    ]
    moved = [sid for sid, rec in per_scenario.items()
             if len({sid in holds[t] for t in tags if t in rec["samples"]}) > 1]
    if moved:
        lines += [
            "Scenarios whose **hold/no-hold outcome moved between identical judgings** — these "
            "are the ones a run-to-run comparison cannot distinguish from a real change:",
            "",
        ]
        for sid in moved:
            per = " ".join(f"`{t}`={'hold' if sid in holds[t] else 'no'}"
                           for t in tags if per_scenario[sid]["samples"].get(t))
            lines.append(f"- `{sid}` — {per}")
        lines.append("")

    # ── per-check agreement ──────────────────────────────────────────────────
    split_gating: list[str] = []
    split_should: list[str] = []
    n_checks = n_unanimous = 0
    detail: list[str] = []

    for sid, rec in per_scenario.items():
        scenario = rec["scenario"]
        gating_ids = {c["id"] for c in scenario.gating()}
        rows = []
        for check in scenario.checks:
            cid = check["id"]
            got = [rec["samples"][t].get(cid, "-") for t in tags if t in rec["samples"]]
            n_checks += 1
            distinct = set(got)
            if len(distinct) == 1:
                n_unanimous += 1
                continue
            entry = (f"`{sid}` `{cid}` [{check['severity']}] "
                     + " ".join(f"{t}={GLYPH[v]}" for t, v in
                                zip([t for t in tags if t in rec['samples']], got)))
            (split_gating if cid in gating_ids else split_should).append(entry)
            rows.append((cid, check["severity"], check["claim"], got))
        if rows:
            detail += [f"### `{sid}` — {scenario.title}", ""]
            present = [t for t in tags if t in rec["samples"]]
            detail += [f"| check | sev | {' | '.join(present)} | claim |",
                       "|---|---|" + "---|" * len(present) + "---|"]
            for cid, sev, claim, got in rows:
                cells = " | ".join(GLYPH[v] for v in got)
                detail.append(f"| `{cid}` | {sev} | {cells} | {claim} |")
            detail.append("")

    n_split = n_checks - n_unanimous
    lines += [
        "## Per-check agreement",
        "",
        "| | |",
        "|---|---|",
        f"| checks compared | {n_checks} |",
        f"| unanimous across all samples | {n_unanimous} ({n_unanimous / n_checks:.0%}) |",
        f"| **split** | **{n_split} ({n_split / n_checks:.0%})** |",
        f"| of those, gating (MUST / MUST-NOT) | {len(split_gating)} |",
        f"| of those, SHOULD (reported only) | {len(split_should)} |",
        "",
        "A split check is one the judge scored differently on identical input. `P` pass, "
        "`F` fail, `?` unclear.",
        "",
    ]
    if split_gating:
        lines += ["**Gating checks that moved** — each of these can change whether a scenario "
                  "holds:", ""] + [f"- {e}" for e in split_gating] + [""]
    if split_should:
        lines += ["**SHOULD checks that moved** (reported, do not gate):", ""] \
            + [f"- {e}" for e in split_should] + [""]

    if detail:
        lines += ["## Detail, by scenario", "",
                  "Only checks that moved are listed. A scenario absent from this section was "
                  "scored identically every time.", ""] + detail

    # ── what a difference has to clear to be signal ──────────────────────────
    lines += [
        "## What this means for reading a re-run",
        "",
        f"With {len(tags)} judgings and nothing changed, {n_split} of {n_checks} checks "
        f"disagreed with themselves and the headline moved by {spread}. Any comparison between "
        "two runs has to clear that before it means anything.",
        "",
    ]

    text = "\n".join(lines) + "\n"
    out = Path(args.out) if args.out else run_dir / "JUDGE-STABILITY.md"
    out.write_text(text)
    print(text)
    print(f"written to {out}")


if __name__ == "__main__":
    main()
