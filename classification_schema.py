"""Phase 19A schema. Installation never reads or converts legacy classifications."""
import sqlite3

SEMANTIC_FIELDS = (
    'Actividades y acciones', 'Alimentación', 'Animales', 'Antropónimos',
    'Áreas de conocimiento', 'Calima', 'Cantidad y medición', 'Colores',
    'Comunicación y afines', 'Cortesía', 'Cualidades', 'Cuerpo humano',
    'Cultura sorda', 'Deporte', 'Educación', 'Expresiones de Tiempo',
    'Gobierno y sociedad', 'Hogar y vivienda', 'Lugares', 'Objetos y materiales',
    'Parentesco', 'Plantas', 'Profesiones y oficios', 'Relaciones espaciales',
    'Relaciones sociales', 'Religión', 'Salud', 'Sentimientos y emociones',
    'Sexualidad', 'Tecnología', 'Transporte', 'Vestuario', 'Otro',
)
KNOWLEDGE_AREAS = (
    'Astronomía', 'Matemáticas', 'Física', 'Química', 'Biología',
    'Ciencias de la Tierra', 'Ciencias Ambientales', 'Medicina y Ciencias de la Salud',
    'Ingeniería', 'Tecnología e Informática', 'Lingüística', 'Educación', 'Derecho',
    'Economía y Administración', 'Ciencias Sociales', 'Historia', 'Filosofía',
    'Literatura y Lenguas', 'Artes', 'Diseño y Arquitectura', 'Comunicación',
    'Ciencias Agrarias', 'Otro',
)
TABLES = ('collection', 'classification_system', 'classification_category',
          'collection_membership', 'concept_classification_revision',
          'submission_classification_proposal', 'submission_collection_proposal')

PAIR = """
    category_1_id INTEGER REFERENCES classification_category(category_id),
    category_2_id INTEGER REFERENCES classification_category(category_id),
    category_1_name TEXT, category_1_code TEXT,
    category_2_name TEXT, category_2_code TEXT,
"""
PAIR_CHECKS = """
    CHECK(category_2_id IS NULL OR category_1_id IS NOT NULL),
    CHECK(category_1_id IS NULL OR category_2_id IS NULL OR category_1_id != category_2_id),
    CHECK((category_1_id IS NULL AND category_1_name IS NULL AND category_1_code IS NULL)
       OR (category_1_id IS NOT NULL AND category_1_name IS NOT NULL AND category_1_code IS NOT NULL)),
    CHECK((category_2_id IS NULL AND category_2_name IS NULL AND category_2_code IS NULL)
       OR (category_2_id IS NOT NULL AND category_2_name IS NOT NULL AND category_2_code IS NOT NULL)),
    FOREIGN KEY(system_id,category_1_id) REFERENCES classification_category(system_id,category_id),
    FOREIGN KEY(system_id,category_2_id) REFERENCES classification_category(system_id,category_id)
"""
ACTOR = """
    collaborator_id INTEGER REFERENCES collaborator(collaborator_id),
    collaborator_name_snapshot TEXT,
    access_role TEXT NOT NULL CHECK(access_role IN ('reviewer','master')),
    resolution_id INTEGER REFERENCES submission_concept_resolution(submission_concept_resolution_id),
"""
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS collection (
    collection_id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL CHECK(length(trim(name))>0), name_key TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS classification_system (
    system_id INTEGER PRIMARY KEY, collection_id INTEGER REFERENCES collection(collection_id),
    code TEXT NOT NULL UNIQUE, name TEXT NOT NULL CHECK(length(trim(name))>0),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    CHECK((code='semantic-fields' AND collection_id IS NULL) OR
          (code!='semantic-fields' AND collection_id IS NOT NULL)),
    UNIQUE(collection_id,name)
);
CREATE TABLE IF NOT EXISTS classification_category (
    category_id INTEGER PRIMARY KEY, system_id INTEGER NOT NULL REFERENCES classification_system(system_id),
    code TEXT NOT NULL, name TEXT NOT NULL CHECK(length(trim(name))>0),
    name_key TEXT NOT NULL CHECK(name_key!='n/a'),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    display_order INTEGER NOT NULL DEFAULT 0,
    CHECK(lower(trim(name))!='n/a' AND lower(trim(code))!='n/a'),
    UNIQUE(system_id,code), UNIQUE(system_id,name_key), UNIQUE(system_id,category_id)
);
CREATE TABLE IF NOT EXISTS collection_membership (
    membership_id INTEGER PRIMARY KEY,
    concept_id INTEGER NOT NULL REFERENCES concept(concept_id),
    collection_id INTEGER NOT NULL REFERENCES collection(collection_id),
    collection_name_snapshot TEXT NOT NULL, collection_code_snapshot TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, ended_at TEXT,
    {ACTOR}
    ended_by_collaborator_id INTEGER REFERENCES collaborator(collaborator_id),
    ended_by_name_snapshot TEXT, ended_access_role TEXT CHECK(ended_access_role IN ('reviewer','master')),
    ended_resolution_id INTEGER REFERENCES submission_concept_resolution(submission_concept_resolution_id),
    CHECK(ended_at IS NULL OR ended_at>=started_at),
    UNIQUE(membership_id,concept_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_collection_membership
    ON collection_membership(concept_id,collection_id) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS concept_classification_revision (
    revision_id INTEGER PRIMARY KEY,
    concept_id INTEGER NOT NULL REFERENCES concept(concept_id),
    system_id INTEGER NOT NULL REFERENCES classification_system(system_id),
    system_name_snapshot TEXT NOT NULL, system_code_snapshot TEXT NOT NULL,
    membership_id INTEGER REFERENCES collection_membership(membership_id),
    {PAIR}
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, ended_at TEXT,
    supersedes_revision_id INTEGER REFERENCES concept_classification_revision(revision_id),
    semantic_changed INTEGER NOT NULL CHECK(semantic_changed IN (0,1)),
    {ACTOR}
    ended_by_collaborator_id INTEGER REFERENCES collaborator(collaborator_id),
    ended_by_name_snapshot TEXT, ended_access_role TEXT CHECK(ended_access_role IN ('reviewer','master')),
    ended_resolution_id INTEGER REFERENCES submission_concept_resolution(submission_concept_resolution_id),
    CHECK(ended_at IS NULL OR ended_at>=started_at),
    FOREIGN KEY(membership_id,concept_id) REFERENCES collection_membership(membership_id,concept_id),
    {PAIR_CHECKS}
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_concept_classification
    ON concept_classification_revision(concept_id,system_id) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS submission_classification_proposal (
    submission_id INTEGER NOT NULL REFERENCES submission(submission_id),
    system_id INTEGER NOT NULL REFERENCES classification_system(system_id),
    system_name_snapshot TEXT NOT NULL, system_code_snapshot TEXT NOT NULL,
    {PAIR}
    base_concept_id INTEGER REFERENCES concept(concept_id),
    base_state_json TEXT NOT NULL,
    PRIMARY KEY(submission_id,system_id),
    {PAIR_CHECKS}
);
CREATE TABLE IF NOT EXISTS submission_collection_proposal (
    submission_id INTEGER NOT NULL REFERENCES submission(submission_id),
    collection_id INTEGER NOT NULL REFERENCES collection(collection_id),
    action TEXT NOT NULL CHECK(action IN ('join','leave')),
    collection_name_snapshot TEXT NOT NULL, collection_code_snapshot TEXT NOT NULL,
    base_concept_id INTEGER REFERENCES concept(concept_id), base_state_json TEXT NOT NULL,
    PRIMARY KEY(submission_id,collection_id)
);
"""


def statements(script):
    pending = ''
    for line in script.splitlines(True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ''
    if pending.strip():
        raise ValueError('Incomplete schema statement')


def install(db):
    for sql in statements(SCHEMA):
        db.execute(sql)
    columns = {r[1] for r in db.execute('PRAGMA table_info(submission_concept_resolution)')}
    if 'classification_decision_json' not in columns:
        db.execute('ALTER TABLE submission_concept_resolution ADD COLUMN classification_decision_json TEXT CHECK(classification_decision_json IS NULL OR json_valid(classification_decision_json))')
    db.execute("""CREATE TRIGGER IF NOT EXISTS immutable_classification_decision
        BEFORE UPDATE OF classification_decision_json ON submission_concept_resolution
        WHEN OLD.classification_decision_json IS NOT NULL AND NEW.classification_decision_json IS NOT OLD.classification_decision_json
        BEGIN SELECT RAISE(ABORT,'Classification decision snapshots are immutable'); END""")
    for table in ('collection', 'classification_system', 'classification_category'):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS no_delete_{table} BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT,'Deactivate controlled catalog entries instead of deleting'); END""")
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS stable_code_{table} BEFORE UPDATE OF code ON {table}
            WHEN NEW.code!=OLD.code BEGIN SELECT RAISE(ABORT,'Catalog codes are stable'); END""")
    for table, column in (('classification_system', 'collection_id'), ('classification_category', 'system_id')):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS stable_scope_{table} BEFORE UPDATE OF {column} ON {table}
            WHEN NEW.{column} IS NOT OLD.{column} BEGIN SELECT RAISE(ABORT,'Catalog scope is immutable'); END""")
    for table in ('collection_membership', 'concept_classification_revision'):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS no_delete_{table} BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT,'History cannot be deleted'); END""")
        immutable = [r[1] for r in db.execute(f'PRAGMA table_info({table})')
                     if not r[1].startswith('ended_')]
        conditions = ' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in immutable)
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{table} BEFORE UPDATE ON {table}
            WHEN OLD.ended_at IS NOT NULL OR NEW.ended_at IS NULL OR {conditions}
            BEGIN SELECT RAISE(ABORT,'History can only be closed once'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS classification_scope_insert
        BEFORE INSERT ON concept_classification_revision WHEN
        (NEW.resolution_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM submission_concept_resolution d WHERE d.submission_concept_resolution_id=NEW.resolution_id
          AND d.concept_id=NEW.concept_id)) OR
        EXISTS(SELECT 1 FROM classification_system s WHERE s.system_id=NEW.system_id AND
          ((s.collection_id IS NULL AND NEW.membership_id IS NOT NULL) OR
           (s.collection_id IS NOT NULL AND NOT EXISTS(
             SELECT 1 FROM collection_membership m WHERE m.membership_id=NEW.membership_id
             AND m.concept_id=NEW.concept_id AND m.collection_id=s.collection_id
             AND m.ended_at IS NULL)))) OR
        (NEW.supersedes_revision_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM concept_classification_revision r WHERE r.revision_id=NEW.supersedes_revision_id
          AND r.concept_id=NEW.concept_id AND r.system_id=NEW.system_id
          AND r.membership_id IS NEW.membership_id AND r.ended_at IS NOT NULL))
        BEGIN SELECT RAISE(ABORT,'Invalid classification scope or revision chain'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS close_membership_classifications_first
        BEFORE UPDATE OF ended_at ON collection_membership WHEN NEW.ended_at IS NOT NULL
        AND EXISTS(SELECT 1 FROM concept_classification_revision WHERE membership_id=OLD.membership_id AND ended_at IS NULL)
        BEGIN SELECT RAISE(ABORT,'Close collection classifications first'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS membership_resolution_scope
        BEFORE INSERT ON collection_membership WHEN NEW.resolution_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM submission_concept_resolution d WHERE d.submission_concept_resolution_id=NEW.resolution_id
          AND d.concept_id=NEW.concept_id)
        BEGIN SELECT RAISE(ABORT,'Membership decision belongs to another Concept'); END""")
    for table in ('submission_classification_proposal', 'submission_collection_proposal'):
        for operation in ('UPDATE', 'DELETE'):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()}
                BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Original proposals are immutable'); END""")
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS pending_{table} BEFORE INSERT ON {table}
            WHEN NOT EXISTS(SELECT 1 FROM submission WHERE submission_id=NEW.submission_id
              AND submission_type='ALTERNATIVE' AND status='pending')
            BEGIN SELECT RAISE(ABORT,'Classification proposal requires pending lexical submission'); END""")
    seed(db)


def seed(db):
    db.execute("INSERT OR IGNORE INTO collection(code,name,name_key) VALUES('academic-vocabulary','Vocabulario Académico','vocabulario académico')")
    cid = db.execute("SELECT collection_id FROM collection WHERE code='academic-vocabulary'").fetchone()[0]
    for code, name, collection, names, prefix in (
        ('semantic-fields', 'Campos Semánticos', None, SEMANTIC_FIELDS, 'SF'),
        ('knowledge-areas', 'Área de Conocimiento', cid, KNOWLEDGE_AREAS, 'KA'),
    ):
        db.execute('INSERT OR IGNORE INTO classification_system(code,name,collection_id) VALUES(?,?,?)', (code,name,collection))
        sid = db.execute('SELECT system_id FROM classification_system WHERE code=?', (code,)).fetchone()[0]
        for index, label in enumerate(names, 1):
            db.execute('INSERT OR IGNORE INTO classification_category(system_id,code,name,name_key,display_order) VALUES(?,?,?,?,?)',
                       (sid, f'{prefix}-{index:02}', label, label.casefold(), index))
