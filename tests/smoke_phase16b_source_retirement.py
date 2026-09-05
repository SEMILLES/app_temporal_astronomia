"""Visual retirement smoke on a temporary post-16A baseline copy only."""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(output):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    baseline = ROOT / "import_inputs/astronomia/lesico_astronomia_working_baseline_post_fase16a_2026-09-04.db"
    protected = [ROOT / "lesico_prototipo.db", *baseline.parent.glob("*.db")]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lesico-16b-") as tmp:
        path = Path(tmp) / "smoke.db"; shutil.copy2(baseline, path)
        spec = importlib.util.spec_from_file_location("migration019", ROOT / "migrations/019_source_retirement.py")
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        assert migration.migrate(path, None)
        assert not migration.migrate(path, None)
        db = sqlite3.connect(path)
        cases = []
        for number in range(1, 5):
            source = db.execute("INSERT INTO source(source_name,source_type) VALUES(?,'OTRO')", (f"Smoke16B Origin {number}",)).lastrowid
            target = db.execute("INSERT INTO source(source_name,source_type,start_year,end_year,end_year_status) VALUES(?,'MATERIAL_IMPRESO',?,?,?)",
                                (f"Smoke16B Target {number}", 2021 if number in (2,3) else None, 2023 if number in (2,3) else None, "known" if number in (2,3) else None)).lastrowid
            for year in (2019,2022,2024):
                db.execute("INSERT INTO occurrence(source_id,original_gloss,occurrence_year,source_detail_1,source_detail_2,source_detail_1_status,source_detail_2_status) VALUES(?,'SMOKE',?,'Literal','02:30','VALUE','VALUE')", (source, year))
            cases.append((source,target))
        db.commit(); db.close()
        os.environ.update(LESICO_DATABASE_PATH=str(path), LESICO_ANALYST_ROUTE="smoke-analyst", LESICO_REVIEWER_ROUTE="smoke-reviewer", LESICO_MASTER_ROUTE="smoke-master")
        from app import app
        server = make_server("127.0.0.1", 0, app)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width":1440,"height":1050})
                root = f"http://127.0.0.1:{server.server_port}"
                for number, (source, target) in enumerate(cases, 1):
                    role = "master" if number == 4 else "reviewer"
                    page.goto(f"{root}/smoke-{role}/fuentes/{source}/retirar")
                    if number == 4:
                        page.locator('[name="destination_mode"]').select_option("new")
                        page.locator('[name="source_name"]').fill("Smoke16B New destination")
                        page.locator('[name="source_type"]').select_option("OTRO")
                    else:
                        page.locator('[name="destination_id"]').select_option(str(target))
                    before = path.read_bytes()
                    page.get_by_role("button", name="Previsualizar", exact=True).click()
                    assert path.read_bytes() == before
                    assert "Previsualización" in page.locator("body").inner_text()
                    assert "se conservarán literalmente" in page.locator("body").inner_text()
                    if number in (2,3):
                        assert "2 occurrences quedan fuera" in page.locator("body").inner_text()
                        page.locator(f'[name="resolution"][value="{"expand" if number==2 else "clear"}"]').check()
                    page.screenshot(path=str(output/f"case-{number}-preview.png"), full_page=True)
                    page.locator('[name="reason"]').fill(f"Disposable smoke case {number}")
                    page.locator('[name="confirm"]').check()
                    page.get_by_role("button", name="Confirmar retiro / migración", exact=True).click()
                    assert "Retiro / migración completado" in page.locator("body").inner_text()
                    page.screenshot(path=str(output/f"case-{number}-history.png"), full_page=True)
                page.goto(f"{root}/smoke-analyst/fuentes")
                assert "Retirar / migrar fuente" not in page.locator("body").inner_text()
                assert page.request.get(f"{root}/smoke-analyst/fuentes/{cases[0][0]}/retirar").status == 404
                assert page.request.post(f"{root}/smoke-analyst/fuentes/{cases[0][0]}/retirar", data={"action":"confirm"}).status == 404
                page.goto(f"{root}/smoke-analyst/aportes/nuevo")
                assert "Smoke16B Origin" not in page.locator('[name="source_id"]').inner_text()
                page.goto(f"{root}/smoke-master/fuentes/retiradas")
                assert page.get_by_role("link", name="Consultar historial", exact=True).count() == 4
                page.screenshot(path=str(output/"retired-sources.png"), full_page=True)
                browser.close()
            db = sqlite3.connect(path)
            try:
                assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []
                assert db.execute("SELECT count(*) FROM occurrence o JOIN source s USING(source_id) WHERE s.retired_at IS NOT NULL").fetchone()[0] == 0
                assert db.execute("SELECT count(*) FROM occurrence_revision WHERE change_note LIKE 'Disposable smoke case %'").fetchone()[0] == 12
                assert db.execute("SELECT start_year,end_year FROM source WHERE source_id=?", (cases[1][1],)).fetchone() == (2019,2024)
                assert db.execute("SELECT occurrence_year FROM occurrence WHERE source_id=? ORDER BY occurrence_id", (cases[2][1],)).fetchall() == [(None,),(2022,),(None,)]
                assert db.execute("SELECT analyst_protected FROM source WHERE source_name='Smoke16B New destination'").fetchone()[0] == 1
                assert db.execute("SELECT count(*) FROM catalog_publication").fetchone()[0] == 0
            finally: db.close()
        finally:
            server.shutdown(); thread.join(); server.server_close()
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes}
    print("SMOKE_PHASE16B_OK; integrity=ok; FK=0; protected hashes intact")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output-dir", type=Path, required=True)
    run(parser.parse_args().output_dir)
