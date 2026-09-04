"""Deterministic Phase 15 source/detail normalization for disposable databases."""
import argparse, json, sqlite3
from pathlib import Path

PROTECTED={"lesico_astronomia_working.db","lesico_astronomia_candidate.db","lesico_astronomia_write_test.db","lesico_prototipo.db"}
EXPLICIT={
 1:"MATERIAL_IMPRESO",2:"VARIOS_VIDEOS_VARIAS_SENAS",3:"VIDEO_POR_SENA",4:"VIDEO_POR_SENA",5:"VIDEO_POR_SENA",6:"VIDEO_POR_SENA",7:"MATERIAL_IMPRESO",8:"MATERIAL_IMPRESO",9:"MATERIAL_IMPRESO",10:"MATERIAL_IMPRESO",11:"MATERIAL_IMPRESO",12:"MATERIAL_IMPRESO",13:"MATERIAL_IMPRESO",14:"MATERIAL_IMPRESO",15:"MATERIAL_IMPRESO",16:"MATERIAL_IMPRESO",17:"MATERIAL_IMPRESO",18:"MATERIAL_IMPRESO",19:"UN_VIDEO_VARIAS_SENAS",20:"VARIOS_VIDEOS_VARIAS_SENAS",21:"VIDEO_POR_SENA",22:"VARIOS_VIDEOS_VARIAS_SENAS",23:"VARIOS_VIDEOS_VARIAS_SENAS",24:"VIDEO_POR_SENA",25:"VIDEO_POR_SENA",26:"VARIOS_VIDEOS_VARIAS_SENAS",27:"VARIOS_VIDEOS_VARIAS_SENAS",28:"VIDEO_POR_SENA",29:"VARIOS_VIDEOS_VARIAS_SENAS",30:"VARIOS_VIDEOS_VARIAS_SENAS",31:"VIDEO_POR_SENA",32:"MATERIAL_IMPRESO",33:"MATERIAL_IMPRESO",34:"VARIOS_VIDEOS_VARIAS_SENAS",35:"VARIOS_VIDEOS_VARIAS_SENAS",36:"VIDEO_POR_SENA",37:"VIDEO_POR_SENA",38:"VIDEO_POR_SENA",39:"VIDEO_POR_SENA",40:"UN_VIDEO_VARIAS_SENAS",41:"UN_VIDEO_VARIAS_SENAS",42:"UN_VIDEO_VARIAS_SENAS",43:"UN_VIDEO_VARIAS_SENAS",44:"VIDEO_POR_SENA"}

def _status(value): return "VALUE" if value is not None and str(value).strip() else "UNKNOWN"
def normalize(connection, apply=False):
    sources=connection.execute("SELECT * FROM source WHERE source_id BETWEEN 1 AND 44 ORDER BY source_id").fetchall()
    if len(sources)!=44: raise ValueError("Se esperaban las 44 Sources originales.")
    plan={"sources":[],"occurrences":[]}
    for source in sources:
        wanted=EXPLICIT[source["source_id"]]
        if source["source_type"]!=wanted: plan["sources"].append((source["source_id"],wanted))
    for row in connection.execute("SELECT o.*,s.source_type FROM occurrence o JOIN source s USING(source_id) WHERE occurrence_id BETWEEN 1 AND 253 ORDER BY occurrence_id"):
        typ=EXPLICIT[row["source_id"]]; d1=row["source_detail_1"]; d2=row["source_detail_2"]
        if typ=="MATERIAL_IMPRESO": d1=None; s1="NA"; s2=_status(d2)
        elif typ=="VIDEO_POR_SENA": s1=_status(d1); d2=None; s2="NA"
        else: s1=_status(d1); s2=_status(d2)
        if row["occurrence_id"]==206: d2=None; s2="NA"
        if row["occurrence_id"]==210: d2="32:40"; s2="VALUE"
        wanted=(d1,d2,s1,s2)
        current=(row["source_detail_1"],row["source_detail_2"],row["source_detail_1_status"],row["source_detail_2_status"])
        if current!=wanted: plan["occurrences"].append((row["occurrence_id"],*wanted))
    if not apply:return plan
    connection.execute("BEGIN IMMEDIATE")
    try:
        for sid,wanted in plan["sources"]:
            connection.execute("""INSERT INTO source_revision(source_id,source_name,source_type,source_reference,start_year,end_year,end_year_status,legacy_source_code,source_scope,format_original,format_detail,region_description,characterization,reported_entry_count,change_note) SELECT source_id,source_name,source_type,source_reference,start_year,end_year,end_year_status,legacy_source_code,source_scope,format_original,format_detail,region_description,characterization,reported_entry_count,'Normalización de tipo de Source Fase 15' FROM source WHERE source_id=?""",(sid,))
            connection.execute("UPDATE source SET source_type=?,updated_at=CURRENT_TIMESTAMP WHERE source_id=?",(wanted,sid))
        connection.executemany("UPDATE occurrence SET source_detail_1=?,source_detail_2=?,source_detail_1_status=?,source_detail_2_status=? WHERE occurrence_id=?",[(d1,d2,s1,s2,oid) for oid,d1,d2,s1,s2 in plan["occurrences"]])
        if connection.execute("PRAGMA foreign_key_check").fetchall(): raise RuntimeError("Foreign keys inválidas")
        connection.commit()
    except Exception: connection.rollback(); raise
    return plan

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--database",required=True);parser.add_argument("--apply",action="store_true");args=parser.parse_args(argv)
    path=Path(args.database).resolve()
    if path.name.lower() in PROTECTED: raise SystemExit("Base protegida; use una copia desechable")
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.execute("PRAGMA foreign_keys=ON")
    try: plan=normalize(db,args.apply)
    finally:db.close()
    print(json.dumps({"mode":"apply" if args.apply else "dry-run","source_changes":len(plan["sources"]),"occurrence_changes":len(plan["occurrences"])},ensure_ascii=False))
if __name__=="__main__":main()
