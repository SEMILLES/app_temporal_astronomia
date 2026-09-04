"""Reproducible Phase 15 parity normalization; run only on a disposable DB copy."""
import argparse
import json
import sqlite3
from pathlib import Path

from alternative_morphology import create_or_replace_alternative_morphology
from normalization.astronomy_normalizer import audit_database, database_hash

PROTECTED = {
    "lesico_astronomia_candidate.db", "lesico_astronomia_working.db",
    "lesico_astronomia_working_baseline_2026-09-01.db",
    "lesico_astronomia_write_test.db", "lesico_prototipo.db",
}

SOURCE_RULES = {
    40: {"source_name": "Ferney Maldonado", "source_reference": "https://www.youtube.com/watch?v=nRa2Tt6o2ys",
         "detail_1": "Sistema de solar"},
    41: {"source_name": "edwin anderson caviedes rodriguez", "source_reference": "https://youtu.be/X4U9qjFK8os",
         "detail_1": "LSC 8 las planetas"},
    # RP3 is intentionally absent: it remains provisional and unchanged.
    43: {"source_name": "Bryan Mantilla", "source_reference": "https://www.youtube.com/watch?v=L3MuFjzGIfE",
         "detail_1": "Lengua de Señas Colombiana: 30 palabras de astronomia y astrofotografia"},
}

MORPHOLOGY = {
    "ASTERISMO-1a": (2, "ESTRELLA-1a"), "ASTERISMO-3a": (2, None),
    "ASTRONOMÍA-2a": (2, None), "ASTRONOMÍA-2b": (2, None),
    "ASTRONOMÍA-INDÍGENA-1a": (2, "ASTRONOMÍA-CULTURAL-1a"),
    "ASTRONOMÍA-PREHISPÁNICA-1a": (2, "ASTRONOMÍA-CULTURAL-1a"),
    "CONSTELACIÓN-2a": (2, "UNIVERSO-1b"), "COSMOS-1a": (1, None),
    "COSMOVISIÓN-1a": (2, "COSMOS-1a"), "ECLIPSE-2a": (3, "SOL-1a"),
    "FUERZA-ROZAMIENTO-1a": (2, "FUERZA-1a"),
    "PATRIMONIO-DE-LA-HUMANIDAD-1a": (2, None), "SOL-2a": (2, "SOL-1a"),
    "TIEMPO-1a": (1, None), "VÍA-LÁCTEA-2a": (2, "UNIVERSO-1b"),
}

def _alternative(db, full_label):
    rows = db.execute("""SELECT a.alternative_id FROM alternative a JOIN concept c USING(concept_id)
        WHERE UPPER(c.preferred_label || '-' || a.working_label)=UPPER(?)""", (full_label,)).fetchall()
    if len(rows) != 1: raise RuntimeError(f"Alternative no inequívoca: {full_label} ({len(rows)})")
    return rows[0][0]

def normalize(db, apply=False):
    before = audit_database(db); before_relations = [tuple(r) for r in db.execute(
        "SELECT * FROM alternative_relation ORDER BY alternative_relation_id")]
    plan = {"canonical_details": db.execute("SELECT count(*) FROM occurrence WHERE source_detail_1 IS NULL AND legacy_source_detail_1 IS NOT NULL OR source_detail_2 IS NULL AND legacy_source_detail_2 IS NOT NULL").fetchone()[0],
            "concept_areas": db.execute("SELECT count(*) FROM concept WHERE knowledge_area_1 IS NOT 'ASTRONOMÍA' OR knowledge_area_2 IS NOT NULL").fetchone()[0],
            "sources": [], "morphology": []}
    for source_id, desired in SOURCE_RULES.items():
        row = db.execute("SELECT * FROM source WHERE source_id=?", (source_id,)).fetchone()
        if row is None: raise RuntimeError(f"Falta source_id {source_id}")
        changes = {k: v for k, v in desired.items() if k != "detail_1" and row[k] != v}
        detail_changes = db.execute("SELECT count(*) FROM occurrence WHERE source_id=? AND source_detail_1 IS NOT ?", (source_id, desired["detail_1"])).fetchone()[0]
        plan["sources"].append({"source_id": source_id, "changes": changes, "occurrence_details": detail_changes})
    for label, (count, component) in MORPHOLOGY.items():
        aid = _alternative(db, label); current = db.execute("SELECT * FROM alternative_morphology WHERE alternative_id=? AND is_current=1", (aid,)).fetchone()
        plan["morphology"].append({"alternative_id": aid, "label": label,
            "old": "N/A" if current and current["component_count_not_applicable"] else current["component_count"] if current else None,
            "new": count, "component": component})
    if not apply: return {"mode": "dry-run", "plan": plan, "before": before}
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("""UPDATE occurrence SET source_detail_1=COALESCE(source_detail_1,legacy_source_detail_1),
            source_detail_2=COALESCE(source_detail_2,legacy_source_detail_2)""")
        db.execute("UPDATE concept SET knowledge_area_1='ASTRONOMÍA',knowledge_area_2=NULL")
        for item in plan["sources"]:
            sid=item["source_id"]; desired=SOURCE_RULES[sid]
            if item["changes"]:
                db.execute("""INSERT INTO source_revision(source_id,source_name,source_type,source_reference,start_year,end_year,end_year_status,legacy_source_code,source_scope,format_original,format_detail,region_description,characterization,reported_entry_count,change_note)
                    SELECT source_id,source_name,source_type,source_reference,start_year,end_year,end_year_status,legacy_source_code,source_scope,format_original,format_detail,region_description,characterization,reported_entry_count,'Normalización Fase 15' FROM source WHERE source_id=?""",(sid,))
                cols=list(item["changes"]); db.execute("UPDATE source SET "+",".join(f"{c}=?" for c in cols)+",updated_at=CURRENT_TIMESTAMP WHERE source_id=?", (*[item["changes"][c] for c in cols],sid))
            db.execute("UPDATE occurrence SET source_detail_1=?,source_detail_2=CASE WHEN ?=43 THEN NULL ELSE source_detail_2 END WHERE source_id=?", (desired["detail_1"],sid,sid))
        for item in plan["morphology"]:
            components=[]
            if item["component"]: components=[{"position":1,"component_alternative_id":_alternative(db,item["component"])}]
            create_or_replace_alternative_morphology(db,item["alternative_id"],component_count=item["new"],free_permutation="N/A" if item["new"]==1 else "SIN INFORMACIÓN",components=components,note="Análisis documentado en reconstrucción astronómica Fase 15",created_by=None,created_from_submission_id=None)
        if before_relations != [tuple(r) for r in db.execute("SELECT * FROM alternative_relation ORDER BY alternative_relation_id")]: raise RuntimeError("Se alteraron relaciones")
        if db.execute("PRAGMA foreign_key_check").fetchall(): raise RuntimeError("Foreign keys inválidas")
        db.commit()
    except Exception: db.rollback(); raise
    return {"mode":"apply","plan":plan,"before":before,"after":audit_database(db),"relations_unchanged":True}

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--database",required=True); parser.add_argument("--apply",action="store_true"); parser.add_argument("--report")
    args=parser.parse_args(argv); path=Path(args.database).resolve()
    if path.name.lower() in PROTECTED: raise SystemExit("Base protegida; use una copia desechable")
    before_hash=database_hash(path); db=sqlite3.connect(path); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON")
    try: result=normalize(db,args.apply)
    finally: db.close()
    result.update(database_hash_before=before_hash,database_hash_after=database_hash(path))
    output=json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)
    if args.report: Path(args.report).write_text(output+"\n",encoding="utf-8")
    print(output)

if __name__ == "__main__": main()
