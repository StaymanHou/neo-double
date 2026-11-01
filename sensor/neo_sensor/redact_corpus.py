"""Redaction-at-rest: fill `events.body_redacted` so the raw body never reaches a read path.

Why at write time rather than read time
---------------------------------------
The old architecture gated egress at a single function (`core/router.py`), and the gate turned
out to be **decorative**: `RedactedText.credentials_redacted` had zero consumers and all three
dispatch sites hardcoded it, so real AWS credential pairs went out verbatim while the flag read
True (SURFACE-2026-07-25 / "F26").

There is no router any more — the agent reads through `neo-corpus`. Rather than re-create a
gate that a future call site can forget to use, redaction runs **once, at write time**, into a
separate column. The read path selects `body_redacted` and physically cannot name `body`:

    events.body           raw. behind the fence. for the operator only.
    events.body_redacted  the ONLY column any read path may emit.
    events.redacted_at    NULL => not yet processed => the row is not emittable.

That makes "the filter ran" a schema property instead of a caller promise. It also keeps a ~2s
spaCy model load out of every interactive query.

Honest limit, unchanged from the ported module: the guarantee is *the filter ran*, not *no
credential remains*. An unrecognised credential shape still survives. Adding recognisers
narrows the gap; it does not close the class.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from neo_sensor.config import STATE_DIR

DEFAULT_DB = STATE_DIR / "corpus.db"

MIGRATION = """
ALTER TABLE events ADD COLUMN body_redacted TEXT;
ALTER TABLE events ADD COLUMN redaction_meta TEXT;
ALTER TABLE events ADD COLUMN redacted_at TEXT;
"""


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    if "body_redacted" in cols:
        return
    conn.executescript(MIGRATION)
    conn.commit()
    print("added body_redacted / redaction_meta / redacted_at columns")


def rebuild_fts_over_redacted(conn: sqlite3.Connection) -> None:
    """Point FTS at `body_redacted`.

    Indexing `body` would let a search snippet leak exactly what redaction removed — the
    search path is a read path like any other.
    """
    conn.executescript(
        """
        DROP TABLE IF EXISTS events_fts;
        CREATE VIRTUAL TABLE events_fts USING fts5(
            body_redacted, content='events', content_rowid='rowid', tokenize='unicode61');
        """
    )
    conn.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild')")
    conn.commit()
    print("rebuilt FTS5 over body_redacted")


def run(db: Path, sample: int | None, commit: bool, redo: bool = False) -> int:
    from neo_sensor.redaction import redact  # deferred: ~2s spaCy load

    conn = sqlite3.connect(db)
    try:
        ensure_columns(conn)

        # `redacted_at IS NULL` is the right filter for incremental ingest, and the WRONG one
        # after a recogniser is added: every row is already stamped, so a re-run silently does
        # nothing and the newly-recognised shape stays in `body_redacted` for good. `--redo`
        # reprocesses every row against the current recogniser set. Adding a recogniser without
        # it protects future writes only (SURFACE-2026-08-14, the CREDENTIAL_PAIR leak).
        where = "body <> ''" if redo else "redacted_at IS NULL AND body <> ''"
        # S608: `where` is one of two literals chosen above, never caller input.
        sql = f"SELECT rowid, event_id, body FROM events WHERE {where}"  # noqa: S608
        if sample:
            # Deterministic spread rather than the first N rows, which would all be 2024.
            sql += f" ORDER BY (rowid * 7919) % 104729 LIMIT {sample}"
        rows = conn.execute(sql).fetchall()
        print(f"{len(rows):,} rows to process{' (SAMPLE — no writes)' if not commit else ''}\n")

        hits: Counter[str] = Counter()
        modified = 0
        examples: list[tuple[str, str]] = []

        for i, (rowid, _event_id, body) in enumerate(rows, 1):
            r = redact(body)
            if r.text != body:
                modified += 1
                for item in r.redaction_metadata:
                    hits[item.entity_type] += 1
                if len(examples) < 12:
                    examples.append((body, r.text))
            if commit:
                conn.execute(
                    "UPDATE events SET body_redacted=?, redaction_meta=?, "
                    "redacted_at=datetime('now') WHERE rowid=?",
                    (
                        r.text,
                        json.dumps([m.model_dump() for m in r.redaction_metadata]) or None,
                        rowid,
                    ),
                )
                if i % 2000 == 0:
                    conn.commit()
                    print(f"  … {i:,}/{len(rows):,}")

        if commit:
            # Empty bodies still need to be emittable.
            empty_where = "body = ''" if redo else "redacted_at IS NULL AND body = ''"
            conn.execute(
                # S608: `empty_where` is one of two literals chosen above, never caller input.
                "UPDATE events SET body_redacted='', redaction_meta='[]', "  # noqa: S608
                f"redacted_at=datetime('now') WHERE {empty_where}"
            )
            conn.commit()

        pct = 100.0 * modified / len(rows) if rows else 0.0
        print(f"\nmodified {modified:,} of {len(rows):,} rows ({pct:.2f}%)")
        if hits:
            print("entity hits:")
            for ent, n in hits.most_common():
                print(f"  {ent:<24} {n:>6,}")

        if not commit and examples:
            print("\n=== eyeball: before -> after (truncated) ===")
            for before, after in examples:
                print(f"\n  RAW: {before[:200]!r}")
                print(f"  RED: {after[:200]!r}")

        if commit:
            rebuild_fts_over_redacted(conn)
            unredacted = conn.execute(
                "SELECT count(*) FROM events WHERE redacted_at IS NULL"
            ).fetchone()[0]
            print(f"\nrows still unredacted (must be 0): {unredacted}")
            if unredacted:
                return 1
    finally:
        conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--sample", type=int, help="dry-run over N rows, write nothing")
    ap.add_argument("--commit", action="store_true", help="write body_redacted for every row")
    ap.add_argument(
        "--redo",
        action="store_true",
        help="reprocess ALL rows, not just unredacted ones — required after adding a recogniser",
    )
    args = ap.parse_args()
    if not args.sample and not args.commit:
        print("pass --sample N to eyeball first, or --commit to write", file=sys.stderr)
        return 2
    return run(args.db, args.sample, args.commit, args.redo)


if __name__ == "__main__":
    raise SystemExit(main())
