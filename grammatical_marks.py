GRAMMATICAL_MARK_VOCABULARIES = {
    "gender": (
        "SIN-MARCA", "MASC-O", "MASC-HOMBRE", "FEM-A", "FEM-MUJER", "OTRA",
    ),
    "plural": (
        "SIN-MARCA", "REDUP.", "SEÑA-MUCHO", "SEÑA-DIFERENTES",
        "SEÑA-PERSONAS", "SEÑA-ELLOS", "MOV. AMPLIO", "CLASIF", "OTRA",
    ),
    "agentive": ("N/A", "C-Baby", "K (P-ASL)"),
    "conjugated_form": ("NO", "SÍ"),
    "negation": ("SIN-NEG", "CON-NEG"),
}


class InvalidGrammaticalMark(ValueError):
    pass


def normalize_mark(value):
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def validate_grammatical_marks(values, legacy_values=None):
    legacy_values = legacy_values or {}
    normalized = {}
    for name, allowed in GRAMMATICAL_MARK_VOCABULARIES.items():
        value = normalize_mark(values.get(name))
        if value not in (None, *allowed) and value != legacy_values.get(name):
            raise InvalidGrammaticalMark(f"Valor no válido para {name}.")
        normalized[name] = value
    return normalized
