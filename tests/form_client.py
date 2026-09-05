"""Legacy route fixtures submit the preconditions from a freshly opened form.

Concurrency tests deliberately use the raw Flask client to retain old tokens.
"""
import re
from html import unescape


def hidden(page, name):
    match = re.search(r'name="' + name + r'" value="([^"]*)"', page)
    return unescape(match[1]) if match else ""


class FormClient:
    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        return getattr(self.client, name)

    def post(self, path, *args, **kwargs):
        data = kwargs.get("data")
        form_path = None
        if data is not None:
            if re.fullmatch(r"/(?:[^/]+/)?(ocurrencias|fuentes|conceptos)/\d+/actualizar", path):
                form_path = path.replace("/actualizar", "/editar")
            elif "/gramatica/aceptacion-inmediata/" in path:
                form_path = path.split("/aceptacion-inmediata/")[0]
            elif path.endswith("/video") and data.get("action") in ("replace", "retire"):
                form_path = path
            elif path.endswith("/gestionar") and data.get("action") == "morphology":
                form_path = path
        if form_path and "edit_token" not in data:
            data = data.copy()
            data["edit_token"] = hidden(self.client.get(form_path).get_data(as_text=True), "edit_token")
            kwargs["data"] = data
        return self.client.post(path, *args, **kwargs)
