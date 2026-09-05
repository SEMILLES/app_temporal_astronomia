"""Minimal runtime mode shared by the application and database selection."""
import os


def is_production():
    mode = os.environ.get("LESICO_ENV", "development").strip().lower()
    if mode not in {"development", "production"}:
        raise RuntimeError("LESICO_ENV debe ser development o production")
    return mode == "production"
