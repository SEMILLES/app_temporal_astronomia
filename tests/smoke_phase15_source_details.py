"""Manual smoke for Phase 15 source semantics; requires a disposable DB path."""
import os, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("LESICO_ANALYST_ROUTE","fase15-analyst")
os.environ.setdefault("LESICO_REVIEWER_ROUTE","fase15-reviewer")
os.environ.setdefault("LESICO_MASTER_ROUTE","fase15-master")

from app import app
from database import conectar

client=app.test_client()
def page(path):
    response=client.get(path);assert response.status_code==200,(path,response.status_code);return response.get_data(as_text=True)

new=page("/fase15-analyst/aportes/nuevo")+client.get("/static/source-details.js").get_data(as_text=True)
for text in ("Submaterial / sección","Página","Título / identificador del video","Título del video","Tiempo","Detalle Fuente 1","Detalle Fuente 2","source-details.js"):
    assert text in new,text
assert client.post("/fase15-analyst/fuentes/nueva",data={"source_name":"Smoke Analyst Source","source_type":"OTRO"}).status_code==302
master=page("/fase15-master/trabajo");assert "Permitir a analistas crear nuevas fuentes" in master
assert client.post("/fase15-master/configuracion/creacion-fuentes",data={}).status_code==302
assert "Registrar fuente" not in page("/fase15-analyst/trabajo")
assert client.post("/fase15-analyst/fuentes/nueva",data={"source_name":"Denied","source_type":"OTRO"}).status_code==404
assert client.post("/fase15-reviewer/fuentes/nueva",data={"source_name":"Smoke Reviewer Source","source_type":"OTRO"}).status_code==302
assert client.post("/fase15-master/fuentes/nueva",data={"source_name":"Smoke Master Source","source_type":"OTRO"}).status_code==302
db=conectar()
for occurrence_id,expected in ((31,"p. 143"),(35,"FENASCOL_yt · 1:49:00"),(59,"COHETE 2")):
    alternative_id=db.execute("SELECT alternative_id FROM assignment WHERE occurrence_id=? AND is_current=1",(occurrence_id,)).fetchone()[0]
    catalog=page(f"/fase15-analyst/catalogo-interno/alternativas/{alternative_id}")
    assert expected in catalog,(occurrence_id,expected)
    assert "Detalle Fuente 1" not in catalog and "Detalle Fuente 2" not in catalog
    assert catalog.count("Información técnica e historial")==1
alternative_id=db.execute("SELECT alternative_id FROM assignment WHERE occurrence_id=9 AND is_current=1").fetchone()[0];db.close()
catalog=page(f"/fase15-analyst/catalogo-interno/alternativas/{alternative_id}")
assert "Referencia en la fuente</dt><dd>ASTERISMO</dd>" not in catalog
print("SMOKE_PHASE15_SOURCE_DETAILS_OK")
