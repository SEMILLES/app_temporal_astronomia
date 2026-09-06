"""Explicit instance identification, independent of access roles and server mode."""
import re

from flask import render_template


def install_instance_presentation(app):
    @app.after_request
    def identify_testing_instance(response):
        if (app.config.get("LESICO_INSTANCE_MODE") != "testing"
                or response.mimetype != "text/html"
                or response.is_streamed or response.direct_passthrough):
            return response
        html = response.get_data(as_text=True)
        body = re.search(r"<body\b[^>]*>", html, re.IGNORECASE)
        if body is None:
            return response
        banner = render_template("_instance_banner.html")
        html = html[:body.end()] + banner + html[body.end():]
        html = re.sub(r"(<title\b[^>]*>)", r"\1[PRUEBAS] ", html,
                      count=1, flags=re.IGNORECASE)
        response.set_data(html)
        return response
