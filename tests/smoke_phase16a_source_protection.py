"""Run Edge against a temporary baseline copy; screenshots remain in output-dir."""
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

    baseline = ROOT / "import_inputs/astronomia/lesico_astronomia_working_baseline_post_fase15_2026-09-04.db"
    working = baseline.with_name("lesico_astronomia_working.db")
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (working, baseline)}
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lesico-16a-") as tmp:
        path = Path(tmp) / "smoke.db"
        shutil.copy2(baseline, path)
        spec = importlib.util.spec_from_file_location("migration018", ROOT / "migrations/018_source_protection.py")
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        assert migration.migrate(path, None)
        assert not migration.migrate(path, None)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*),sum(analyst_protected) FROM source").fetchone() == (44, 44)
        db.close()
        os.environ.update(LESICO_DATABASE_PATH=str(path), LESICO_ANALYST_ROUTE="smoke-analyst",
                          LESICO_REVIEWER_ROUTE="smoke-reviewer", LESICO_MASTER_ROUTE="smoke-master")
        from app import app
        server = make_server("127.0.0.1", 0, app)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                root = f"http://127.0.0.1:{server.server_port}"
                def visit(role, route="/fuentes"):
                    response = page.goto(f"{root}/smoke-{role}{route}")
                    assert response.status == 200
                def source_row():
                    return page.locator("tr").filter(has_text="Smoke Phase16A")
                visit("analyst")
                assert page.get_by_role("link", name="Editar", exact=True).count() == 0
                assert "Protegida" not in page.locator("body").inner_text()
                assert page.request.post(f"{root}/smoke-analyst/fuentes/1/actualizar", data={}).status == 404
                page.locator('[name="source_name"]').fill("Smoke Phase16A")
                page.locator('[name="source_type"]').select_option("OTRO")
                page.get_by_role("button", name="Guardar fuente").click()
                source_row().get_by_role("link", name="Editar", exact=True).click()
                page.locator('[name="characterization"]').fill("Direct analyst edit")
                page.get_by_role("button", name="Guardar cambios").click()
                page.screenshot(path=str(output / "analyst-on.png"), full_page=True)
                visit("reviewer")
                source_row().get_by_role("button", name="Proteger", exact=True).click()
                assert "Protegida contra Analyst" in source_row().inner_text()
                source_row().get_by_role("link", name="Desproteger", exact=True).click()
                assert "Los analistas podrán editar esta fuente" in page.locator("body").inner_text()
                page.screenshot(path=str(output / "confirmation.png"))
                page.get_by_role("link", name="Cancelar", exact=True).click()
                assert "Protegida contra Analyst" in source_row().inner_text()
                source_row().get_by_role("link", name="Desproteger", exact=True).click()
                page.get_by_role("button", name="Desproteger", exact=True).click()
                visit("master")
                page.locator('[name="source_name"]').fill("Another source")
                page.locator('[name="source_type"]').select_option("OTRO")
                page.get_by_role("button", name="Guardar fuente").click()
                other = page.locator("tr").filter(has_text="Another source")
                assert "Protegida contra Analyst" in other.inner_text()
                other.get_by_role("link", name="Editar", exact=True).click()
                page.get_by_role("button", name="Guardar cambios").click()
                other.get_by_role("link", name="Desproteger", exact=True).click()
                page.get_by_role("button", name="Desproteger", exact=True).click()
                page.screenshot(path=str(output / "master.png"), full_page=True)
                visit("analyst")
                other.get_by_role("link", name="Editar", exact=True).click()
                page.locator('[name="characterization"]').fill("Edited by another role")
                page.get_by_role("button", name="Guardar cambios").click()
                visit("master", "/trabajo")
                page.locator('[name="enabled"]').uncheck()
                page.get_by_role("button", name="Guardar configuración").click()
                visit("analyst")
                assert page.get_by_role("button", name="Guardar fuente").count() == 0
                assert page.get_by_role("link", name="Editar", exact=True).count() == 0
                page.screenshot(path=str(output / "analyst-off.png"), full_page=True)
                visit("master", "/trabajo")
                page.locator('[name="enabled"]').check()
                page.get_by_role("button", name="Guardar configuración").click()
                visit("analyst")
                assert page.get_by_role("link", name="Editar", exact=True).count() == 2
                browser.close()
        finally:
            server.shutdown(); thread.join(); server.server_close()
    assert hashes == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes}
    print("SMOKE_PHASE16A_OK; 44 existing sources protected; working/baseline hashes intact")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    run(parser.parse_args().output_dir)
