"""Database invariants for retired sources (shared by fresh schema and 019)."""

TRIGGERS = {
    "occurrence_active_source_insert": """CREATE TRIGGER IF NOT EXISTS occurrence_active_source_insert
        BEFORE INSERT ON occurrence WHEN EXISTS
        (SELECT 1 FROM source WHERE source_id=NEW.source_id AND retired_at IS NOT NULL)
        BEGIN SELECT RAISE(ABORT, 'La fuente esta retirada'); END""",
    "occurrence_active_source_update": """CREATE TRIGGER IF NOT EXISTS occurrence_active_source_update
        BEFORE UPDATE OF source_id ON occurrence WHEN EXISTS
        (SELECT 1 FROM source WHERE source_id=NEW.source_id AND retired_at IS NOT NULL)
        BEGIN SELECT RAISE(ABORT, 'La fuente esta retirada'); END""",
    "source_retire_empty": """CREATE TRIGGER IF NOT EXISTS source_retire_empty
        BEFORE UPDATE OF retired_at ON source WHEN NEW.retired_at IS NOT NULL AND EXISTS
        (SELECT 1 FROM occurrence WHERE source_id=NEW.source_id)
        BEGIN SELECT RAISE(ABORT, 'Migre todas las occurrences antes de retirar la fuente'); END""",
}


def install(connection):
    for statement in TRIGGERS.values():
        connection.execute(statement)
