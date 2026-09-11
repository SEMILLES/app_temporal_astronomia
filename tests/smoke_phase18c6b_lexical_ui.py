"""Browser checks against a Flask test client and a disposable database.

Run: .venv/Scripts/python.exe -m tests.smoke_phase18c6b_lexical_ui
Requires the locally available Playwright package and Microsoft Edge.
"""
from urllib.parse import urlsplit, parse_qsl

from playwright.sync_api import sync_playwright, expect
from werkzeug.datastructures import MultiDict

from tests.test_submission_lexical_ui import LexicalUITests


def main():
    fixture = LexicalUITests()
    fixture.setUp()
    try:
        sid = fixture.create()
        before = fixture.dump()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def serve(route):
                request = route.request
                url = urlsplit(request.url)
                response = fixture.client.open(
                    url.path, method=request.method,
                    data=MultiDict(parse_qsl(request.post_data or '', keep_blank_values=True)),
                    follow_redirects=True)
                route.fulfill(status=response.status_code, body=response.data,
                              content_type='text/html; charset=utf-8')

            page.route('http://lexical.test/**', serve)
            page.goto(f'http://lexical.test/aportes/{sid}')
            form = page.locator('.review-decision')
            form.locator('[name=decision][value=new]').check()
            expect(form.locator('.group-decision')).to_have_count(2)
            expect(form.locator('.lexical-preview:visible')).to_have_count(0)
            expect(form.locator('.preview-pending')).to_be_visible()
            form.locator('button[type=submit]').click()
            expect(form.locator('.lexical-validation')).to_be_visible()
            assert fixture.dump() == before

            form.locator('[name=relations_resolution][value=ACCEPTED]').check()
            expect(form.locator('.lexical-preview[data-resolution=ACCEPTED]')).to_be_visible()
            form.locator('[name=relations_resolution][value=REJECTED]').check()
            expect(form.locator('.lexical-preview[data-resolution=ACCEPTED]')).to_be_hidden()
            expect(form.locator('.lexical-preview[data-resolution=REJECTED]')).to_be_visible()
            assert form.locator('[name=review_note]').evaluate('(input)=>input.required')
            form.locator('[name=relations_resolution][value=pending]').check()
            expect(form.locator('.lexical-preview:visible')).to_have_count(0)

            form.locator('[name=relations_resolution][value=ACCEPTED]').check()
            form.locator('[name=morphology_resolution][value=ACCEPTED]').check()
            form.locator('[name=decision][value=existing]').check()
            form.locator('[name=alternative_id]').select_option('1')
            expect(form.locator('.accept-group:visible')).to_have_count(0)
            expect(form.locator('.existing-group-warning:visible')).to_have_count(2)
            expect(form.locator('[value=pending]:checked')).to_have_count(2)
            assert 'ACCEPTED' not in form.evaluate('(form)=>JSON.stringify([...new FormData(form)])')
            form.locator('[name=decision][value=rejected]').check()
            expect(form.locator('.group-decision:visible')).to_have_count(0)
            payload = form.evaluate('(form)=>Object.fromEntries(new FormData(form))')
            assert 'relations_resolution' not in payload and 'morphology_resolution' not in payload

            form.locator('[name=decision][value=pending]').check()
            assert not form.locator('[name=review_note]').evaluate('(input)=>input.required')
            form.locator('button[type=submit]').click()
            expect(page.locator('.review-decision')).to_be_visible()
            assert fixture.dump() == before

            form = page.locator('.review-decision')
            form.locator('[name=decision][value=new]').check()
            form.locator('[name=relations_resolution][value=REJECTED]').check()
            form.locator('[name=morphology_resolution][value=ACCEPTED]').check()
            form.locator('[name=review_note]').fill('Rechazo explícito de relaciones')
            form.locator('button[type=submit]').click()
            page.goto(f'http://lexical.test/aportes/{sid}')
            expect(page.locator('.lexical-history')).to_contain_text('Creó una Alternative nueva')
            expect(page.locator('.lexical-history')).to_contain_text('Relaciones: rechazadas')
            expect(page.locator('.lexical-current')).to_be_visible()
            expect(page.get_by_text('NOTA ORIGINAL', exact=True)).to_be_visible()
            assert not errors, errors
            browser.close()
        print('PASS: browser controls, preview switching, pending no-op, group payloads and closed history')
    finally:
        fixture.tearDown()


if __name__ == '__main__':
    main()
