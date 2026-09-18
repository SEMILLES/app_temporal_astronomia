"""Offline LeSiCo integrity checks. No application, migration or write imports."""
import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

from alternative_nomenclature import canonical_concept_state, connected_components
from lexical_simulation import calculate_concept_nomenclature, validate_concept_nomenclature
from phonological_parameters import PHONOLOGICAL_PARAMETERS
from youtube_media import parse_youtube_url
from usage_profile import FIELDS as USAGE_FIELDS, normalize as normalize_usage


class AuditAccessError(Exception):
    pass


def readonly_authorizer(action, arg1, arg2, database, trigger):
    """Allow only reads, safe introspection, functions and read transactions."""
    if action == sqlite3.SQLITE_PRAGMA:
        return (sqlite3.SQLITE_OK if arg1.lower() in {
            'table_info', 'foreign_key_list', 'foreign_key_check', 'integrity_check'
        } else sqlite3.SQLITE_DENY)
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if arg2 in {'load_extension', 'writefile'} else sqlite3.SQLITE_OK
    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_TRANSACTION and arg1 in {'BEGIN', 'ROLLBACK'}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@contextmanager
def open_readonly(path):
    """Require an offline rollback-mode file; never recover journals or ignore WAL."""
    path = Path(path).expanduser().resolve(strict=True)
    with path.open('rb') as stream:
        header = stream.read(100)
    if header[:16] != b'SQLite format 3\x00':
        raise AuditAccessError('Not a SQLite database')
    if header[18:20] != b'\x01\x01' or any(
        Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')
    ):
        raise AuditAccessError('Use an offline, checkpointed rollback-mode copy without sidecars')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.set_authorizer(readonly_authorizer)
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        yield connection
        if connection.total_changes != 0:
            raise AuditAccessError('Unexpected changes on audit connection')
    finally:
        connection.close()


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


# Columns used by semantic checks. Missing coverage fails closed, including preflight.
REQUIRED = {
    'alternative_usage_profile': 'profile_id alternative_id frequency_impressionistic geographic_zone_general geographic_zone_detail age_group socioeconomic_group popular_etymology iconicity_notes register_notes semantic_pragmatic_nuance spanish_relations additional_notes created_at updated_at',
    'collection': 'collection_id code name name_key active',
    'classification_system': 'system_id collection_id code name active',
    'classification_category': 'category_id system_id code name name_key active',
    'collection_membership': 'membership_id concept_id collection_id started_at ended_at resolution_id ended_resolution_id',
    'concept_classification_revision': 'revision_id concept_id system_id membership_id category_1_id category_2_id category_1_name category_1_code category_2_name category_2_code started_at ended_at supersedes_revision_id resolution_id ended_resolution_id',
    'submission_classification_proposal': 'submission_id system_id category_1_id category_2_id category_1_name category_1_code category_2_name category_2_code base_state_json',
    'submission_collection_proposal': 'submission_id collection_id action base_state_json',
    'source': 'source_id start_year end_year end_year_status source_name source_reference retired_at',
    'concept': 'concept_id',
    'alternative': 'alternative_id concept_id working_label created_at retired_at',
    'occurrence': 'occurrence_id source_id occurrence_year',
    'assignment': 'assignment_id occurrence_id alternative_id is_current',
    'alternative_relation': 'alternative_relation_id alternative_low_id alternative_high_id phonological_parameter is_current',
    'submission': 'submission_id occurrence_id submission_type status resolution resolved_at',
    'alternative_submission': 'submission_id resolved_alternative_id is_legacy',
    'grammar_submission': 'submission_id',
    'submission_concept_resolution': 'submission_concept_resolution_id submission_id concept_id is_current classification_decision_json',
    'submission_lexical_decision': 'submission_id concept_resolution_id decision_action resolved_alternative_id assignment_result_id concept_id_at_decision',
    'occurrence_grammar': 'occurrence_grammar_id occurrence_id is_current created_from_submission_id',
    'alternative_morphology': 'alternative_morphology_id alternative_id is_current',
    'alternative_component': 'alternative_component_id alternative_morphology_id component_alternative_id',
    'media_asset': 'media_asset_id file_size storage_key mime_type origin_kind',
    'occurrence_media': 'occurrence_id media_asset_id',
    'alternative_media': 'alternative_media_id alternative_id media_asset_id role is_current retired_at',
    'occurrence_concept_reference': 'occurrence_id concept_id is_current',
    'renumber_event': 'renumber_event_id concept_id',
    'renumber_change': 'renumber_change_id renumber_event_id alternative_id',
    'activity_event': 'activity_event_id entity_type entity_id',
    'catalog_publication': 'publication_id version_number snapshot_json snapshot_sha256 change_summary_json',
    'concept_proposal': 'concept_proposal_id status resolved_concept_id',
    'source_revision': 'source_revision_id source_id',
    'source_systematization': 'source_systematization_id source_id',
    'occurrence_revision': 'occurrence_revision_id occurrence_id source_id',
    'occurrence_draft': 'draft_id source_id',
    'alternative_submission_relation': 'alternative_submission_relation_id submission_id',
    'alternative_submission_morphology': 'submission_id',
    'alternative_submission_component': 'component_id submission_id',
    'collaborator': 'collaborator_id',
    'conflict': 'conflict_id',
    'conflict_subject': 'conflict_subject_id conflict_id',
    'conflict_resolution_attempt': 'conflict_resolution_attempt_id conflict_id',
    'publication_open_conflict': 'publication_id conflict_id',
    'application_setting': 'setting_key',
}

PERSISTENT_ACTIVITY_ENTITIES = {
    'collection': ('collection','collection_id'),
    'classification_system': ('classification_system','system_id'),
    'classification_category': ('classification_category','category_id'),
    'application_setting': ('application_setting', 'setting_key'),
    'alternative': ('alternative', 'alternative_id'),
    'alternative_relation': ('alternative_relation', 'alternative_relation_id'),
    'catalog_publication': ('catalog_publication', 'publication_id'),
    'collaborator': ('collaborator', 'collaborator_id'),
    'concept': ('concept', 'concept_id'),
    'concept_proposal': ('concept_proposal', 'concept_proposal_id'),
    'conflict': ('conflict', 'conflict_id'),
    'occurrence': ('occurrence', 'occurrence_id'),
    'renumber_event': ('renumber_event', 'renumber_event_id'),
    'source': ('source', 'source_id'),
    'submission': ('submission', 'submission_id'),
}
EPHEMERAL_ACTIVITY_ENTITIES = {'occurrence_draft'}


class Auditor:
    def __init__(self, connection, preflight=False):
        self.db = connection
        self.preflight = preflight
        self.results = []
        self.columns = {
            row[0]: {c[1] for c in connection.execute('PRAGMA table_info(' + quote(row[0]) + ')')}
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }

    def record(self, code, details=(), severity='FAIL'):
        """One result per executed rule, exact count and at most five examples."""
        count, examples = 0, []
        for detail in details:
            count += 1
            if len(examples) < 5:
                examples.append(dict(detail))
        self.results.append(dict(code=code, status=severity if count else 'PASS',
                                 count=count, examples=examples))

    def query(self, code, sql, severity='FAIL'):
        self.record(code, self.db.execute(sql), severity)

    def references(self):
        self.record('FOREIGN_KEY_VIOLATION', (
            dict(table=r[0], rowid=r[1], parent=r[2], foreign_key=r[3])
            for r in self.db.execute('PRAGMA foreign_key_check')))
        # Check conventional IDs even if a restored schema has lost its FK clauses.
        targets = {t + '_id': (t, t + '_id') for t, cols in self.columns.items() if t + '_id' in cols}
        targets['publication_id'] = ('catalog_publication', 'publication_id')
        aliases = {
            'alternative_low_id': 'alternative_id', 'alternative_high_id': 'alternative_id',
            'resolved_alternative_id': 'alternative_id', 'proposed_existing_alternative_id': 'alternative_id',
            'target_alternative_id': 'alternative_id', 'component_alternative_id': 'alternative_id',
            'reference_concept_id': 'concept_id', 'concept_id_at_decision': 'concept_id',
            'resolved_concept_id': 'concept_id', 'reference_concept_proposal_id': 'concept_proposal_id',
            'concept_resolution_id': 'submission_concept_resolution_id',
            'assignment_before_id': 'assignment_id', 'assignment_result_id': 'assignment_id',
            'morphology_result_id': 'alternative_morphology_id',
            'target_submission_id': 'submission_id', 'created_from_submission_id': 'submission_id',
        }
        for table, columns in sorted(self.columns.items()):
            for column in sorted(columns):
                target = targets.get(aliases.get(column, column.removeprefix('supersedes_')))
                if not target or (table == target[0] and column == target[1]):
                    continue
                parent, key = target
                self.query('REFERENCE:' + table + '.' + column,
                    f'SELECT c.rowid AS rowid,c.{quote(column)} AS missing_id FROM {quote(table)} c '
                    f'LEFT JOIN {quote(parent)} p ON p.{quote(key)}=c.{quote(column)} '
                    f'WHERE c.{quote(column)} IS NOT NULL AND p.{quote(key)} IS NULL')
        # Renumber events describe membership at the time of the event. Moves
        # preserve alternative IDs and history; current membership cannot prove
        # historical membership. The reference checks above still require the
        # event, concept and alternative to exist.

    def relations(self):
        base = '''SELECT r.*,a.concept_id AS concept_a,b.concept_id AS concept_b
            FROM alternative_relation r LEFT JOIN alternative a ON a.alternative_id=r.alternative_low_id
            LEFT JOIN alternative b ON b.alternative_id=r.alternative_high_id WHERE r.is_current=1 AND '''
        for code, predicate in {
            'ORPHAN_RELATION': 'a.alternative_id IS NULL OR b.alternative_id IS NULL',
            'RETIRED_RELATION_ENDPOINT': 'a.retired_at IS NOT NULL OR b.retired_at IS NOT NULL',
            'CROSS_CONCEPT_RELATION': 'a.concept_id!=b.concept_id',
            'SELF_RELATION': 'r.alternative_low_id=r.alternative_high_id',
            'REVERSED_RELATION_PAIR': 'r.alternative_low_id>r.alternative_high_id',
        }.items():
            self.query(code, base + '(' + predicate + ')')
        self.query('DUPLICATE_CURRENT_RELATION', '''SELECT min(alternative_low_id,alternative_high_id) AS a,
            max(alternative_low_id,alternative_high_id) AS b,phonological_parameter,count(*) AS n
            FROM alternative_relation WHERE is_current=1 GROUP BY a,b,phonological_parameter HAVING count(*)>1''')
        self.record('UNKNOWN_RELATION_PARAMETER', (dict(r) for r in self.db.execute(
            'SELECT alternative_relation_id,phonological_parameter FROM alternative_relation WHERE is_current=1')
            if r['phonological_parameter'] not in PHONOLOGICAL_PARAMETERS))
        concepts = dict(self.db.execute('SELECT alternative_id,concept_id FROM alternative'))
        edges = [tuple(r) for r in self.db.execute('SELECT alternative_low_id,alternative_high_id FROM alternative_relation WHERE is_current=1')]
        self.record('CROSS_CONCEPT_COMPONENT', (
            dict(alternative_ids=sorted(component), concept_ids=sorted({concepts[a] for a in component}))
            for component in sorted(connected_components(concepts, edges), key=lambda c: min(c))
            if len({concepts[a] for a in component}) > 1))

    def current_states(self):
        for table, key, predicate in [
            ('assignment', 'occurrence_id', 'is_current=1'),
            ('occurrence_grammar', 'occurrence_id', 'is_current=1'),
            ('occurrence_concept_reference', 'occurrence_id', 'is_current=1'),
            ('alternative_morphology', 'alternative_id', 'is_current=1'),
            ('submission_concept_resolution', 'submission_id', 'is_current=1'),
            ('alternative_media', 'alternative_id', "is_current=1 AND role='catalog_video'"),
            ('submission', 'occurrence_id,submission_type', "status='pending'"),
        ]:
            self.query('MULTIPLE_CURRENT:' + table, f'SELECT {key},count(*) AS n FROM {table} WHERE {predicate} GROUP BY {key} HAVING count(*)>1')
        self.query('RETIRED_ASSIGNMENT_TARGET', '''SELECT a.assignment_id,a.alternative_id FROM assignment a
            JOIN alternative b USING(alternative_id) WHERE a.is_current=1 AND b.retired_at IS NOT NULL''')
        self.query('MEDIA_CURRENT_STATE', '''SELECT alternative_media_id FROM alternative_media
            WHERE (is_current=1 AND retired_at IS NOT NULL) OR (is_current=0 AND retired_at IS NULL)''')
        self.query('MEDIA_RETIRED_ALTERNATIVE', '''SELECT am.alternative_media_id,am.alternative_id
            FROM alternative_media am JOIN alternative a USING(alternative_id)
            WHERE am.is_current=1 AND a.retired_at IS NOT NULL''')
        for table, columns in sorted(self.columns.items()):
            if 'is_current' in columns:
                self.query('INVALID_CURRENT:' + table, f'SELECT rowid FROM {quote(table)} WHERE is_current IS NULL OR is_current NOT IN (0,1)')

    def nomenclature(self):
        for row in self.db.execute('SELECT DISTINCT concept_id FROM alternative WHERE retired_at IS NULL ORDER BY concept_id'):
            concept = row[0]
            try:
                state = canonical_concept_state(self.db, concept)
                labels = {a.ref: a.working_label for a in state.lexical_state.alternatives}
                validation = validate_concept_nomenclature(state, labels)
                self.record('NOMENCLATURE:' + str(concept), (
                    dict(concept_id=concept, **conflict) for conflict in validation['conflicts']))
                if not self.preflight and validation['valid']:
                    canonical = calculate_concept_nomenclature(state)['automatic_labels']
                    self.record('NONCANONICAL_ALLOWED_LABELS:' + str(concept), (
                        dict(alternative_id=a, current=label, canonical=canonical[a])
                        for a, label in labels.items() if label != canonical[a]), 'WARN')
            except (ValueError, TypeError, OverflowError) as exc:
                self.record('NOMENCLATURE_UNREADABLE:' + str(concept), [dict(error=str(exc))])

    def evidence(self):
        self.query('OCCURRENCE_RETIRED_SOURCE', '''SELECT o.occurrence_id,o.source_id FROM occurrence o
            JOIN source s USING(source_id) WHERE s.retired_at IS NOT NULL''')
        self.query('IMPOSSIBLE_SOURCE_PERIOD', '''SELECT source_id,start_year,end_year FROM source
            WHERE (start_year IS NOT NULL AND typeof(start_year)!='integer')
               OR (end_year IS NOT NULL AND typeof(end_year)!='integer')
               OR (start_year IS NOT NULL AND end_year IS NOT NULL AND start_year>end_year)''')
        self.query('INVALID_OCCURRENCE_YEAR', "SELECT occurrence_id,occurrence_year FROM occurrence WHERE occurrence_year IS NOT NULL AND typeof(occurrence_year)!='integer'")
        self.query('OCCURRENCE_OUTSIDE_SOURCE_PERIOD', '''SELECT o.occurrence_id,o.occurrence_year,
                s.start_year,s.end_year
            FROM occurrence o JOIN source s USING(source_id)
            WHERE typeof(o.occurrence_year)='integer' AND
                ((s.start_year IS NOT NULL AND o.occurrence_year<s.start_year)
                 OR (s.end_year IS NOT NULL AND o.occurrence_year>s.end_year))''')
        self.query('INVALID_MEDIA_SIZE', "SELECT media_asset_id,file_size FROM media_asset WHERE file_size<0 OR (file_size IS NOT NULL AND typeof(file_size)!='integer')")
        self.record('INVALID_CANONICAL_VIDEO', self.invalid_videos())
        if not self.preflight:
            self.query('EMPTY_ACTIVE_ALTERNATIVE', '''SELECT alternative_id,concept_id,working_label FROM alternative al
                WHERE retired_at IS NULL AND NOT EXISTS(SELECT 1 FROM assignment a JOIN occurrence o USING(occurrence_id)
                    WHERE a.alternative_id=al.alternative_id AND a.is_current=1)''')
            self.query('OCCURRENCE_WITHOUT_CURRENT_ASSIGNMENT', '''SELECT occurrence_id FROM occurrence o
                WHERE NOT EXISTS(SELECT 1 FROM assignment a WHERE a.occurrence_id=o.occurrence_id AND a.is_current=1)''', 'WARN')

    def invalid_videos(self):
        for row in self.db.execute('''SELECT am.alternative_media_id,m.storage_key,m.mime_type,m.origin_kind
                FROM alternative_media am JOIN media_asset m USING(media_asset_id)
                WHERE am.role='catalog_video' AND am.is_current=1'''):
            try:
                if not isinstance(row['storage_key'], str):
                    raise ValueError('Video URL is not text')
                parse_youtube_url(row['storage_key'])
                if row['mime_type'] != 'video/youtube' or row['origin_kind'] != 'external_reference':
                    raise ValueError('Incompatible canonical video asset')
            except ValueError as exc:
                yield dict(alternative_media_id=row['alternative_media_id'], error=str(exc))

    def workflow(self):
        self.query('CONCEPT_PROPOSAL_STATE', '''SELECT concept_proposal_id,status,resolved_concept_id FROM concept_proposal
            WHERE status IS NULL OR status NOT IN ('pending','resolved','rejected')
                OR (status='resolved' AND resolved_concept_id IS NULL)
                OR (status IN ('pending','rejected') AND resolved_concept_id IS NOT NULL)''')
        self.query('SUBMISSION_STATE', '''SELECT submission_id,status,resolution,resolved_at FROM submission
            WHERE status NOT IN ('pending','resolved') OR status IS NULL
            OR (status='pending' AND (resolution IS NOT NULL OR resolved_at IS NOT NULL))
            OR (status='resolved' AND (resolution IS NULL OR resolution NOT IN ('accepted','rejected')))''')
        self.query('SUBMISSION_DETAIL', '''SELECT s.submission_id,s.submission_type FROM submission s
            LEFT JOIN alternative_submission a USING(submission_id) LEFT JOIN grammar_submission g USING(submission_id)
            WHERE (s.submission_type='ALTERNATIVE' AND (a.submission_id IS NULL OR g.submission_id IS NOT NULL))
               OR (s.submission_type='GRAMMAR' AND (g.submission_id IS NULL OR a.submission_id IS NOT NULL))
               OR s.submission_type NOT IN ('ALTERNATIVE','GRAMMAR')''')
        # Accepted results are historical references, including after retirement
        # or merge. Existence is checked by references(), not current activity.
        self.query('LEXICAL_SUBMISSION_RESULT', '''SELECT s.submission_id FROM submission s JOIN alternative_submission a USING(submission_id)
            WHERE (s.status='resolved' AND s.resolution='accepted' AND a.resolved_alternative_id IS NULL)
               OR (s.status='pending' AND a.resolved_alternative_id IS NOT NULL)''')
        self.query('LEXICAL_DECISION_STATE', '''SELECT d.submission_id FROM submission_lexical_decision d
            JOIN submission s USING(submission_id) LEFT JOIN alternative_submission a USING(submission_id)
            LEFT JOIN submission_concept_resolution c ON c.submission_concept_resolution_id=d.concept_resolution_id
            WHERE s.status!='resolved' OR s.submission_type!='ALTERNATIVE'
               OR c.submission_id!=d.submission_id OR c.concept_id!=d.concept_id_at_decision
               OR (d.decision_action='REJECT_REST' AND (s.resolution!='rejected' OR d.resolved_alternative_id IS NOT NULL))
               OR (d.decision_action IN ('USE_EXISTING','CREATE_NEW') AND
                   (s.resolution!='accepted' OR d.resolved_alternative_id IS NULL
                    OR d.assignment_result_id IS NULL OR a.resolved_alternative_id IS NOT d.resolved_alternative_id))''')
        self.query('DECISION_ASSIGNMENT_RESULT', '''SELECT d.submission_id,d.assignment_result_id FROM submission_lexical_decision d
            JOIN submission s USING(submission_id) JOIN assignment a ON a.assignment_id=d.assignment_result_id
            WHERE a.occurrence_id!=s.occurrence_id OR
                (d.decision_action!='REJECT_REST' AND a.alternative_id IS NOT d.resolved_alternative_id)''')
        self.query('CONCEPT_RESOLUTION_TYPE', '''SELECT c.submission_concept_resolution_id FROM submission_concept_resolution c
            JOIN submission s USING(submission_id) WHERE s.submission_type!='ALTERNATIVE' ''')
        self.query('PENDING_MATERIALIZED_GRAMMAR', '''SELECT s.submission_id,g.occurrence_grammar_id FROM submission s
            JOIN occurrence_grammar g ON g.created_from_submission_id=s.submission_id
            WHERE s.status='pending' OR s.resolution!='accepted' OR s.submission_type!='GRAMMAR' OR g.occurrence_id!=s.occurrence_id''')
        # Earlier migrations deliberately did not backfill decision records.
        if not self.preflight:
            self.query('ACCEPTED_GRAMMAR_WITHOUT_HISTORY', '''SELECT s.submission_id FROM submission s
                WHERE s.submission_type='GRAMMAR' AND s.resolution='accepted' AND NOT EXISTS(
                    SELECT 1 FROM occurrence_grammar g WHERE g.created_from_submission_id=s.submission_id)''', 'WARN')
            self.query('CLOSED_LEXICAL_WITHOUT_DECISION', '''SELECT s.submission_id FROM submission s
                WHERE s.submission_type='ALTERNATIVE' AND s.status='resolved' AND NOT EXISTS(
                    SELECT 1 FROM submission_lexical_decision d WHERE d.submission_id=s.submission_id)''', 'WARN')
            self.record('ACTIVITY_ENTITY_REFERENCE', self.activity_orphans(), 'FAIL')

    def activity_orphans(self):
        for row in self.db.execute('SELECT activity_event_id,entity_type,entity_id FROM activity_event WHERE entity_id IS NOT NULL'):
            entity_type = row['entity_type']
            if entity_type in PERSISTENT_ACTIVITY_ENTITIES:
                table, key = PERSISTENT_ACTIVITY_ENTITIES[entity_type]
                if not self.db.execute(
                    f'SELECT 1 FROM {quote(table)} WHERE {quote(key)}=?',
                    (row['entity_id'],),
                ).fetchone():
                    yield dict(row)
            elif entity_type in EPHEMERAL_ACTIVITY_ENTITIES:
                table, key = 'occurrence_draft', 'draft_id'
                if self.db.execute(
                    f'SELECT 1 FROM {quote(table)} WHERE {quote(key)}=?',
                    (row['entity_id'],),
                ).fetchone() is None:
                    continue
            else:
                yield dict(row, reason='Unknown polymorphic entity type; cannot verify')

    def publications(self):
        self.query('DUPLICATE_PUBLICATION_VERSION', 'SELECT version_number,count(*) AS n FROM catalog_publication GROUP BY version_number HAVING count(*)>1')
        problems = []
        for row in self.db.execute('SELECT * FROM catalog_publication ORDER BY publication_id'):
            try:
                snapshot = json.loads(row['snapshot_json'])
                summary = json.loads(row['change_summary_json'])
                valid = isinstance(snapshot, dict) and isinstance(snapshot.get('concepts'), list) and isinstance(summary, dict)
                if valid:
                    for concept in snapshot['concepts']:
                        valid = valid and isinstance(concept, dict) and isinstance(concept.get('concept_id'), int) and isinstance(concept.get('alternatives'), list) and isinstance(concept.get('relations'), list)
                        if not valid:
                            break
                        for alternative in concept['alternatives']:
                            valid = valid and isinstance(alternative, dict) and isinstance(alternative.get('alternative_id'), int) and isinstance(alternative.get('working_label'), str) and isinstance(alternative.get('occurrences'), list)
                if not valid or row['version_number'] < 1:
                    raise ValueError('Invalid minimum snapshot structure or version')
                if hashlib.sha256(row['snapshot_json'].encode('utf-8')).hexdigest() != row['snapshot_sha256']:
                    raise ValueError('Snapshot SHA256 mismatch')
            except (ValueError, TypeError, KeyError) as exc:
                problems.append(dict(publication_id=row['publication_id'], error=str(exc)))
        self.record('INVALID_PUBLICATION_SNAPSHOT', problems)

    def classifications(self):
        self.query('DUPLICATE_OPEN_MEMBERSHIP', '''SELECT concept_id,collection_id,count(*) AS n
            FROM collection_membership WHERE ended_at IS NULL GROUP BY concept_id,collection_id HAVING count(*)>1''')
        self.query('DUPLICATE_OPEN_CLASSIFICATION', '''SELECT concept_id,system_id,count(*) AS n
            FROM concept_classification_revision WHERE ended_at IS NULL GROUP BY concept_id,system_id HAVING count(*)>1''')
        for table in ('concept_classification_revision','submission_classification_proposal'):
            self.query('INVALID_CATEGORY_PAIR:' + table, f'''SELECT r.* FROM {table} r
                LEFT JOIN classification_category a ON a.category_id=r.category_1_id
                LEFT JOIN classification_category b ON b.category_id=r.category_2_id
                WHERE (r.category_1_id IS NULL AND r.category_2_id IS NOT NULL)
                OR r.category_1_id=r.category_2_id
                OR (r.category_1_id IS NOT NULL AND (a.category_id IS NULL OR a.system_id!=r.system_id
                    OR r.category_1_name IS NULL OR r.category_1_code IS NULL))
                OR (r.category_2_id IS NOT NULL AND (b.category_id IS NULL OR b.system_id!=r.system_id
                    OR r.category_2_name IS NULL OR r.category_2_code IS NULL))
                OR (r.category_1_id IS NULL AND (r.category_1_name IS NOT NULL OR r.category_1_code IS NOT NULL))
                OR (r.category_2_id IS NULL AND (r.category_2_name IS NOT NULL OR r.category_2_code IS NOT NULL))''')
        self.query('INVALID_CLASSIFICATION_SCOPE', '''SELECT r.revision_id FROM concept_classification_revision r
            LEFT JOIN classification_system s ON s.system_id=r.system_id
            LEFT JOIN collection_membership m ON m.membership_id=r.membership_id
            WHERE s.system_id IS NULL OR (s.collection_id IS NULL AND r.membership_id IS NOT NULL)
            OR (s.collection_id IS NOT NULL AND (m.membership_id IS NULL OR m.collection_id!=s.collection_id
                OR m.concept_id!=r.concept_id OR (r.ended_at IS NULL AND m.ended_at IS NOT NULL)
                OR r.started_at<m.started_at OR (m.ended_at IS NOT NULL AND r.ended_at>m.ended_at)))''')
        self.query('INVALID_CLASSIFICATION_CHAIN', '''SELECT r.revision_id FROM concept_classification_revision r
            LEFT JOIN concept_classification_revision p ON p.revision_id=r.supersedes_revision_id
            WHERE r.ended_at<r.started_at OR (r.supersedes_revision_id IS NOT NULL AND
                (p.revision_id IS NULL OR p.revision_id>=r.revision_id OR p.concept_id!=r.concept_id
                 OR p.system_id!=r.system_id OR p.membership_id IS NOT r.membership_id
                 OR p.ended_at IS NULL OR p.ended_at>r.started_at))''')
        self.query('BRANCHED_CLASSIFICATION_HISTORY', '''SELECT supersedes_revision_id,count(*) AS n
            FROM concept_classification_revision WHERE supersedes_revision_id IS NOT NULL
            GROUP BY supersedes_revision_id HAVING count(*)>1''')
        for table in ('collection_membership','concept_classification_revision'):
            self.query('INVALID_CLASSIFICATION_ORIGIN:' + table, f'''SELECT r.* FROM {table} r
                LEFT JOIN submission_concept_resolution d ON d.submission_concept_resolution_id=r.resolution_id
                LEFT JOIN submission_concept_resolution e ON e.submission_concept_resolution_id=r.ended_resolution_id
                WHERE (r.resolution_id IS NOT NULL AND (d.submission_concept_resolution_id IS NULL OR d.concept_id!=r.concept_id))
                   OR (r.ended_resolution_id IS NOT NULL AND (e.submission_concept_resolution_id IS NULL OR e.concept_id!=r.concept_id))''')
        self.query('INVALID_MEMBERSHIP_PERIOD', 'SELECT membership_id FROM collection_membership WHERE ended_at<started_at')
        self.query('INVALID_CLASSIFICATION_SYSTEM', """SELECT system_id FROM classification_system
            WHERE (code='semantic-fields' AND collection_id IS NOT NULL)
               OR (code!='semantic-fields' AND collection_id IS NULL)""")
        self.query('INVALID_CONTROLLED_CATEGORY', """SELECT category_id FROM classification_category
            WHERE lower(trim(name))='n/a' OR lower(trim(code))='n/a' OR name_key='n/a' OR trim(name)=''""")
        for table in ('submission_classification_proposal','submission_collection_proposal'):
            self.query('INVALID_CLASSIFICATION_SUBMISSION:' + table, f'''SELECT p.submission_id FROM {table} p
                LEFT JOIN submission s ON s.submission_id=p.submission_id
                WHERE s.submission_id IS NULL OR s.submission_type!='ALTERNATIVE' ''')
            invalid=[]
            for r in self.db.execute(f'SELECT submission_id,base_state_json FROM {table}'):
                try:
                    value=json.loads(r['base_state_json'])
                    if not isinstance(value,dict) or not isinstance(value['memberships'],list) or not isinstance(value['revisions'],list):
                        raise ValueError('Invalid proposal base')
                except (ValueError,KeyError,TypeError):
                    invalid.append(dict(submission_id=r['submission_id']))
            self.record('INVALID_CLASSIFICATION_PROPOSAL_BASE:' + table,invalid)
        problems=[]
        if 'classification_decision_json' in self.columns['submission_concept_resolution']:
            for r in self.db.execute('SELECT * FROM submission_concept_resolution WHERE classification_decision_json IS NOT NULL'):
                try:
                    value=json.loads(r['classification_decision_json'])
                    if value['concept_id']!=r['concept_id'] or not isinstance(value['classifications'],list) or not isinstance(value['collections'],list):
                        raise ValueError('Invalid conceptual decision')
                except (ValueError,KeyError,TypeError):
                    problems.append(dict(resolution_id=r['submission_concept_resolution_id']))
        self.record('INVALID_CLASSIFICATION_DECISION',problems)

    def usage_profiles(self):
        self.query('DUPLICATE_USAGE_PROFILE', '''SELECT alternative_id,count(*) AS count
            FROM alternative_usage_profile GROUP BY alternative_id HAVING count(*)>1''')
        self.record('ARTIFICIAL_USAGE_ABSENCE', (
            dict(profile_id=row['profile_id'], field=field)
            for row in self.db.execute('SELECT * FROM alternative_usage_profile')
            for field in USAGE_FIELDS if row[field] is not None and normalize_usage(row[field]) is None
        ))

    def run(self):
        self.record('SCHEMA_COVERAGE', (
            dict(table=t, missing_columns=sorted(set(cols.split()) - self.columns.get(t, set())))
            for t, cols in REQUIRED.items() if not set(cols.split()) <= self.columns.get(t, set())))
        self.record('SQLITE_INTEGRITY', (dict(error=r[0]) for r in self.db.execute('PRAGMA integrity_check') if r[0] != 'ok'))
        if self.results[0]['status'] != 'FAIL':
            self.references()
            self.relations()
            self.current_states()
            self.evidence()
            self.nomenclature()
            self.workflow()
            self.publications()
            self.classifications()
            self.usage_profiles()
        return self.results


def audit_database(path, *, preflight=False):
    with open_readonly(path) as connection:
        results = Auditor(connection, preflight).run()
        changes = connection.total_changes
    counts = Counter(r['status'] for r in results)
    by_code = {}
    for result in results:
        if result['status'] != 'PASS':
            by_code[result['code']] = dict(status=result['status'], count=result['count'])
    return dict(database=str(Path(path).expanduser().resolve()), preflight=preflight,
                status='FAIL' if counts['FAIL'] else 'WARN' if counts['WARN'] else 'PASS',
                checks=len(results), summary={s: counts[s] for s in ('PASS', 'WARN', 'FAIL')},
                total_changes=changes, results=results, by_code=by_code)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', help='Offline SQLite copy to audit')
    parser.add_argument('--preflight', action='store_true', help='Run blocking checks only')
    parser.add_argument('--json', action='store_true', help='Machine-readable report')
    args = parser.parse_args(argv)
    try:
        report = audit_database(args.database, preflight=args.preflight)
    except (OSError, sqlite3.Error, AuditAccessError) as exc:
        report = dict(database=args.database, status='ERROR', error=str(exc), exit_code=2)
        print(json.dumps(report, ensure_ascii=True) if args.json else 'AUDIT ERROR: ' + str(exc))
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        for result in report['results']:
            if result['status'] != 'PASS':
                print(f"[{result['status']}] {result['code']} ({result['count']})")
                for example in result['examples']:
                    # Keep even very large components and malformed values bounded.
                    print('  ' + json.dumps(example, ensure_ascii=True)[:1000])
        print('\nINTEGRITY AUDIT\nDatabase: ' + report['database'])
        print('Checks: ' + str(report['checks']))
        for status, count in report['summary'].items():
            print(f'{status}: {count}')
        print('Result: ' + report['status'])
        if report['by_code']:
            print('By code:')
            for code, item in sorted(report['by_code'].items()):
                print(f"  {item['status']} {code}: {item['count']}")
    return 1 if report['status'] == 'FAIL' else 0


if __name__ == '__main__':
    raise SystemExit(main())
