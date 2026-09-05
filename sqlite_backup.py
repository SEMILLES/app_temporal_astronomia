"""Small SQLite backup/verification/restore CLI; no application startup imports."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid


class BackupError(RuntimeError):
    pass


# Operational minimum, not a replacement for application startup validation.
MINIMUM_SCHEMA = {
    "concept": {"concept_id", "preferred_label"},
    "alternative": {"alternative_id", "concept_id"},
    "occurrence": {"occurrence_id", "source_id"},
    "source": {"source_id", "source_name"},
    "assignment": {"occurrence_id", "alternative_id", "is_current"},
    "occurrence_concept_reference": {"occurrence_id", "concept_id", "is_current"},
    "alternative_morphology": {"alternative_id", "is_current"},
    "alternative_relation": {"is_current"},
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_connection(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise BackupError("Archivo SQLite inexistente")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    return connection


def inspect_database(path):
    with closing(read_connection(path)) as connection:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise BackupError("integrity_check fallido")
        # SQLite 3.42 on this runtime omits CHECK violations with mode=ro.
        # Validate a writable in-memory snapshot as well, never the artifact.
        with closing(sqlite3.connect(":memory:")) as validation:
            connection.backup(validation)
            if [row[0] for row in validation.execute("PRAGMA integrity_check")] != ["ok"]:
                raise BackupError("integrity_check fallido")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupError("foreign_key_check fallido")
        for table, required in MINIMUM_SCHEMA.items():
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not required <= columns:
                raise BackupError(f"Schema mínimo incompatible: {table}")
        return {
            "integrity_check": "ok", "foreign_key_check": 0,
            "schema": {
                "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
                "schema_version": connection.execute("PRAGMA schema_version").fetchone()[0],
                "minimum_schema": 1,
                "table_count": connection.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0],
            },
        }


def manifest_path(path):
    return Path(str(path) + ".manifest.json")


def git_commit():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5, check=True,
        )
        value = result.stdout.strip()
        return value if re.fullmatch(r"[0-9a-f]{40,64}", value) else None
    except (OSError, subprocess.SubprocessError):
        return None


def temporary_file(directory):
    descriptor, name = tempfile.mkstemp(prefix=".lesico-", suffix=".partial", dir=directory)
    os.close(descriptor)
    return Path(name)


def verify_backup(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise BackupError("Backup inexistente")
    try:
        manifest = json.loads(manifest_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BackupError("Manifest ausente, ilegible o inválido") from error
    if not isinstance(manifest, dict) or manifest.get("filename") != path.name:
        raise BackupError("Manifest incorrecto: filename")
    if manifest.get("sha256") != sha256(path):
        raise BackupError("Checksum SHA256 incorrecto")
    if manifest.get("size_bytes") != path.stat().st_size:
        raise BackupError("Manifest incorrecto: tamaño")
    checks = inspect_database(path)
    if any(manifest.get(key) != value for key, value in checks.items()):
        raise BackupError("Manifest incorrecto: validación/schema")
    if not isinstance(manifest.get("reason"), str) or not isinstance(manifest.get("timestamp_utc"), str):
        raise BackupError("Manifest incompleto")
    return manifest


def create_backup(source, directory, reason="manual", timeout=60):
    if not source or not str(source).strip():
        raise BackupError("Indique --source o LESICO_DATABASE_PATH explícita")
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise BackupError("DB origen inexistente")
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(directory).free < source.stat().st_size:
        raise BackupError("Espacio insuficiente para backup")
    reason = re.sub(r"[^a-zA-Z0-9_-]+", "-", reason).strip("-")[:60] or "manual"
    timestamp = datetime.now(timezone.utc)
    name = f"lesico_astronomia_{timestamp:%Y-%m-%dT%H%M%SZ}_{reason}_{uuid.uuid4().hex[:8]}.db"
    target = directory / name
    temporary = temporary_file(directory)
    metadata = None
    published = False
    try:
        deadline = time.monotonic() + timeout

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise BackupError("Backup interrumpido: tiempo máximo excedido; reintente")

        with closing(read_connection(source)) as origin, closing(sqlite3.connect(temporary)) as copy:
            origin.backup(copy, pages=256, progress=progress, sleep=0.1)
        checks = inspect_database(temporary)
        manifest = {
            "filename": target.name, "timestamp_utc": timestamp.isoformat().replace("+00:00", "Z"),
            "sha256": sha256(temporary), "size_bytes": temporary.stat().st_size,
            "reason": reason, "git_commit": git_commit(), **checks,
        }
        metadata = temporary_file(directory)
        with metadata.open("w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Exclusive publication: existing paths are never replaced.
        os.link(temporary, target)
        published = True
        os.link(metadata, manifest_path(target))
        return target
    except BaseException:
        if published:
            target.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        if metadata is not None:
            metadata.unlink(missing_ok=True)


def restore_backup(backup, destination):
    backup = Path(backup).expanduser().resolve()
    manifest = verify_backup(backup)
    destination = Path(destination).expanduser().absolute()
    if os.path.lexists(destination):
        raise BackupError("Destino restore ya existente; no se permite overwrite")
    if not destination.parent.is_dir():
        raise BackupError("El directorio destino debe existir")
    if shutil.disk_usage(destination.parent).free < manifest["size_bytes"]:
        raise BackupError("Espacio insuficiente para restore")
    temporary = temporary_file(destination.parent)
    try:
        # A verified, closed backup is a static artifact; preserve its exact bytes.
        with backup.open("rb") as origin, temporary.open("wb") as target:
            shutil.copyfileobj(origin, target)
            target.flush()
            os.fsync(target.fileno())
        if sha256(temporary) != manifest["sha256"]:
            raise BackupError("Checksum incorrecto durante restore")
        inspect_database(temporary)
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--source", default=os.environ.get("LESICO_DATABASE_PATH"))
    backup.add_argument("--directory", required=True)
    backup.add_argument("--reason", default="manual")
    verify = commands.add_parser("verify")
    verify.add_argument("backup")
    restore = commands.add_parser("restore")
    restore.add_argument("backup")
    restore.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.source, args.directory, args.reason)
        elif args.command == "verify":
            result = verify_backup(args.backup)["filename"]
        else:
            result = restore_backup(args.backup, args.destination).name
        print(f"OK: {Path(result).name}")
        return 0
    except BackupError as error:
        print(f"ERROR: {error}")
    except sqlite3.Error:
        print("ERROR: SQLite ilegible, corrupta, bloqueada o sin espacio; operación fallida")
    except OSError:
        print("ERROR: fallo de archivo; revise permisos, espacio, destino existente y soporte de hard links")
    except KeyboardInterrupt:
        print("ERROR: operación interrumpida; no considerar válido ningún archivo sin manifest verificable")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
