"""Optional real-browser smoke: run with Python + Playwright Chromium installed."""
import json
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests'))
from test_usage_context import CatalogContextTests, connect
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server


def main():
    fixture=CatalogContextTests();fixture.setUp()
    db=connect(fixture.path)
    db.execute("INSERT INTO concept(preferred_label,semantic_field_1,knowledge_area_1) VALUES('A-VECES','Expresiones de Tiempo','Artes')")
    db.execute("INSERT INTO alternative(concept_id,working_label) VALUES(2,'1a')")
    db.commit();db.close()
    server=make_server('127.0.0.1',0,fixture.client.application)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/ana/catalogo-interno/conceptos/1')
            visible=lambda:page.locator('.boton-concepto:visible').count()
            assert visible()==2
            page.locator('#filtro-video').check();assert visible()==1
            assert page.locator('.boton-variante:visible').count()==3
            page.locator('[data-pestana="uso-1"]').click()
            assert page.locator('[data-panel="uso-1"]').is_visible(), page.locator('[data-panel="uso-1"]').evaluate("el=>({hidden:el.hidden,display:getComputedStyle(el).display,parent:el.parentElement.outerHTML.slice(0,1800)})")
            assert 'Córdoba' in page.locator('[data-panel="uso-1"]').inner_text()
            page.locator('.boton-variante[data-alternative-select="3"]').click()
            assert page.locator('[data-alternative-panel="3"]').is_visible()
            assert page.locator('[data-alternative-panel="3"] .media-alternativa').count()==0
            page.locator('#filtro-variacion').select_option('both');assert visible()==1
            page.locator('#filtro-area').select_option('artes');assert visible()==1
            page.locator('#buscador-campos').fill('col');assert page.locator('#filtro-campos label:visible').count()==1
            page.locator('#filtro-campos input[value="colores"]').check();assert visible()==1
            page.locator('#buscador-campos').fill('');page.locator('#filtro-campos input[value="expresiones de tiempo"]').check()
            assert page.locator('#filtro-campos input:checked').count()==2
            page.locator('#limpiar-filtros').click();assert visible()==2
            page.locator('#buscador-catalogo').fill('a veces');assert visible()==1
            page.locator('.boton-concepto:visible').click()
            assert page.locator('.cabecera-concepto').inner_text().startswith('A-VECES')
            assert 'Expresiones de Tiempo' in page.locator('.cabecera-concepto').inner_text()
            page.locator('#limpiar-filtros').click();page.wait_for_load_state('domcontentloaded');assert visible()==2
            page.goto(f'http://127.0.0.1:{server.server_port}/ana/catalogo-interno/conceptos/1')
            page.locator('[data-pestana="uso-1"]').click()
            output=ROOT/'validation';output.mkdir(exist_ok=True)
            page.screenshot(path=str(output/'usage-context-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(output/'usage-context-mobile.png'),full_page=True)
            assert not errors,errors
            browser.close()
            report={'browser':'Chromium','javascript_errors':errors,'filters_combined':True,'all_alternatives_retained':True,'conditional_profile_tab':True,'semantic_multiselect_search':True,'navigation_and_clear':True}
            (output/'browser-smoke.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(report))
    finally:
        server.shutdown();thread.join();fixture.doCleanups()


if __name__=='__main__':main()
