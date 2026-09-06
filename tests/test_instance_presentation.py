import hashlib
import unittest

from flask import Flask, Response

from instance_presentation import install_instance_presentation
import test_production_readiness as readiness


class InstanceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = readiness.ProductionReadinessTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.env.update(LESICO_ANALYST_ROUTE="ana", LESICO_REVIEWER_ROUTE="rev",
                                LESICO_MASTER_ROUTE="mas")

    def check_mode(self, mode):
        if mode is not None:
            self.fixture.env["LESICO_INSTANCE_MODE"] = mode
        before = hashlib.sha256(self.fixture.path.read_bytes()).hexdigest()
        self.fixture.run_code(f'''
import app
import database
assert not app.app.debug
assert app.PRODUCTION
assert app.app.config["LESICO_INSTANCE_MODE"] == {mode or 'production'!r}
# Fail any attempted write during rendering, including rolled-back writes.
original_connect = database.sqlite3.connect
def read_only_connect(*args, **kwargs):
    connection = original_connect(*args, **kwargs)
    connection.execute("PRAGMA query_only=ON")
    return connection
database.sqlite3.connect = read_only_connect
client = app.app.test_client()
for path in ("/ana/trabajo", "/rev/trabajo", "/mas/trabajo",
             "/ana/catalogo-interno", "/rev/catalogo-interno",
             "/mas/catalogo-interno", "/catalogo",
             "/rev/aportes/pendientes", "/mas/colaboradores"):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    html = response.get_data(as_text=True)
    expected = {mode == 'testing'!r}
    assert ("ENTORNO DE PRUEBAS" in html) == expected, path
    assert ("[PRUEBAS]" in html) == expected, path
    if expected:
        assert "Los cambios realizados aquí pueden eliminarse al restaurar el entorno." in html
        assert "<title>[PRUEBAS] " in html
        assert html.count('id="lesico-testing-banner"') == 1
for path in ("/trabajo", "/catalogo-interno", "/invalid/trabajo",
             "/ana/aportes/pendientes", "/rev/colaboradores"):
    assert client.get(path).status_code == 404, path
''')
        self.assertEqual(before, hashlib.sha256(self.fixture.path.read_bytes()).hexdigest())

    def test_testing(self):
        self.check_mode("testing")

    def test_production(self):
        self.check_mode("production")

    def test_absent(self):
        self.check_mode(None)

    def test_unknown_value_is_conservative(self):
        self.check_mode("test")


class InstanceResponseTests(unittest.TestCase):
    def test_response_boundaries_and_unchanged_production_html(self):
        app = Flask(__name__, template_folder=str(readiness.ROOT / "templates"))
        install_instance_presentation(app)
        documents = {
            "/page": ("<html><body>Sin título</body></html>", "text/html"),
            "/fragment": ("<p>Fragmento</p>", "text/html"),
            "/json": ('{"body":"<body>"}', "application/json"),
        }
        for path, (body, mimetype) in documents.items():
            app.add_url_rule(path, path, lambda b=body, m=mimetype: Response(b, mimetype=m))
        client = app.test_client()
        for mode in (None, "production", "testing"):
            app.config["LESICO_INSTANCE_MODE"] = mode
            for path, (body, _) in documents.items():
                response = client.get(path)
                html = response.get_data(as_text=True)
                if mode == "testing" and path == "/page":
                    self.assertIn("ENTORNO DE PRUEBAS", html)
                    self.assertNotIn("<title>", html)
                else:
                    self.assertEqual(html, body)
                self.assertEqual(response.content_length, len(response.data))
