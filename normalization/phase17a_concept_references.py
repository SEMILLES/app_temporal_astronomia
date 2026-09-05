"""Backfill original conceptual references on disposable import copies only."""
import argparse
import json
import sqlite3
from pathlib import Path

from activity import record_activity
from normalization.phase15_information_parity import PROTECTED


def normalize(db, apply=False):
    """Preserve existing references; derive missing originals from current IDs."""
    db.execute("SAVEPOINT phase17a_references")
    try:
        rows = db.execute("""SELECT o.occurrence_id, a.assignment_id, al.concept_id
            FROM occurrence o
            LEFT JOIN assignment a ON a.occurrence_id=o.occurrence_id AND a.is_current=1
            LEFT JOIN alternative al ON al.alternative_id=a.alternative_id
            WHERE NOT EXISTS (SELECT 1 FROM occurrence_concept_reference r
                              WHERE r.occurrence_id=o.occurrence_id AND r.is_current=1)
            ORDER BY o.occurrence_id""").fetchall()
        ids = [row[0] for row in rows]
        if len(ids) != len(set(ids)) or any(row[2] is None for row in rows):
            raise ValueError("Each missing reference requires exactly one current assignment and Concept.")
        for occurrence_id, assignment_id, concept_id in rows:
            if db.execute("SELECT 1 FROM occurrence_concept_reference WHERE occurrence_id=?",
                          (occurrence_id,)).fetchone():
                raise ValueError("Historical references without a current version require explicit review.")
            if apply:
                reference_id = db.execute("""INSERT INTO occurrence_concept_reference
                    (occurrence_id,concept_id) VALUES (?,?)""", (occurrence_id, concept_id)).lastrowid
                record_activity(db, "occurrence_concept_reference_backfilled",
                                entity_type="occurrence", entity_id=occurrence_id,
                                access_role="master", comment=json.dumps({
                                    "origin": "phase17a_import_normalization",
                                    "actor": "normalizer", "assignment_id": assignment_id,
                                    "concept_id": concept_id, "reference_id": reference_id}))
        db.execute("RELEASE SAVEPOINT phase17a_references")
        return {"missing": len(rows), "created": len(rows) if apply else 0}
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT phase17a_references")
        db.execute("RELEASE SAVEPOINT phase17a_references")
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    path = args.database.resolve(strict=True)
    if path.name in PROTECTED or "baseline" in path.name or "pre_" in path.name:
        parser.error("Use a disposable copy; protected databases cannot be normalized.")
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)
    db.execute("PRAGMA foreign_keys=ON")
    try:
        print(json.dumps(normalize(db, apply=args.apply)))
    finally:
        db.close()


if __name__ == "__main__":
    main()
