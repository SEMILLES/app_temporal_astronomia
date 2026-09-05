"""Edge smoke on a temporary post-16B baseline copy; never edits real sources."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run(output):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    baseline=ROOT/"import_inputs/astronomia/lesico_astronomia_working_baseline_post_fase16b_2026-09-05.db"
    protected=[ROOT/"lesico_prototipo.db",*baseline.parent.glob("*.db")]
    hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lesico-16c-") as tmp:
        path=Path(tmp)/"smoke.db";shutil.copy2(baseline,path)
        db=sqlite3.connect(path)
        cases=[]
        for number in range(1,6):
            a=db.execute("INSERT INTO source(source_name,source_type,start_year,end_year,end_year_status,region_description,characterization,format_original,source_reference) VALUES(?,'OTRO',2018,2025,'known','Region A','Character A','Video','Reference A')",(f"Smoke16C A{number}",)).lastrowid
            b=db.execute("INSERT INTO source(source_name,source_type,start_year,end_year,end_year_status,region_description) VALUES(?,'MATERIAL_IMPRESO',?,?,?,'Region B')",(f"Smoke16C B{number}",2021 if number in (3,5) else None,2023 if number in (3,5) else None,"known" if number in (3,5) else None)).lastrowid
            c=db.execute("INSERT INTO source(source_name,source_type,start_year,end_year,end_year_status) VALUES(?,'OTRO',?,?,?)",(f"Smoke16C C{number}",2021 if number==3 else None,2023 if number==3 else None,"known" if number==3 else None)).lastrowid
            ids=[]
            for year in (2019,2022,2024):
                ids.append(db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year,source_detail_1,source_detail_2,source_detail_1_status,source_detail_2_status) VALUES(?,'SMOKE',?,'Literal','02:30','VALUE','VALUE')",(a,year)).lastrowid)
            if number>=4:db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year) VALUES(?,'SMOKE B',2020)",(b,))
            cases.append((a,b,c,ids))
        db.commit();db.close()
        os.environ.update(LESICO_DATABASE_PATH=str(path),LESICO_ANALYST_ROUTE="smoke-analyst",LESICO_REVIEWER_ROUTE="smoke-reviewer",LESICO_MASTER_ROUTE="smoke-master")
        from app import app
        server=make_server("127.0.0.1",0,app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel="msedge",headless=True)
                page=browser.new_page(viewport={"width":1440,"height":1100})
                root=f"http://127.0.0.1:{server.server_port}"
                for number,(a,b,c,ids) in enumerate(cases,1):
                    kind="dividir" if number<=3 else "fusionar"
                    role="reviewer" if number%2 else "master"
                    response=page.goto(f"{root}/smoke-{role}/fuentes/{a}/{kind}");assert response.status==200
                    if number<=3:
                        page.locator('[name="mode"]').select_option("keep" if number==1 else "replace")
                        if number==1:
                            page.get_by_role("button",name="Añadir Source destino nueva").click()
                            assert page.locator('[name="new:1__source_type"]').input_value()=="OTRO"
                            assert page.locator('[name="new:1__start_year"]').input_value()=="2018"
                            assert page.locator('[name="new:1__region_description"]').input_value()=="Region A"
                            assert page.locator('[name="new:1__characterization"]').input_value()=="Character A"
                            page.locator('[name="new:1__source_name"]').fill("Smoke16C Split new")
                            page.locator('[name="new:1__region_description"]').fill("Edited inherited region")
                            targets=[f"source:{a}","new:1","new:1"]
                        else:targets=[f"source:{b}",f"source:{c}",f"source:{c}"]
                        for oid,target in zip(ids,targets):page.locator(f'[name="occurrence_{oid}"]').select_option(target)
                        button="Previsualizar división"
                    else:
                        page.locator('[name="other_source_id"]').select_option(str(b))
                        if number==5:
                            page.locator('[name="metadata_template"]').select_option("blank")
                            assert page.locator('[name="source_type"]').input_value()==""
                            page.locator('[name="metadata_template"]').select_option("second")
                            assert page.locator('[name="region_description"]').input_value()=="Region B"
                        else:
                            assert page.locator('[name="region_description"]').input_value()=="Region A"
                            assert page.locator('[name="source_reference"]').input_value()=="Reference A"
                        page.locator('[name="source_name"]').fill(f"Smoke16C Merged {number}")
                        button="Previsualizar fusión"
                    before=path.read_bytes();page.get_by_role("button",name=button,exact=True).click()
                    assert path.read_bytes()==before
                    assert "Previsualización" in page.locator("h1").inner_text()
                    if number==3:
                        page.locator(f'[name="resolution_source:{b}"][value="expand"]').check()
                        page.locator(f'[name="resolution_source:{c}"][value="clear"]').check()
                    if number==5:page.locator('[name="resolution_new:1"][value="expand"]').check()
                    page.screenshot(path=str(output/f"case-{number}-preview.png"),full_page=True)
                    page.locator('[name="reason"]').fill(f"Smoke 16C operation {number}")
                    page.locator('[name="confirm"]').check()
                    page.get_by_role("button",name="Confirmar división" if number<=3 else "Confirmar fusión",exact=True).click()
                    assert ("División de Source" if number<=3 else "Fusión de Sources") in page.locator("body").inner_text()
                    assert f"Smoke 16C operation {number}" in page.locator("body").inner_text()
                    page.screenshot(path=str(output/f"case-{number}-history.png"),full_page=True)
                page.goto(f"{root}/smoke-analyst/fuentes")
                for action in ("Dividir fuente","Fusionar fuentes"):assert action not in page.locator("body").inner_text()
                for kind in ("dividir","fusionar"):
                    assert page.request.get(f"{root}/smoke-analyst/fuentes/{cases[0][0]}/{kind}").status==404
                    assert page.request.post(f"{root}/smoke-analyst/fuentes/{cases[0][0]}/{kind}",data={"action":"confirm"}).status==404
                browser.close()
            db=sqlite3.connect(path)
            try:
                assert db.execute("PRAGMA integrity_check").fetchall()==[("ok",)]
                assert db.execute("PRAGMA foreign_key_check").fetchall()==[]
                assert db.execute("SELECT count(*) FROM occurrence o JOIN source s USING(source_id) WHERE s.retired_at IS NOT NULL").fetchone()[0]==0
                assert db.execute("SELECT count(*) FROM occurrence WHERE source_id IS NULL").fetchone()[0]==0
                assert db.execute("SELECT count(*) FROM catalog_publication").fetchone()[0]==0
                assert db.execute("SELECT retired_at FROM source WHERE source_id=?",(cases[0][0],)).fetchone()[0] is None
                assert db.execute("SELECT count(*) FROM occurrence WHERE source_id=?",(cases[0][0],)).fetchone()[0]==1
                assert db.execute("SELECT analyst_protected,region_description FROM source WHERE source_name='Smoke16C Split new'").fetchone()==(1,"Edited inherited region")
                assert db.execute("SELECT count(*) FROM source WHERE retired_at IS NOT NULL").fetchone()[0]==6
                assert db.execute("SELECT count(*) FROM activity_event WHERE event_type IN ('source_split','sources_merged')").fetchone()[0]==5
                assert db.execute("SELECT occurrence_year FROM occurrence WHERE occurrence_id=?",(cases[2][3][2],)).fetchone()[0] is None
            finally:db.close()
        finally:server.shutdown();thread.join();server.server_close()
    assert hashes=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes}
    print("SMOKE_PHASE16C_OK; integrity=ok; FK=0; protected hashes intact; no migration required")


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output-dir",type=Path,required=True)
    run(parser.parse_args().output_dir)
