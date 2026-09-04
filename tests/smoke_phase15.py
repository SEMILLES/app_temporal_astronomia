"""Manual Phase 15 EXISTING/NEW smoke flow; requires a disposable DB path."""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if len(sys.argv) != 2:
    raise SystemExit("usage: smoke_phase15.py DISPOSABLE_DB")
os.environ.update(LESICO_DATABASE_PATH=sys.argv[1], LESICO_ANALYST_ROUTE="ana",
                  LESICO_REVIEWER_ROUTE="rev", LESICO_MASTER_ROUTE="mas")
from app import app

client=app.test_client()
db=sqlite3.connect(sys.argv[1]); db.row_factory=sqlite3.Row
context=db.execute("""SELECT c.concept_id,a.alternative_id FROM concept c JOIN alternative a USING(concept_id)
    WHERE a.retired_at IS NULL ORDER BY c.concept_id,a.alternative_id LIMIT 1""").fetchone(); db.close()

def register(gloss):
    response=client.post("/ana/ocurrencias/guardar",data={"source_id":"1","original_gloss":gloss,
        "source_detail_1":"Smoke Fase 15","reference_kind":"concept","reference_concept_id":str(context["concept_id"])})
    assert response.status_code==302,response.get_data(as_text=True); return int(response.location.split("/")[-2])

existing_occurrence=register("FASE15-SMOKE-EXISTING")
assert client.post(f"/ana/ocurrencias/{existing_occurrence}/clasificar",data={"proposal_kind":"EXISTING","proposed_existing_alternative_id":str(context["alternative_id"])}).status_code==302
db=sqlite3.connect(sys.argv[1]); existing_submission=db.execute("SELECT max(submission_id) FROM submission").fetchone()[0]; db.close()
assert client.post(f"/rev/aportes/{existing_submission}/decidir",data={"decision":"existing","alternative_id":str(context["alternative_id"]),"relation_policy":"preserve"}).status_code==302
assert "FASE15-SMOKE-EXISTING" in client.get(f"/ana/catalogo-interno/alternativas/{context['alternative_id']}").get_data(as_text=True)

new_occurrence=register("FASE15-SMOKE-NEW")
assert client.post(f"/ana/ocurrencias/{new_occurrence}/clasificar",data={"proposal_kind":"NEW","phonological_relation_answer":"NO","morphology_component_count":"N/A"}).status_code==302
db=sqlite3.connect(sys.argv[1]); new_submission=db.execute("SELECT max(submission_id) FROM submission").fetchone()[0]; db.close()
response=client.post(f"/rev/aportes/{new_submission}/decidir",data={"decision":"new","nomenclature_mode":"automatic","approve_morphology":"yes","review_note":"Smoke Fase 15"})
assert response.status_code==302,response.get_data(as_text=True)
db=sqlite3.connect(sys.argv[1]); new_alternative=db.execute("SELECT resolved_alternative_id FROM alternative_submission WHERE submission_id=?",(new_submission,)).fetchone()[0]; db.close()
catalog=client.get(f"/ana/catalogo-interno/alternativas/{new_alternative}").get_data(as_text=True)
assert "FASE15-SMOKE-NEW" in catalog and "Smoke Fase 15" in catalog
print(f"EXISTING OK occurrence={existing_occurrence} alternative={context['alternative_id']}")
print(f"NEW OK occurrence={new_occurrence} alternative={new_alternative}")
