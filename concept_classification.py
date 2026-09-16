"""Controlled concept classifications, ordered presentation and membership history."""
import json
import re
import unicodedata
from contextlib import contextmanager

from activity import record_activity, resolve_collaborator
from edit_concurrency import StaleEdit, fingerprint


class ClassificationError(ValueError):
    pass


@contextmanager
def transaction(db):
    owns = not db.in_transaction
    db.execute('BEGIN IMMEDIATE' if owns else 'SAVEPOINT concept_metadata')
    try:
        yield
        db.commit() if owns else db.execute('RELEASE SAVEPOINT concept_metadata')
    except Exception:
        if owns:
            db.rollback()
        else:
            db.execute('ROLLBACK TO SAVEPOINT concept_metadata')
            db.execute('RELEASE SAVEPOINT concept_metadata')
        raise


def rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]


def catalog_state(db):
    return {table: rows(db, f'SELECT * FROM {table} ORDER BY {key}') for table, key in (
        ('collection', 'collection_id'), ('classification_system', 'system_id'),
        ('classification_category', 'category_id'))}


def concept_state(db, concept_id):
    return {
        'concept_id': concept_id,
        # Include closed identities to detect leave/rejoin and change/revert cycles.
        'memberships': rows(db, 'SELECT * FROM collection_membership WHERE concept_id=? ORDER BY membership_id', (concept_id,)),
        'revisions': rows(db, 'SELECT * FROM concept_classification_revision WHERE concept_id=? ORDER BY revision_id', (concept_id,)),
    }


def normalize_pair(values):
    if not isinstance(values, (list, tuple)) or len(values) > 2:
        raise ClassificationError('Seleccione hasta dos categorías.')
    try:
        result = [int(v) for v in values if v not in (None, '')]
    except (ValueError, TypeError):
        raise ClassificationError('Categoría inválida.') from None
    if len(set(result)) != len(result):
        raise ClassificationError('Las dos categorías no pueden repetirse.')
    return result + [None] * (2-len(result))


def pair(row):
    return [row['category_1_id'], row['category_2_id']] if row else [None, None]


def snapshot_pair(db, system_id, values, *, retained=()):
    values = normalize_pair(values)
    snapshots = []
    for value in values:
        if value is None:
            snapshots.extend((None, None))
            continue
        category = db.execute('SELECT * FROM classification_category WHERE category_id=? AND system_id=?', (value,system_id)).fetchone()
        if category is None or (not category['active'] and value not in retained):
            raise ClassificationError('Seleccione una categoría activa del sistema correspondiente.')
        snapshots.extend((category['name'], category['code']))
    return values, snapshots


def _actor(db, role, collaborator_id):
    if role not in ('reviewer', 'master'):
        raise ClassificationError('Se requiere acceso de revisión.')
    identifier, name = resolve_collaborator(db, collaborator_id)
    return identifier, name, role


def _close(db, table, key, identifier, actor, resolution_id):
    db.execute(f'''UPDATE {table} SET ended_at=CURRENT_TIMESTAMP,
        ended_by_collaborator_id=?,ended_by_name_snapshot=?,ended_access_role=?,ended_resolution_id=?
        WHERE {key}=? AND ended_at IS NULL''', (*actor,resolution_id,identifier))


def apply_metadata(db, concept_id, payload, *, access_role, collaborator_id=None,
                   resolution_id=None, expected_state=None):
    """Apply explicit changes only. Missing scopes preserve existing state."""
    with transaction(db):
        actor = _actor(db, access_role, collaborator_id)
        if not db.execute('SELECT 1 FROM concept WHERE concept_id=?', (concept_id,)).fetchone():
            raise ClassificationError('El Concept no existe.')
        if expected_state is not None and fingerprint(concept_state(db, concept_id)) != expected_state:
            raise StaleEdit('El Concept cambió; vuelva a revisar sus clasificaciones.')
        before = concept_state(db, concept_id)
        payload = payload or {}
        actions = {int(k): v for k,v in payload.get('collections', {}).items()}
        selections = {int(k): normalize_pair(v) for k,v in payload.get('classifications', {}).items()}
        for cid, action in actions.items():
            if action not in ('join', 'leave'):
                raise ClassificationError('Acción de Collection inválida.')
            collection = db.execute('SELECT * FROM collection WHERE collection_id=?', (cid,)).fetchone()
            if collection is None:
                raise ClassificationError('La Collection no existe.')
            membership = db.execute('SELECT * FROM collection_membership WHERE concept_id=? AND collection_id=? AND ended_at IS NULL', (concept_id,cid)).fetchone()
            if action == 'leave' and membership:
                revisions = db.execute('SELECT revision_id FROM concept_classification_revision WHERE membership_id=? AND ended_at IS NULL', (membership['membership_id'],)).fetchall()
                for revision in revisions:
                    _close(db, 'concept_classification_revision', 'revision_id', revision[0], actor, resolution_id)
                _close(db, 'collection_membership', 'membership_id', membership['membership_id'], actor, resolution_id)
            elif action == 'join' and not membership:
                if not collection['active']:
                    raise ClassificationError('No se puede ingresar a una Collection inactiva.')
                db.execute('''INSERT INTO collection_membership(concept_id,collection_id,
                    collection_name_snapshot,collection_code_snapshot,collaborator_id,
                    collaborator_name_snapshot,access_role,resolution_id) VALUES(?,?,?,?,?,?,?,?)''',
                    (concept_id,cid,collection['name'],collection['code'],*actor,resolution_id))
        decisions = []
        for sid, values in selections.items():
            system = db.execute('SELECT * FROM classification_system WHERE system_id=?', (sid,)).fetchone()
            if system is None:
                raise ClassificationError('El sistema no existe.')
            old = db.execute('SELECT * FROM concept_classification_revision WHERE concept_id=? AND system_id=? AND ended_at IS NULL', (concept_id,sid)).fetchone()
            membership_id = None
            if system['collection_id'] is not None:
                if actions.get(system['collection_id']) == 'leave':
                    if any(values):
                        raise ClassificationError('No se pueden asignar Áreas al retirar la membresía.')
                    continue
                membership = db.execute('SELECT membership_id FROM collection_membership WHERE concept_id=? AND collection_id=? AND ended_at IS NULL', (concept_id,system['collection_id'])).fetchone()
                if membership is None:
                    raise ClassificationError('La clasificación requiere una membresía vigente.')
                membership_id = membership[0]
            values, snapshots = snapshot_pair(db, sid, values, retained=pair(old))
            changed = set(v for v in values if v) != set(v for v in pair(old) if v)
            if not system['active'] and any(v not in pair(old) for v in values if v):
                raise ClassificationError('El sistema está inactivo.')
            if system['collection_id'] is not None:
                active = db.execute('SELECT active FROM collection WHERE collection_id=?', (system['collection_id'],)).fetchone()[0]
                if not active and any(v not in pair(old) for v in values if v):
                    raise ClassificationError('La Collection está inactiva.')
            if old and pair(old) == values:
                revision_id = old['revision_id']
            else:
                if old:
                    _close(db, 'concept_classification_revision', 'revision_id', old['revision_id'], actor, resolution_id)
                revision_id = db.execute('''INSERT INTO concept_classification_revision
                    (concept_id,system_id,system_name_snapshot,system_code_snapshot,membership_id,
                     category_1_id,category_2_id,category_1_name,category_1_code,category_2_name,category_2_code,
                     supersedes_revision_id,semantic_changed,collaborator_id,collaborator_name_snapshot,access_role,resolution_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (concept_id,sid,system['name'],system['code'],membership_id,*values,*snapshots,
                     old['revision_id'] if old else None,int(changed),*actor,resolution_id)).lastrowid
            decisions.append({'system_id':sid,'system_name':system['name'],'system_code':system['code'],
                              'categories':values,'names':[snapshots[0],snapshots[2]],
                              'codes':[snapshots[1],snapshots[3]],'revision_id':revision_id,
                              'semantic_changed':changed})
        after = concept_state(db, concept_id)
        result = {'concept_id':concept_id, 'classifications':decisions,
                  'collections':[{'collection_id':cid,'action':action,
                    'name':db.execute('SELECT name FROM collection WHERE collection_id=?',(cid,)).fetchone()[0]}
                    for cid,action in actions.items()],
                  'membership_ids':[m['membership_id'] for m in after['memberships'] if m['ended_at'] is None]}
        if before != after:
            record_activity(db,'concept_classification_changed',entity_type='concept',entity_id=concept_id,
                            collaborator_id=collaborator_id,access_role=access_role,
                            comment=json.dumps(result,ensure_ascii=False))
        return result


def store_proposal(db, submission_id, payload, *, access_role, concept_id=None):
    if access_role not in ('analyst','reviewer','master'):
        raise ClassificationError('Se requiere acceso de análisis.')
    with transaction(db):
        state = json.dumps(concept_state(db, concept_id),ensure_ascii=False,sort_keys=True)
        actions = {int(k):v for k,v in (payload or {}).get('collections',{}).items()}
        for cid, action in actions.items():
            collection = db.execute('SELECT * FROM collection WHERE collection_id=?',(cid,)).fetchone()
            if collection is None or action not in ('join','leave') or (action=='join' and not collection['active']):
                raise ClassificationError('Propuesta de membresía inválida.')
            db.execute('''INSERT INTO submission_collection_proposal
                (submission_id,collection_id,action,collection_name_snapshot,collection_code_snapshot,base_concept_id,base_state_json)
                VALUES(?,?,?,?,?,?,?)''',(submission_id,cid,action,collection['name'],collection['code'],concept_id,state))
        for key, values in (payload or {}).get('classifications',{}).items():
            sid = int(key)
            system = db.execute('SELECT * FROM classification_system WHERE system_id=? AND active=1',(sid,)).fetchone()
            if system is None:
                raise ClassificationError('Seleccione un sistema activo.')
            cid = system['collection_id']
            if cid is not None:
                member = db.execute('SELECT 1 FROM collection_membership WHERE concept_id=? AND collection_id=? AND ended_at IS NULL',(concept_id,cid)).fetchone()
                if actions.get(cid)=='leave' or (not member and actions.get(cid)!='join'):
                    raise ClassificationError('Proponga la membresía antes de sus Áreas.')
                if not db.execute('SELECT active FROM collection WHERE collection_id=?',(cid,)).fetchone()[0]:
                    raise ClassificationError('La Collection está inactiva.')
            values,snapshots = snapshot_pair(db,sid,values)
            db.execute('''INSERT INTO submission_classification_proposal
                (submission_id,system_id,system_name_snapshot,system_code_snapshot,
                 category_1_id,category_2_id,category_1_name,category_1_code,category_2_name,category_2_code,
                 base_concept_id,base_state_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (submission_id,sid,system['name'],system['code'],*values,*snapshots,concept_id,state))


def proposal_payload(db, submission_id):
    return {'classifications':{r['system_id']:pair(r) for r in db.execute('SELECT * FROM submission_classification_proposal WHERE submission_id=?',(submission_id,))},
            'collections':{r['collection_id']:r['action'] for r in db.execute('SELECT * FROM submission_collection_proposal WHERE submission_id=?',(submission_id,))}}


def check_proposal_base(db, submission_id, concept_id):
    current = concept_state(db,concept_id)
    for table in ('submission_classification_proposal','submission_collection_proposal'):
        for r in db.execute(f'SELECT base_concept_id,base_state_json FROM {table} WHERE submission_id=?',(submission_id,)):
            if ((r['base_concept_id'] is None and (current['memberships'] or current['revisions'])) or
                (r['base_concept_id'] is not None and (r['base_concept_id'] != concept_id or
                    fingerprint(json.loads(r['base_state_json'])) != fingerprint(current)))):
                raise StaleEdit('El destino o las clasificaciones cambiaron desde la propuesta. Revise el estado vigente y confirme sus selecciones.')


def parse_form(form):
    payload = {'classifications':{},'collections':{}}
    for key in form:
        if key.startswith('classification_apply_') and form.get(key) == 'yes':
            sid = int(key.removeprefix('classification_apply_'))
            payload['classifications'][sid] = normalize_pair([form.get(f'category_{sid}_1'),form.get(f'category_{sid}_2')])
        elif key.startswith('collection_action_') and form.get(key):
            payload['collections'][int(key.removeprefix('collection_action_'))] = form.get(key)
    return payload if any(payload.values()) else None


def editor_context(db, concept_id=None, submission_id=None):
    catalog = catalog_state(db)
    state = concept_state(db,concept_id)
    current = {r['system_id']:r for r in state['revisions'] if r['ended_at'] is None}
    proposals = rows(db,'SELECT * FROM submission_classification_proposal WHERE submission_id=?',(submission_id,)) if submission_id else []
    memberships = rows(db,'SELECT * FROM submission_collection_proposal WHERE submission_id=?',(submission_id,)) if submission_id else []
    for system in catalog['classification_system']:
        system['categories'] = [c for c in catalog['classification_category'] if c['system_id']==system['system_id']]
        system['current'] = current.get(system['system_id'])
        system['current_names'] = [next((c['name'] for c in system['categories'] if c['category_id']==identifier),None)
                                   for identifier in pair(system['current'])]
        system['proposal'] = next((p for p in proposals if p['system_id']==system['system_id']),None)
        system['selection'] = pair(system['proposal'] or system['current'])
    return {'systems':catalog['classification_system'],'collections':catalog['collection'],
            'memberships':state['memberships'],'history':list(reversed(state['revisions'])),
            'proposals':proposals,'membership_proposals':memberships}


def administer(db, kind, *, access_role, code=None, name=None, active=1,
               identifier=None, parent_id=None, collaborator_id=None):
    if access_role != 'master':
        raise ClassificationError('Solo Master administra los catálogos controlados.')
    mapping = {'collection':('collection','collection_id'),
               'system':('classification_system','system_id'),
               'category':('classification_category','category_id')}
    if kind not in mapping:
        raise ClassificationError('Catálogo inválido.')
    name = unicodedata.normalize('NFC',' '.join((name or '').split()))
    if not name or name.casefold() == 'n/a':
        raise ClassificationError('Indique un nombre válido; N/A no es una categoría.')
    if str(active) not in ('0','1'):
        raise ClassificationError('Estado inválido.')
    table,key = mapping[kind]
    with transaction(db):
        before = None
        if identifier is not None:
            old = db.execute(f'SELECT * FROM {table} WHERE {key}=?',(identifier,)).fetchone()
            if old is None:
                raise ClassificationError('La entrada no existe.')
            before = dict(old)
            fields = 'name=?,active=?' + (',name_key=?' if kind!='system' else '')
            values = [name,int(active)] + ([name.casefold()] if kind!='system' else [])
            db.execute(f'UPDATE {table} SET {fields} WHERE {key}=?',(*values,identifier))
        else:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',code or '') or code.casefold()=='n/a':
                raise ClassificationError('Indique un código estable con letras, números o guiones.')
            fields,values = ['code','name','active'],[code,name,int(active)]
            if kind!='system':
                fields.append('name_key');values.append(name.casefold())
            if kind!='collection':
                parent_table,parent_key = ('collection','collection_id') if kind=='system' else ('classification_system','system_id')
                if not db.execute(f'SELECT 1 FROM {parent_table} WHERE {parent_key}=? AND active=1',(parent_id,)).fetchone():
                    raise ClassificationError('Seleccione un ámbito activo.')
                fields.append(parent_key);values.append(parent_id)
            identifier = db.execute(f'INSERT INTO {table}({",".join(fields)}) VALUES({",".join("?" for _ in fields)})',values).lastrowid
        after = dict(db.execute(f'SELECT * FROM {table} WHERE {key}=?',(identifier,)).fetchone())
        record_activity(db,'classification_catalog_changed',entity_type=table,entity_id=identifier,
                        collaborator_id=collaborator_id,access_role=access_role,
                        comment=json.dumps({'before':before,'after':after},ensure_ascii=False))
        return identifier
