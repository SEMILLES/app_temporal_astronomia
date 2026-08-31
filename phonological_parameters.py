PHONOLOGICAL_PARAMETERS = (
    "CM_1", "CM_2", "LOC_1", "LOC_2", "MOV_M1", "MOV_M2", "OR_M1",
    "OR_M2", "N_MANOS", "CM_bimanual", "LOC_bimanual", "MOV_bimanual",
    "OR_bimanual",
)


def validate_phonological_parameter(value, legacy_value=None):
    value = (value or "").strip()
    if not value:
        raise ValueError("El parámetro fonológico es obligatorio.")
    if value not in PHONOLOGICAL_PARAMETERS and value != legacy_value:
        raise ValueError("Parámetro fonológico no válido.")
    return value
