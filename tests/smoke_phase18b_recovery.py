"""Explicit recovery drill against a disposable derivative of the local baseline."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlite_backup import create_backup, inspect_database, read_connection, restore_backup, sha256, verify_backup


def main():
    protected = {
        ROOT / "lesico_prototipo.db": "DCCD19EDEAF96725951F194C66014A2DCE429F18B8DB285FB0CF8B34C1C93292",
        ROOT / "import_inputs/astronomia/lesico_astronomia_working.db": "25AFFFEE43476569CFA894C5CAD8DDC0A517DD4A988FE9B97E956A34E32FF8C0",
        ROOT / "import_inputs/astronomia/lesico_astronomia_working_baseline_post_fase17a_2026-09-05.db": "25AFFFEE43476569CFA894C5CAD8DDC0A517DD4A988FE9B97E956A34E32FF8C0",
        ROOT / "import_inputs/astronomia/lesico_astronomia_candidate.db": "3F7540A916AEC8DE91B1FEF01DE4A10D375304C915A6C1C675B7568FE2C78158",
        ROOT / "import_inputs/astronomia/lesico_astronomia_write_test.db": "B13DB525141341CEB7DF784ECF410146C1E203676E14A1F294E46E83B5B74D36",
    }
    for path, expected in protected.items():
        assert sha256(path).upper() == expected, path.name
    baseline = ROOT / "import_inputs/astronomia/lesico_astronomia_working_baseline_post_fase17a_2026-09-05.db"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "disposable.db"
        with closing(read_connection(baseline)) as origin, closing(sqlite3.connect(source)) as target:
            origin.backup(target)
        artifact = create_backup(source, root / "backups", "recovery-drill")
        manifest = verify_backup(artifact)
        restored = restore_backup(artifact, root / "restored.db")
        expected_counts = {
            "concept": 150, "alternative": 228, "occurrence": 253, "source": 44,
            "assignment": 253, "occurrence_concept_reference": 253,
            "alternative_morphology": 228, "alternative_relation": 19,
        }
        with closing(read_connection(restored)) as connection:
            for table, expected in expected_counts.items():
                current = " WHERE is_current=1" if table in {
                    "assignment", "occurrence_concept_reference", "alternative_morphology", "alternative_relation"
                } else ""
                count = connection.execute(f'SELECT count(*) FROM "{table}"{current}').fetchone()[0]
                assert count == expected, (table, count, expected)
                print(f"{table}{current}: {count}")
        assert sha256(restored) == manifest["sha256"]
        checks = inspect_database(restored)
        print(f"integrity: {checks['integrity_check']}; FK: {checks['foreign_key_check']}; SHA256: identical")
    for path, expected in protected.items():
        assert sha256(path).upper() == expected, path.name
    print("Protected hashes: 5/5 unchanged")


if __name__ == "__main__":
    main()
