from flask import Blueprint, render_template, request, redirect, url_for, g, abort

import sqlite3
import re

from database import conectar
from activity import record_activity
from access_control import requires_reviewer
from alternative_video_service import (AlternativeVideoError, add_video,
                                       get_current_video, get_video_history,
                                       replace_video, retire_video)
from youtube_media import InvalidYouTubeURL
from alternative_morphology import MorphologyValidationError
from alternative_relations import (DuplicateCurrentRelationError,
                                   RelationNotFoundError, SelfRelationError)
from alternative_nomenclature import (InvalidNomenclatureError,
                                      calculate_nomenclature_preview)
from alternative_admin import (AlternativeAdminError, apply_direct_nomenclature,
                               apply_relation_change, relation_preview,
                               update_morphology)
from alternative_structural import (StructuralAlternativeError, retire_preview,
    apply_retire, merge_preview, apply_merge, split_preview, apply_split,
    move_preview, apply_move)
from phonological_parameters import PHONOLOGICAL_PARAMETERS


alternatives_bp = Blueprint("alternatives", __name__)


def _actor():
    return {"access_role": g.current_access_role,
            "collaborator_id": request.form.get("collaborator_id")}


def _components_from_form(form):
    result = []
    positions = sorted({int(match.group(1)) for key in form for match in
                        [re.fullmatch(r"component_(\d+)_position", key)] if match})
    for index in positions:
        prefix = f"component_{index}_"
        alternative_id = form.get(prefix + "alternative_id", "").strip()
        label = form.get(prefix + "label", "").strip()
        note = form.get(prefix + "note", "").strip()
        position = form.get(prefix + "position", "").strip()
        if alternative_id or label or note:
            result.append({"position": position,
                           "component_alternative_id": alternative_id or None,
                           "component_label": label or None, "note": note or None})
    return result


def _labels_from_form(form):
    return {int(key.split("_", 1)[1]): value for key, value in form.items()
            if key.startswith("label_")}


def _management_context(connection, alternative_id, *, message=None, error=None,
                        relation_result=None, structural_result=None):
    alternative = connection.execute("""
        SELECT a.*,c.preferred_label FROM alternative a JOIN concept c USING(concept_id)
        WHERE a.alternative_id=?
    """, (alternative_id,)).fetchone()
    if alternative is None:
        abort(404)
    morphology = connection.execute(
        "SELECT * FROM alternative_morphology WHERE alternative_id=? AND is_current=1",
        (alternative_id,),).fetchone()
    morphology_history = connection.execute(
        "SELECT * FROM alternative_morphology WHERE alternative_id=? ORDER BY is_current DESC,created_at DESC,alternative_morphology_id DESC",
        (alternative_id,),).fetchall()
    morphology_components = {}
    for row in morphology_history:
        morphology_components[row["alternative_morphology_id"]] = connection.execute(
            "SELECT * FROM alternative_component WHERE alternative_morphology_id=? ORDER BY position",
            (row["alternative_morphology_id"],),).fetchall()
    relations = connection.execute("""
        SELECT r.*,lo.working_label low_label,hi.working_label high_label,
          (SELECT ae.collaborator_name_snapshot FROM activity_event ae
           WHERE ae.entity_type='alternative_relation' AND ae.entity_id=r.alternative_relation_id
           ORDER BY ae.activity_event_id DESC LIMIT 1) administrative_actor,
          (SELECT ae.access_role FROM activity_event ae
           WHERE ae.entity_type='alternative_relation' AND ae.entity_id=r.alternative_relation_id
           ORDER BY ae.activity_event_id DESC LIMIT 1) administrative_role
        FROM alternative_relation r JOIN alternative lo ON lo.alternative_id=r.alternative_low_id
        JOIN alternative hi ON hi.alternative_id=r.alternative_high_id
        WHERE r.is_current=1 AND (r.alternative_low_id=? OR r.alternative_high_id=?)
        ORDER BY r.phonological_parameter,r.alternative_relation_id
    """, (alternative_id, alternative_id)).fetchall()
    relation_history = connection.execute("""
        SELECT r.*,lo.working_label low_label,hi.working_label high_label,
          (SELECT ae.collaborator_name_snapshot FROM activity_event ae
           WHERE ae.entity_type='alternative_relation' AND ae.entity_id=r.alternative_relation_id
           ORDER BY ae.activity_event_id DESC LIMIT 1) administrative_actor,
          (SELECT ae.access_role FROM activity_event ae
           WHERE ae.entity_type='alternative_relation' AND ae.entity_id=r.alternative_relation_id
           ORDER BY ae.activity_event_id DESC LIMIT 1) administrative_role
        FROM alternative_relation r JOIN alternative lo ON lo.alternative_id=r.alternative_low_id
        JOIN alternative hi ON hi.alternative_id=r.alternative_high_id
        WHERE r.alternative_low_id=? OR r.alternative_high_id=?
        ORDER BY r.is_current DESC,r.created_at DESC,r.alternative_relation_id DESC
    """, (alternative_id, alternative_id)).fetchall()
    concept_alternatives = connection.execute(
        "SELECT alternative_id,working_label,created_at FROM alternative WHERE concept_id=? AND retired_at IS NULL ORDER BY alternative_id",
        (alternative["concept_id"],),).fetchall()
    nomenclature = calculate_nomenclature_preview(connection, alternative["concept_id"])
    renumber_history = connection.execute(
        "SELECT * FROM renumber_event WHERE concept_id=? ORDER BY created_at DESC,renumber_event_id DESC",
        (alternative["concept_id"],),).fetchall()
    renumber_changes = {event["renumber_event_id"]: connection.execute(
        "SELECT rc.*,a.working_label current_label FROM renumber_change rc JOIN alternative a USING(alternative_id) WHERE renumber_event_id=? ORDER BY alternative_id",
        (event["renumber_event_id"],),).fetchall() for event in renumber_history}
    concepts = connection.execute("SELECT concept_id,preferred_label FROM concept WHERE concept_id<>? ORDER BY preferred_label,concept_id", (alternative["concept_id"],)).fetchall()
    structural_history = connection.execute("SELECT * FROM activity_event WHERE entity_type='alternative' AND entity_id=? AND event_type IN ('alternative_retired','alternative_merged','alternative_split','alternative_moved') ORDER BY occurred_at DESC,activity_event_id DESC", (alternative_id,)).fetchall()
    current_occurrences = connection.execute("SELECT o.occurrence_id,o.original_gloss,s.source_name FROM assignment x JOIN occurrence o USING(occurrence_id) JOIN source s USING(source_id) WHERE x.alternative_id=? AND x.is_current=1 ORDER BY o.occurrence_id", (alternative_id,)).fetchall()
    return dict(alternative=alternative, morphology=morphology,
                morphology_history=morphology_history,
                morphology_components=morphology_components, relations=relations,
                relation_history=relation_history, concept_alternatives=concept_alternatives,
                nomenclature=nomenclature, renumber_history=renumber_history,
                renumber_changes=renumber_changes, parameters=PHONOLOGICAL_PARAMETERS,
                current_video=get_current_video(connection, alternative_id),
                concepts=concepts, structural_history=structural_history,
                current_occurrences=current_occurrences,
                message=message, error=error, relation_result=relation_result,
                structural_result=structural_result)


def _occurrence_mapping(form, prefix):
    return {int(key[len(prefix):]): value for key, value in form.items()
            if key.startswith(prefix) and key[len(prefix):].isdigit()}


def structured_working_label(form, fallback=None):

    number = form.get("working_label_number", "").strip()
    letter = form.get("working_label_letter", "").strip().lower()

    if number or letter:
        if not number.isdigit() or not 1 <= int(number) <= 10:
            raise ValueError
        if not re.fullmatch(r"[a-z]", letter):
            raise ValueError
        return f"{int(number)}{letter}"

    return fallback


def generated_working_label(conexion, concept_id, related_alternative_id=None):
    rows = conexion.execute("""
        SELECT working_label
        FROM alternative
        WHERE concept_id = ?
    """, (concept_id,)).fetchall()
    labels = [row["working_label"] for row in rows if row["working_label"]]
    parsed = [
        (int(match.group(1)), match.group(2))
        for label in labels
        for match in [re.fullmatch(r"(10|[1-9])([a-z])", label)]
        if match
    ]
    if not parsed and labels:
        return None

    if related_alternative_id is not None:
        base = conexion.execute("""
            SELECT working_label
            FROM alternative
            WHERE alternative_id = ? AND concept_id = ?
        """, (related_alternative_id, concept_id)).fetchone()
        if base is None:
            raise ValueError
        match = re.fullmatch(r"(10|[1-9])([a-z])", base["working_label"] or "")
        if match is None:
            return None
        number = int(match.group(1))
        used_letters = {letter for item_number, letter in parsed if item_number == number}
        for code in range(ord("a"), ord("z") + 1):
            letter = chr(code)
            if letter not in used_letters:
                return f"{number}{letter}"
        return None

    max_number = max((number for number, _ in parsed), default=0)
    if max_number >= 10:
        return None
    return f"{max_number + 1}a"


@alternatives_bp.route("/conceptos/<int:concept_id>/alternativas")
def alternativas(concept_id):

    conexion = conectar()

    concepto = conexion.execute("""
        SELECT concept_id, preferred_label
        FROM concept
        WHERE concept_id = ?
    """, (concept_id,)).fetchone()

    if concepto is None:
        conexion.close()
        return "El concepto no existe.", 404

    alternative_rows = conexion.execute("""
        SELECT a.alternative_id, a.working_label, a.original_code,
               a.created_at, a.retired_at,
               EXISTS(SELECT 1 FROM alternative_morphology m
                      WHERE m.alternative_id=a.alternative_id AND m.is_current=1)
                   AS has_current_morphology
        FROM alternative a
        WHERE a.concept_id = ?
        ORDER BY a.alternative_id
    """, (concept_id,)).fetchall()

    alternatives = {
        row["alternative_id"]: {
            "alternative": row,
            "occurrences": [],
            "relations": []
        }
        for row in alternative_rows
    }

    occurrence_rows = conexion.execute("""
        SELECT
            a.alternative_id,
            o.occurrence_id,
            o.occurrence_year,
            o.original_gloss,
            o.source_locator,
            o.hyperlink,
            s.source_name,
            s.start_year,
            s.end_year,
            s.end_year_status
        FROM assignment AS a
        JOIN occurrence AS o ON o.occurrence_id = a.occurrence_id
        JOIN source AS s ON s.source_id = o.source_id
        JOIN alternative AS alt
            ON alt.alternative_id = a.alternative_id
        WHERE alt.concept_id = ? AND a.is_current = 1
        ORDER BY a.alternative_id, o.occurrence_id
    """, (concept_id,)).fetchall()

    for occurrence in occurrence_rows:
        alternatives[occurrence["alternative_id"]]["occurrences"].append(
            occurrence
        )

    relation_rows = conexion.execute("""
        SELECT
            r.alternative_low_id AS alternative_a_id,
            r.alternative_high_id AS alternative_b_id,
            r.phonological_parameter,
            alternative_a.working_label AS alternative_a_working_label,
            alternative_b.working_label AS alternative_b_working_label,
            concept_a.preferred_label AS alternative_a_concept_label,
            concept_b.preferred_label AS alternative_b_concept_label
        FROM alternative_relation AS r
        JOIN alternative AS alternative_a
            ON alternative_a.alternative_id = r.alternative_low_id
        JOIN alternative AS alternative_b
            ON alternative_b.alternative_id = r.alternative_high_id
        JOIN concept AS concept_a
            ON concept_a.concept_id = alternative_a.concept_id
        JOIN concept AS concept_b
            ON concept_b.concept_id = alternative_b.concept_id
        WHERE r.is_current = 1
          AND (alternative_a.concept_id = ? OR alternative_b.concept_id = ?)
    """, (concept_id, concept_id)).fetchall()

    for relation in relation_rows:
        for alternative_id, related_id, related_label, related_concept in (
            (
                relation["alternative_a_id"],
                relation["alternative_b_id"],
                relation["alternative_b_working_label"],
                relation["alternative_b_concept_label"]
            ),
            (
                relation["alternative_b_id"],
                relation["alternative_a_id"],
                relation["alternative_a_working_label"],
                relation["alternative_a_concept_label"]
            )
        ):
            if alternative_id in alternatives:
                alternatives[alternative_id]["relations"].append({
                    "alternative_id": related_id,
                    "working_label": related_label,
                    "concept_label": related_concept,
                    "phonological_parameter": (
                        relation["phonological_parameter"]
                    )
                })

    alternative_groups = list(alternatives.values())
    for group in alternative_groups:
        group["current_video"] = get_current_video(
            conexion, group["alternative"]["alternative_id"]
        )
        group["relations"].sort(
            key=lambda relation: relation["alternative_id"]
        )

    conexion.close()

    return render_template(
        "alternativas.html",
        concepto=concepto,
        alternative_groups=alternative_groups,
        access_role=getattr(g,"current_access_role",None)
    )


@alternatives_bp.route("/alternativas/<int:alternative_id>/gestionar")
@requires_reviewer
def gestionar_alternativa(alternative_id):
    conexion = conectar()
    try:
        context = _management_context(conexion, alternative_id,
                                      message=request.args.get("message"))
        return render_template("gestionar_alternativa.html", **context)
    finally:
        conexion.close()


@alternatives_bp.route("/alternativas/<int:alternative_id>/gestionar", methods=["POST"])
@requires_reviewer
def actualizar_gestion_alternativa(alternative_id):
    action = request.form.get("action", "")
    conexion = conectar()
    relation_result = None
    structural_result = None
    try:
        if action == "morphology":
            if request.form.get("confirm") != "yes":
                raise AlternativeAdminError("Confirme la actualización de morfología.")
            count_raw = request.form.get("component_count", "").strip()
            _, changed = update_morphology(conexion, alternative_id, {
                "component_count": count_raw if count_raw.upper() != "N/A" else None,
                "component_count_not_applicable": count_raw.upper() == "N/A",
                "free_permutation": request.form.get("free_permutation"),
                "note": request.form.get("morphology_note"),
                "components": _components_from_form(request.form),
            }, _actor())
            message = "Morfología actualizada." if changed else "No hay cambios."
        elif action in ("preview_add_relation", "preview_retire_relation"):
            relation_result = relation_preview(
                conexion, alternative_id,
                action="add" if action == "preview_add_relation" else "retire",
                target_id=request.form.get("target_id"), parameter=request.form.get("parameter"),
                relation_id=request.form.get("relation_id"))
            message = "Revise el efecto sobre la nomenclatura y confirme."
        elif action == "confirm_relation":
            if request.form.get("confirm") != "yes":
                raise AlternativeAdminError("Confirme el cambio de relación.")
            _, event_id = apply_relation_change(
                conexion, alternative_id, action=request.form.get("relation_action"),
                target_id=request.form.get("target_id"), parameter=request.form.get("parameter"),
                relation_id=request.form.get("relation_id") or None,
                labels=_labels_from_form(request.form), mode=request.form.get("mode", "automatic"),
                reason=request.form.get("reason"), actor=_actor())
            message = "Relación actualizada." + (" La nomenclatura no cambia." if event_id is None else " Nomenclatura actualizada.")
        elif action == "apply_nomenclature":
            if request.form.get("confirm") != "yes":
                raise AlternativeAdminError("Confirme la actualización de nomenclatura.")
            concept_row = conexion.execute(
                "SELECT concept_id FROM alternative WHERE alternative_id=? AND retired_at IS NULL",
                (alternative_id,),).fetchone()
            if concept_row is None:
                abort(404)
            event_id = apply_direct_nomenclature(
                conexion, concept_row["concept_id"], _labels_from_form(request.form),
                mode=request.form.get("mode", "automatic"), reason=request.form.get("reason"), actor=_actor())
            message = "La nomenclatura ya está actualizada." if event_id is None else "Nomenclatura actualizada."
        elif action == "preview_retire":
            structural_result = retire_preview(conexion, alternative_id, _occurrence_mapping(request.form, "occurrence_")); message = "Revise el retiro estructural antes de confirmarlo."
        elif action == "confirm_retire":
            if request.form.get("confirm") != "yes": raise StructuralAlternativeError("Confirme que revisÃ³ los cambios.")
            apply_retire(conexion,alternative_id,_occurrence_mapping(request.form,"occurrence_"),reason=request.form.get("reason"),actor=_actor()); return redirect(url_for("alternatives.gestionar_alternativa",alternative_id=alternative_id,message="Alternativa retirada."))
        elif action == "preview_merge":
            structural_result=merge_preview(conexion,alternative_id,int(request.form.get("target_id",0)),request.form.get("relation_mode")); message="Revise la fusiÃ³n antes de confirmarla."
        elif action == "confirm_merge":
            if request.form.get("confirm") != "yes": raise StructuralAlternativeError("Confirme que revisÃ³ los cambios.")
            target_id=int(request.form.get("target_id"));apply_merge(conexion,alternative_id,target_id,request.form.get("relation_mode"),reason=request.form.get("reason"),actor=_actor());return redirect(url_for("alternatives.gestionar_alternativa",alternative_id=alternative_id,message=f"Alternativa fusionada en {target_id}."))
        elif action == "preview_split":
            structural_result=split_preview(conexion,alternative_id,_occurrence_mapping(request.form,"split_occurrence_"),request.form.get("new_count"));message="Revise la divisiÃ³n antes de confirmarla."
        elif action == "confirm_split":
            if request.form.get("confirm") != "yes": raise StructuralAlternativeError("Confirme que revisÃ³ los cambios.")
            apply_split(conexion,alternative_id,_occurrence_mapping(request.form,"split_occurrence_"),request.form.get("new_count"),reason=request.form.get("reason"),actor=_actor());return redirect(url_for("alternatives.gestionar_alternativa",alternative_id=alternative_id,message="Alternativa dividida."))
        elif action == "preview_move":
            structural_result=move_preview(conexion,alternative_id,int(request.form.get("destination_concept_id",0)));message="Revise el movimiento antes de confirmarlo."
        elif action == "confirm_move":
            if request.form.get("confirm") != "yes": raise StructuralAlternativeError("Confirme que revisÃ³ los cambios.")
            apply_move(conexion,alternative_id,int(request.form.get("destination_concept_id")),reason=request.form.get("reason"),actor=_actor());return redirect(url_for("alternatives.gestionar_alternativa",alternative_id=alternative_id,message="Alternativa movida."))
        else:
            raise AlternativeAdminError("Acción administrativa no válida.")
        context = _management_context(conexion, alternative_id, message=message,
                                      relation_result=relation_result, structural_result=structural_result)
        return render_template("gestionar_alternativa.html", **context)
    except (AlternativeAdminError, MorphologyValidationError,
            DuplicateCurrentRelationError, RelationNotFoundError,
            SelfRelationError, InvalidNomenclatureError, ValueError,
            sqlite3.IntegrityError) as error:
        if conexion.in_transaction:
            conexion.rollback()
        context = _management_context(conexion, alternative_id, error=str(error),
                                      relation_result=relation_result, structural_result=structural_result)
        return render_template("gestionar_alternativa.html", **context), 400
    finally:
        conexion.close()


@alternatives_bp.route("/alternativas/<int:alternative_id>/video")
@requires_reviewer
def gestionar_video(alternative_id):
    conexion=conectar()
    alternative=conexion.execute("""SELECT a.alternative_id,a.concept_id,a.working_label,c.preferred_label FROM alternative a JOIN concept c USING(concept_id) WHERE a.alternative_id=?""",(alternative_id,)).fetchone()
    if alternative is None: conexion.close(); abort(404)
    current=get_current_video(conexion,alternative_id); history=get_video_history(conexion,alternative_id); conexion.close()
    return render_template("gestionar_video_alternativa.html",alternative=alternative,current_video=current,video_history=history,error=request.args.get("error"))


@alternatives_bp.route("/alternativas/<int:alternative_id>/video",methods=["POST"])
@requires_reviewer
def actualizar_video(alternative_id):
    action=request.form.get("action",""); actor={"access_role":g.current_access_role,"collaborator_id":request.form.get("collaborator_id")}
    conexion=conectar()
    try:
        if action=="add": add_video(conexion,alternative_id,request.form.get("youtube_url"),actor)
        elif action=="replace":
            if request.form.get("confirm")!="yes": raise AlternativeVideoError("Confirme el reemplazo del video vigente.")
            replace_video(conexion,alternative_id,request.form.get("youtube_url"),actor)
        elif action=="retire":
            if request.form.get("confirm")!="yes": raise AlternativeVideoError("Confirme el retiro del video vigente.")
            retire_video(conexion,alternative_id,actor,request.form.get("comment"))
        else: raise AlternativeVideoError("Acción de video no válida.")
    except (AlternativeVideoError,InvalidYouTubeURL,sqlite3.IntegrityError) as error:
        return str(error),400
    finally: conexion.close()
    return redirect(url_for("alternatives.gestionar_video",alternative_id=alternative_id))


@alternatives_bp.route(
    "/conceptos/<int:concept_id>/alternativas/nueva",
    methods=["POST"]
)
def nueva_alternativa(concept_id):
    abort(404)
    try:
        working_label = structured_working_label(request.form)
    except ValueError:
        return "Número y letra no son válidos.", 400
    conexion = conectar()

    try:
        if conexion.execute(
            "SELECT 1 FROM concept WHERE concept_id = ?", (concept_id,)
        ).fetchone() is None:
            return "El concepto no existe.", 404

        conexion.execute("BEGIN IMMEDIATE")
        alternative_id = conexion.execute("""
            INSERT INTO alternative (concept_id, working_label)
            VALUES (?, ?)
        """, (concept_id, working_label)).lastrowid
        role = getattr(g, "current_access_role", None)
        if role:
            record_activity(conexion,"alternative_created",entity_type="alternative",
                            entity_id=alternative_id,
                            collaborator_id=request.form.get("collaborator_id"),
                            access_role=role)
        conexion.commit()
    except sqlite3.IntegrityError:
        conexion.rollback()
        return "No fue posible crear la alternativa.", 400
    finally:
        conexion.close()

    return redirect(url_for("alternatives.alternativas", concept_id=concept_id))


@alternatives_bp.route("/alternativas/<int:alternative_id>/editar")
def editar_alternativa(alternative_id):
    abort(404)
    conexion = conectar()
    alternative = conexion.execute("""
        SELECT alternative_id, concept_id, working_label
        FROM alternative
        WHERE alternative_id = ?
    """, (alternative_id,)).fetchone()
    conexion.close()

    if alternative is None:
        return "La alternativa no existe.", 404

    label = alternative["working_label"] or ""
    match = re.fullmatch(r"(10|[1-9])([a-z])", label)

    return render_template(
        "editar_alternativa.html",
        alternativa=alternative,
        working_label_number=match.group(1) if match else None,
        working_label_letter=match.group(2) if match else None,
        working_label_is_unstructured=bool(label) and match is None
    )


@alternatives_bp.route(
    "/alternativas/<int:alternative_id>/actualizar",
    methods=["POST"]
)
def actualizar_alternativa(alternative_id):
    abort(404)
    fallback = request.form.get("working_label_fallback", "").strip() or None
    try:
        working_label = structured_working_label(request.form, fallback)
    except ValueError:
        return "Número y letra no son válidos.", 400
    conexion = conectar()

    try:
        alternative = conexion.execute("""
            SELECT concept_id
            FROM alternative
            WHERE alternative_id = ?
        """, (alternative_id,)).fetchone()

        if alternative is None:
            return "La alternativa no existe.", 404

        conexion.execute("""
            UPDATE alternative
            SET working_label = ?
            WHERE alternative_id = ?
        """, (working_label, alternative_id))
        conexion.commit()
    except sqlite3.IntegrityError:
        conexion.rollback()
        return "No fue posible actualizar la alternativa.", 400
    finally:
        conexion.close()

    return redirect(
        url_for(
            "alternatives.alternativas",
            concept_id=alternative["concept_id"]
        )
    )
