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
    # Stable alternative_id: (documentary reference, component_count, stable component IDs).
    9: ("legacy ASTERISMO-1a; renumber_change 1a -> 2a", 2, (81,)),
    11: ("ASTERISMO-3a; original_code ASTERISMO-2a", 2, ()),
    20: ("ASTRONOMÍA-2a; original_code ASTRONOMÍA-3a", 2, ()),
    21: ("ASTRONOMÍA-2b; original_code ASTRONOMÍA-3b", 2, ()),
    23: ("ASTRONOMÍA-INDÍGENA-1a", 2, (22,)),
    24: ("ASTRONOMÍA-PREHISPÁNICA-1a", 2, (22,)),
    57: ("CONSTELACIÓN-2a; occurrence 11167", 2, (212,)),
    61: ("COSMOS-1a; decisión Módulo 1", 1, ()),
    62: ("COSMOVISIÓN-1a; decisión Módulo 1", 2, (61,)),
    71: ("ECLIPSE-2a", 3, (190,)),
    97: ("FUERZA-ROZAMIENTO-1a", 2, (93,)),
    161: ("PATRIMONIO-DE-LA-HUMANIDAD-1a; decisión Módulo 1", 2, ()),
    191: ("SOL-2a", 2, (190,)),
    202: ("TIEMPO-1a; corrección explícita del error legacy", 1, ()),
    223: ("VÍA-LÁCTEA-2a; occurrence 11169", 2, (212,)),
}

VIDEO_DETAIL_SOURCE_IDS = (37, 38, 39, 44)


def _stable_alternative(db, alternative_id):
    row = db.execute("""SELECT a.alternative_id,a.working_label,a.original_code,
        c.preferred_label FROM alternative a JOIN concept c USING(concept_id)
        WHERE a.alternative_id=? AND a.retired_at IS NULL""", (alternative_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Falta alternative_id estable {alternative_id}")
    return row


def _detail_plan(db):
    changes, preserved, numbered = [], [], []
    duplicate_groups = duplicate_occurrences = 0
    for source_id in VIDEO_DETAIL_SOURCE_IDS:
        rows = db.execute("""SELECT occurrence_id,original_gloss,source_detail_1
            FROM occurrence WHERE source_id=? ORDER BY occurrence_id""", (source_id,)).fetchall()
        groups = {}
        for row in rows:
            gloss = (row["original_gloss"] or "").strip().upper()
            if not gloss:
                preserved.append({"occurrence_id": row["occurrence_id"], "reason": "glosa vacía"})
                continue
            groups.setdefault(gloss, []).append(row)
        for gloss, occurrences in groups.items():
            if len(occurrences) > 1:
                duplicate_groups += 1
                duplicate_occurrences += len(occurrences) - 1
            for index, row in enumerate(occurrences):
                desired = gloss if index == 0 else f"{gloss} {index}"
                current = (row["source_detail_1"] or "").strip()
                if current:
                    preserved.append({"occurrence_id": row["occurrence_id"], "value": current,
                                      "proposed": desired, "reason": "valor canónico preexistente"})
                else:
                    changes.append({"occurrence_id": row["occurrence_id"], "source_id": source_id,
                                    "value": desired})
                if index:
                    numbered.append({"source_id": source_id, "occurrence_id": row["occurrence_id"],
                                     "value": desired})
    return {"changes": changes, "preserved": preserved, "numbered": numbered,
            "duplicate_groups": duplicate_groups,
            "duplicate_occurrences": duplicate_occurrences}

def normalize(db, apply=False):
    before = audit_database(db); before_relations = [tuple(r) for r in db.execute(
        "SELECT * FROM alternative_relation ORDER BY alternative_relation_id")]
    details = _detail_plan(db)
    plan = {"canonical_details": db.execute("SELECT count(*) FROM occurrence WHERE source_detail_1 IS NULL AND legacy_source_detail_1 IS NOT NULL OR source_detail_2 IS NULL AND legacy_source_detail_2 IS NOT NULL").fetchone()[0],
            "video_source_details": details,
            "concept_areas": db.execute("SELECT count(*) FROM concept WHERE knowledge_area_1 IS NOT 'ASTRONOMÍA' OR knowledge_area_2 IS NOT NULL").fetchone()[0],
            "sources": [], "morphology": []}
    for source_id, desired in SOURCE_RULES.items():
        row = db.execute("SELECT * FROM source WHERE source_id=?", (source_id,)).fetchone()
        if row is None: raise RuntimeError(f"Falta source_id {source_id}")
        changes = {k: v for k, v in desired.items() if k != "detail_1" and row[k] != v}
        detail_changes = db.execute("SELECT count(*) FROM occurrence WHERE source_id=? AND source_detail_1 IS NOT ?", (source_id, desired["detail_1"])).fetchone()[0]
        plan["sources"].append({"source_id": source_id, "changes": changes, "occurrence_details": detail_changes})
    for aid, (reference, count, component_ids) in MORPHOLOGY.items():
        alternative = _stable_alternative(db, aid)
        for component_id in component_ids:
            _stable_alternative(db, component_id)
        current = db.execute("SELECT * FROM alternative_morphology WHERE alternative_id=? AND is_current=1", (aid,)).fetchone()
        plan["morphology"].append({"alternative_id": aid,
            "current_label": f"{alternative['preferred_label']}-{alternative['working_label']}",
            "documentary_reference": reference,
            "old": "N/A" if current and current["component_count_not_applicable"] else current["component_count"] if current else None,
            "new": count, "component_alternative_ids": list(component_ids)})
    if not apply: return {"mode": "dry-run", "plan": plan, "before": before}
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("""UPDATE occurrence SET source_detail_1=COALESCE(source_detail_1,legacy_source_detail_1),
            source_detail_2=COALESCE(source_detail_2,legacy_source_detail_2)""")
        db.execute("UPDATE concept SET knowledge_area_1='ASTRONOMÍA',knowledge_area_2=NULL")
        for item in details["changes"]:
            db.execute("UPDATE occurrence SET source_detail_1=? WHERE occurrence_id=? AND trim(coalesce(source_detail_1,''))=''",
                       (item["value"], item["occurrence_id"]))
        for item in plan["sources"]:
            sid=item["source_id"]; desired=SOURCE_RULES[sid]
            if item["changes"]:
                db.execute("""INSERT INTO source_revision(source_id,source_name,source_type,source_reference,start_year,end_year,end_year_status,legacy_source_code,source_scope,format_original,format_detail,region_description,characterization,reported_entry_count,change_note)
                    SELECT source_id,source_name,source_type,source_reference,start_year,end_year,end_year_status,legacy_source_code,source_scope,format_original,format_detail,region_description,characterization,reported_entry_count,'Normalización Fase 15' FROM source WHERE source_id=?""",(sid,))
                cols=list(item["changes"]); db.execute("UPDATE source SET "+",".join(f"{c}=?" for c in cols)+",updated_at=CURRENT_TIMESTAMP WHERE source_id=?", (*[item["changes"][c] for c in cols],sid))
            db.execute("UPDATE occurrence SET source_detail_1=?,source_detail_2=CASE WHEN ?=43 THEN NULL ELSE source_detail_2 END WHERE source_id=?", (desired["detail_1"],sid,sid))
        for item in plan["morphology"]:
            components=[{"position": position, "component_alternative_id": component_id}
                        for position, component_id in enumerate(item["component_alternative_ids"], 1)]
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
