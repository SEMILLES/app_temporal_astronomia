FREE_PERMUTATION_VALUES=("SÍ","NO","SIN INFORMACIÓN")


class MorphologyValidationError(ValueError): pass
class AlternativeNotFoundError(ValueError): pass


def _text(value):
    value="" if value is None else str(value).strip()
    return value or None


def normalize_morphology(component_count=None,component_count_not_applicable=False,
                         free_permutation=None,note=None,components=()):
    not_applicable=1 if component_count_not_applicable else 0
    count=None
    if component_count not in (None,""):
        try: count=int(component_count)
        except (TypeError,ValueError): raise MorphologyValidationError("La cantidad de componentes debe ser un entero.")
        if count<1: raise MorphologyValidationError("La cantidad de componentes debe ser al menos 1.")
    if not_applicable and count is not None: raise MorphologyValidationError("N/A no puede combinarse con una cantidad.")
    permutation=_text(free_permutation)
    if not_applicable or count==1:
        if permutation not in (None,"N/A"): raise MorphologyValidationError("La permutación debe ser N/A.")
        permutation="N/A"
    elif count is None:
        if permutation is not None: raise MorphologyValidationError("Sin cantidad analizada, la permutación debe quedar vacía.")
    else:
        if permutation not in FREE_PERMUTATION_VALUES: raise MorphologyValidationError("Seleccione SÍ, NO o SIN INFORMACIÓN para permutación libre.")
    normalized=[];positions=set()
    for index,item in enumerate(components,1):
        position=item.get("position",index)
        try: position=int(position)
        except (TypeError,ValueError): raise MorphologyValidationError("La posición del componente debe ser positiva.")
        if position<1 or position in positions: raise MorphologyValidationError("Las posiciones deben ser positivas y no repetirse.")
        positions.add(position)
        alternative_id=item.get("component_alternative_id") or None
        if alternative_id is not None: alternative_id=int(alternative_id)
        label=_text(item.get("component_label")); component_note=_text(item.get("note"))
        if alternative_id is None and label is None and component_note is None: raise MorphologyValidationError("Un componente debe identificar una alternative, label o nota.")
        normalized.append({"position":position,"component_alternative_id":alternative_id,"component_label":label,"note":component_note})
    return {"component_count":count,"component_count_not_applicable":not_applicable,"free_permutation":permutation,"note":_text(note),"components":normalized}


def _validate_component_targets(connection,morphology):
    for component in morphology["components"]:
        alternative_id=component["component_alternative_id"]
        if alternative_id is None: continue
        row=connection.execute("SELECT retired_at FROM alternative WHERE alternative_id=?",(alternative_id,)).fetchone()
        if row is None or row[0] is not None: raise MorphologyValidationError("La alternative seleccionada como componente no está vigente.")


def store_submission_morphology(connection,submission_id,**values):
    morphology=normalize_morphology(**values);_validate_component_targets(connection,morphology)
    proposal=connection.execute("SELECT proposal_kind FROM alternative_submission WHERE submission_id=?",(submission_id,)).fetchone()
    if proposal is None or proposal[0]!="NEW": raise MorphologyValidationError("Solo una propuesta NEW puede registrar morphology.")
    connection.execute("INSERT INTO alternative_submission_morphology(submission_id,component_count,component_count_not_applicable,free_permutation,note) VALUES(?,?,?,?,?)",(submission_id,morphology["component_count"],morphology["component_count_not_applicable"],morphology["free_permutation"],morphology["note"]))
    for item in morphology["components"]:
        connection.execute("INSERT INTO alternative_submission_component(submission_id,position,component_alternative_id,component_label,note) VALUES(?,?,?,?,?)",(submission_id,item["position"],item["component_alternative_id"],item["component_label"],item["note"]))
    return morphology


def submission_morphology(connection,submission_id):
    row=connection.execute("SELECT * FROM alternative_submission_morphology WHERE submission_id=?",(submission_id,)).fetchone()
    if row is None:return None
    components=connection.execute("SELECT * FROM alternative_submission_component WHERE submission_id=? ORDER BY position",(submission_id,)).fetchall()
    return row,components


def create_or_replace_alternative_morphology(connection,alternative_id,*,
        component_count=None,component_count_not_applicable=False,
        free_permutation=None,note=None,components=(),created_by=None,
        created_from_submission_id=None):
    morphology=normalize_morphology(component_count,component_count_not_applicable,free_permutation,note,components);_validate_component_targets(connection,morphology)
    owns=not connection.in_transaction
    try:
        connection.execute("BEGIN IMMEDIATE" if owns else "SAVEPOINT morphology_operation")
        if connection.execute("SELECT 1 FROM alternative WHERE alternative_id=?",(alternative_id,)).fetchone() is None: raise AlternativeNotFoundError("La alternative no existe.")
        current=connection.execute("SELECT * FROM alternative_morphology WHERE alternative_id=? AND is_current=1",(alternative_id,)).fetchone()
        supersedes=current["alternative_morphology_id"] if current else None
        if current:
            old_components=[tuple(row) for row in connection.execute("SELECT position,component_alternative_id,component_label,note FROM alternative_component WHERE alternative_morphology_id=? ORDER BY position",(supersedes,)).fetchall()]
            new_components=[(item["position"],item["component_alternative_id"],item["component_label"],item["note"]) for item in morphology["components"]]
            same=(current["component_count"],current["component_count_not_applicable"],current["free_permutation"],current["note"])==(morphology["component_count"],morphology["component_count_not_applicable"],morphology["free_permutation"],morphology["note"]) and old_components==new_components
            if same:
                connection.commit() if owns else connection.execute("RELEASE SAVEPOINT morphology_operation")
                return supersedes,False
            connection.execute("UPDATE alternative_morphology SET is_current=0 WHERE alternative_morphology_id=?",(supersedes,))
        cursor=connection.execute("INSERT INTO alternative_morphology(alternative_id,component_count,component_count_not_applicable,free_permutation,note,is_current,supersedes_alternative_morphology_id,created_from_submission_id,created_by) VALUES(?,?,?,?,?,1,?,?,?)",(alternative_id,morphology["component_count"],morphology["component_count_not_applicable"],morphology["free_permutation"],morphology["note"],supersedes,created_from_submission_id,_text(created_by)))
        morphology_id=cursor.lastrowid
        for item in morphology["components"]: connection.execute("INSERT INTO alternative_component(alternative_morphology_id,position,component_alternative_id,component_label,note) VALUES(?,?,?,?,?)",(morphology_id,item["position"],item["component_alternative_id"],item["component_label"],item["note"]))
        from conflicts import detect_conflicts_after_change
        detect_conflicts_after_change(connection,"alternative_morphology",morphology_id)
        connection.commit() if owns else connection.execute("RELEASE SAVEPOINT morphology_operation")
        return morphology_id,True
    except Exception:
        if owns:connection.rollback()
        else:connection.execute("ROLLBACK TO SAVEPOINT morphology_operation");connection.execute("RELEASE SAVEPOINT morphology_operation")
        raise


def materialize_submission_morphology(connection,submission_id,alternative_id,created_by=None):
    proposed=submission_morphology(connection,submission_id)
    if proposed is None: raise MorphologyValidationError("La submission no contiene morphology propuesta.")
    row,components=proposed
    return create_or_replace_alternative_morphology(connection,alternative_id,component_count=row["component_count"],component_count_not_applicable=row["component_count_not_applicable"],free_permutation=row["free_permutation"],note=row["note"],components=[{"position":c["position"],"component_alternative_id":c["component_alternative_id"],"component_label":c["component_label"],"note":c["note"]} for c in components],created_by=created_by,created_from_submission_id=submission_id)
