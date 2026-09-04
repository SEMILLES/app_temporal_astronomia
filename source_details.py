import re
import unicodedata

SOURCE_TYPES = {
    "MATERIAL_IMPRESO": "Material impreso",
    "VIDEO_POR_SENA": "Video por seña",
    "UN_VIDEO_VARIAS_SENAS": "Un video con varias señas",
    "VARIOS_VIDEOS_VARIAS_SENAS": "Varios videos con varias señas",
    "OTRO": "Otro",
}
DETAIL_STATUSES = frozenset(("VALUE", "NA", "UNKNOWN"))

def source_type_labels(source_type):
    if source_type == "MATERIAL_IMPRESO": return ("Submaterial / sección", "Página", True)
    if source_type == "VIDEO_POR_SENA": return ("Título / identificador del video", None, False)
    if source_type in ("UN_VIDEO_VARIAS_SENAS", "VARIOS_VIDEOS_VARIAS_SENAS"):
        return ("Título del video", "Tiempo", True)
    return ("Detalle Fuente 1", "Detalle Fuente 2", True)

def normalize_detail(status, value, *, applicable=True, kind=None, allow_incomplete=False):
    value = (value or "").strip() or None
    status = (status or "").strip().upper() or None
    if not applicable: return "NA", None
    if allow_incomplete and status is None: return None, value
    if status not in DETAIL_STATUSES: raise ValueError("Debe indicar Dato, N/A o Desconocido para cada detalle aplicable.")
    if status == "VALUE":
        if value is None: raise ValueError("Un detalle marcado como Dato requiere un valor.")
        if kind == "page" and not value.isdigit(): raise ValueError("La página debe contener únicamente números.")
        if kind == "time" and not re.fullmatch(r"(?:\d{1,2}):[0-5]\d(?::[0-5]\d)?", value):
            raise ValueError("El tiempo debe usar M:SS, MM:SS o H:MM:SS.")
        return status, value
    if value is not None: raise ValueError("N/A y Desconocido no admiten texto.")
    return status, None

def normalize_occurrence_details(source_type, status1, value1, status2, value2, *, allow_incomplete=False):
    label1, label2, detail2_applicable = source_type_labels(source_type)
    kind1 = None
    kind2 = "page" if source_type == "MATERIAL_IMPRESO" else "time" if source_type in ("UN_VIDEO_VARIAS_SENAS", "VARIOS_VIDEOS_VARIAS_SENAS") else None
    s1, v1 = normalize_detail(status1, value1, kind=kind1, allow_incomplete=allow_incomplete)
    s2, v2 = normalize_detail(status2, value2, applicable=detail2_applicable, kind=kind2, allow_incomplete=allow_incomplete)
    return s1, v1, s2, v2

def _comparison(value):
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[\s-]+", "", value).casefold()

def catalog_source_reference(occurrence):
    source_type = occurrence["source"]["source_type"]
    d1 = occurrence.get("source_detail_1") or None
    d2 = occurrence.get("source_detail_2") or None
    if source_type == "VIDEO_POR_SENA":
        return None if d1 and _comparison(d1) == _comparison(occurrence.get("original_gloss")) else d1
    if source_type == "MATERIAL_IMPRESO":
        return " · ".join(filter(None, (d1, f"p. {d2}" if d2 else None))) or None
    if source_type in ("UN_VIDEO_VARIAS_SENAS", "VARIOS_VIDEOS_VARIAS_SENAS"):
        result = " · ".join(filter(None, (d1, d2)))
        if result: return result
        return "Referencia desconocida" if (occurrence.get("source_detail_1_status") == "UNKNOWN" and occurrence.get("source_detail_2_status") == "UNKNOWN") else None
    return " · ".join(filter(None, (d1, d2))) or None

def analysts_may_create_sources(connection):
    row = connection.execute("SELECT setting_value FROM application_setting WHERE setting_key='analyst_source_creation'").fetchone()
    return bool(row and row[0] == "1")
