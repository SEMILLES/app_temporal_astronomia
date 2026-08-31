import argparse
import os
from pathlib import Path


def selected_database(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    args = parser.parse_args(argv)
    configured = (os.environ.get("LESICO_DATABASE_PATH") or "").strip()
    database = args.database or (Path(configured) if configured else None)
    if database is None:
        parser.error(
            "indique --database o configure LESICO_DATABASE_PATH; "
            "no se migrará lesico_prototipo.db implícitamente"
        )
    return database.expanduser().resolve()


def run_migration_cli(migrate, migration_number, argv=None):
    database = selected_database(argv)
    backup = database.with_name(
        f"{database.stem}.pre_migration_{migration_number}{database.suffix}"
    )
    changed = migrate(database, backup)
    if changed:
        print(f"Migration {migration_number} aplicada a {database}.")
        print(f"Backup conservado en: {backup}")
    else:
        print(f"Migration {migration_number} ya estaba aplicada a {database}.")
    return changed
