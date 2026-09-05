"""Production WSGI target: wsgi:application."""
import os

os.environ.setdefault("LESICO_ENV", "production")

from runtime_config import is_production

if not is_production():
    raise RuntimeError("El entrypoint WSGI requiere LESICO_ENV=production")

from app import app as application
