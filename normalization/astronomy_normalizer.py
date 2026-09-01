import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from alternative_morphology import create_or_replace_alternative_morphology
from catalog_projection import build_catalog_projection
from occurrence_grammar import create_or_replace_occurrence_grammar


GENERIC_GRAMMAR = {
    "gender": "SIN-MARCA", "plural": "SIN-MARCA", "agentive": "N/A",
    "conjugated_form": "NO", "negation": "SIN-NEG",
}
GRAMMAR_FIELDS = tuple(GENERIC_GRAMMAR)
COUNT_TABLES = (
    "source", "concept", "alternative", "occurrence", "assignment",
    "occurrence_grammar", "alternative_morphology", "alternative_component",
    "alternative_relation", "submission", "occurrence_draft", "conflict",
)


class NormalizationError(RuntimeError):
    pass


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _count(db, table, where=""):
    if table not in _tables(db):
        return 0
    return db.execute(f"SELECT count(*) FROM {table} {where}").fetchone()[0]


def database_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_config(path):
    with Path(path).open(encoding="utf-8") as stream:
        config = json.load(stream)
    if not isinstance(config.get("sources", []), list):
        raise NormalizationError("sources debe ser una lista")
    return config


def _source_plan(db, config):
    plan = []
    for rule in config.get("sources", []):
        match = rule.get("match", {})
        allowed = {"source_id", "source_name", "legacy_source_code"}
        if not match or set(match) - allowed:
            raise NormalizationError("Cada source requiere un match exacto permitido")
        where = " AND ".join(f"{key}=?" for key in match)
        rows = db.execute(f"SELECT * FROM source WHERE {where}", tuple(match.values())).fetchall()
        if not rows and rule.get("rename") and "source_name" in match:
            rematch = dict(match); rematch["source_name"] = rule["rename"]
            retry_where = " AND ".join(f"{key}=?" for key in rematch)
            rows = db.execute(f"SELECT * FROM source WHERE {retry_where}", tuple(rematch.values())).fetchall()
        if len(rows) != 1:
            raise NormalizationError(
                f"El match privado de source debe resolver exactamente una fila; resolvio {len(rows)}"
            )
        row = rows[0]
        desired = {
            "source_name": rule.get("rename", row["source_name"]),
            "start_year": rule.get("year", rule.get("start_year", row["start_year"])),
            "end_year": rule.get("year", rule.get("end_year", row["end_year"])),
            "end_year_status": rule.get("end_year_status", "known" if "year" in rule else row["end_year_status"]),
        }
        changes = {key: value for key, value in desired.items() if row[key] != value}
        plan.append({
            "source_id": row["source_id"], "matched_name": row["source_name"],
            "occurrence_count": db.execute(
                "SELECT count(*) FROM occurrence WHERE source_id=?", (row["source_id"],)
            ).fetchone()[0],
            "changes": changes,
        })
    return plan


def _grammar_plan(db, config):
    legacy = config.get("legacy_grammar_placeholders", {})
    planned = []
    preserved = {field: 0 for field in GRAMMAR_FIELDS}
    for occurrence in db.execute("SELECT occurrence_id FROM occurrence ORDER BY occurrence_id"):
        row = db.execute(
            "SELECT * FROM occurrence_grammar WHERE occurrence_id=? AND is_current=1",
            (occurrence[0],),
        ).fetchone()
        values = {}
        for field in GRAMMAR_FIELDS:
            old = row[field] if row else None
            placeholders = legacy.get(field, [None])
            if old is None or old in placeholders:
                values[field] = GENERIC_GRAMMAR[field]
            else:
                values[field] = old
                if old != GENERIC_GRAMMAR[field]:
                    preserved[field] += 1
        values["grammar_note"] = row["grammar_note"] if row else None
        current = tuple(row[field] for field in (*GRAMMAR_FIELDS, "grammar_note")) if row else None
        desired = tuple(values[field] for field in (*GRAMMAR_FIELDS, "grammar_note"))
        if current != desired:
            planned.append({"occurrence_id": occurrence[0], "values": values})
    return planned, preserved


def _morphology_plan(db):
    return [r[0] for r in db.execute("""
        SELECT a.alternative_id FROM alternative a
        LEFT JOIN alternative_morphology m
          ON m.alternative_id=a.alternative_id AND m.is_current=1
        WHERE a.retired_at IS NULL AND m.alternative_morphology_id IS NULL
        ORDER BY a.alternative_id
    """)]


def audit_database(db):
    tables = _tables(db)
    counts = {table: _count(db, table) for table in COUNT_TABLES}
    counts.update({
        "assignment_current": _count(db, "assignment", "WHERE is_current=1"),
        "grammar_current": _count(db, "occurrence_grammar", "WHERE is_current=1"),
        "grammar_historical": _count(db, "occurrence_grammar", "WHERE is_current=0"),
        "morphology_current": _count(db, "alternative_morphology", "WHERE is_current=1"),
        "morphology_historical": _count(db, "alternative_morphology", "WHERE is_current=0"),
        "relations_current": _count(db, "alternative_relation", "WHERE is_current=1"),
        "catalog_video_current": _count(db, "alternative_media", "WHERE role='catalog_video' AND is_current=1"),
        "pending_submissions": _count(db, "submission", "WHERE status='pending'"),
        "open_conflicts": _count(db, "conflict", "WHERE status='open'"),
        "occurrence_year_present": _count(db, "occurrence", "WHERE occurrence_year IS NOT NULL"),
    })
    missing_dates = []
    for row in db.execute("""
        SELECT s.source_id,s.source_name,s.start_year,s.end_year,count(o.occurrence_id)
        FROM source s LEFT JOIN occurrence o ON o.source_id=s.source_id
        WHERE s.start_year IS NULL OR s.end_year IS NULL
        GROUP BY s.source_id ORDER BY s.source_id
    """):
        missing_dates.append({"source_id": row[0], "source_name": row[1],
                              "start_year": row[2], "end_year": row[3],
                              "occurrence_count": row[4],
                              "classification": "without_occurrences" if row[4] == 0 else "with_occurrences"})
    distributions = {}
    if "occurrence_grammar" in tables:
        for field in GRAMMAR_FIELDS:
            distributions[field] = {str(r[0]): r[1] for r in db.execute(
                f"SELECT {field},count(*) FROM occurrence_grammar WHERE is_current=1 GROUP BY {field}"
            )}
    return {"counts": counts, "sources_missing_dates": missing_dates,
            "grammar_distribution": distributions,
            "foreign_key_violations": [tuple(r) for r in db.execute("PRAGMA foreign_key_check")]}


def normalize(db, config, *, apply=False):
    required = {"alternative_morphology", "alternative_component"}
    missing = required - _tables(db)
    if missing:
        raise NormalizationError("Faltan migrations requeridas: " + ", ".join(sorted(missing)))
    sources = _source_plan(db, config)
    grammars, preserved = _grammar_plan(db, config)
    morphologies = _morphology_plan(db)
    before_years = [tuple(r) for r in db.execute("SELECT occurrence_id,occurrence_year FROM occurrence ORDER BY occurrence_id")]
    result = {
        "mode": "apply" if apply else "dry-run",
        "source_changes": [item for item in sources if item["changes"]],
        "indirectly_affected_occurrences": sum(item["occurrence_count"] for item in sources if item["changes"]),
        "grammar_versions_to_create": len(grammars),
        "grammar_explicit_values_preserved": preserved,
        "morphology_versions_to_create": len(morphologies),
        "morphology_explicit_preserved": _count(db, "alternative_morphology", "WHERE is_current=1"),
        "occurrence_year_modified": 0, "videos_modified": 0,
    }
    if not apply:
        return result
    try:
        db.execute("BEGIN IMMEDIATE")
        for item in sources:
            if not item["changes"]:
                continue
            source_id = item["source_id"]
            db.execute("""
                INSERT INTO source_revision(source_id,source_name,source_type,source_reference,
                start_year,end_year,end_year_status,legacy_source_code,source_scope,format_original,
                format_detail,region_description,characterization,reported_entry_count,changed_by,change_note)
                SELECT source_id,source_name,source_type,source_reference,start_year,end_year,end_year_status,
                legacy_source_code,source_scope,format_original,format_detail,region_description,
                characterization,reported_entry_count,NULL,'Legacy administrative normalization'
                FROM source WHERE source_id=?
            """, (source_id,))
            columns = list(item["changes"])
            db.execute("UPDATE source SET " + ",".join(f"{c}=?" for c in columns) +
                       ",updated_at=CURRENT_TIMESTAMP,updated_by=NULL WHERE source_id=?",
                       (*[item["changes"][c] for c in columns], source_id))
        for item in grammars:
            create_or_replace_occurrence_grammar(
                db, item["occurrence_id"], **item["values"],
                change_note="Legacy administrative normalization",
                created_by=None, created_from_submission_id=None,
            )
        for alternative_id in morphologies:
            create_or_replace_alternative_morphology(
                db, alternative_id, component_count_not_applicable=True,
                free_permutation="N/A", components=(), created_by=None,
                created_from_submission_id=None,
            )
        if before_years != [tuple(r) for r in db.execute("SELECT occurrence_id,occurrence_year FROM occurrence ORDER BY occurrence_id")]:
            raise NormalizationError("La normalizacion modifico occurrence_year")
        violations = db.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise NormalizationError(f"Foreign keys invalidas: {violations!r}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


def _safe_database(path, allow_protected=False):
    resolved = Path(path).expanduser().resolve()
    protected = {"lesico_prototipo.db", "lesico_astronomia_candidate.db", "lesico_astronomia_write_test.db"}
    if resolved.name.lower() in protected and not allow_protected:
        raise NormalizationError(f"Base protegida; use una copia temporal: {resolved.name}")
    if not resolved.is_file():
        raise NormalizationError(f"No existe la base: {resolved}")
    return resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generic private-data normalization workflow")
    parser.add_argument("--database", default=os.environ.get("LESICO_DATABASE_PATH"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-protected-database", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    if not args.database:
        parser.error("--database o LESICO_DATABASE_PATH es obligatorio")
    database = _safe_database(args.database, args.allow_protected_database)
    before_hash = database_hash(database)
    db = sqlite3.connect(database); db.row_factory = sqlite3.Row; db.execute("PRAGMA foreign_keys=ON")
    try:
        result = normalize(db, load_config(args.config), apply=args.apply)
        result["audit"] = audit_database(db)
        projection = build_catalog_projection(db)
        result["projection_serializable"] = json.loads(json.dumps(projection, ensure_ascii=False)) == projection
    finally:
        db.close()
    result["database_hash_before"] = before_hash
    result["database_hash_after"] = database_hash(database)
    if not args.apply and result["database_hash_before"] != result["database_hash_after"]:
        raise NormalizationError("Dry-run modifico la base")
    output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
