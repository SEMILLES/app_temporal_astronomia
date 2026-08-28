import re
import unicodedata


class InvalidConceptLabel(ValueError):
    pass


def normalize_concept_label(value):
    if not isinstance(value, str):
        raise InvalidConceptLabel("La etiqueta del concepto no es válida.")

    value = unicodedata.normalize("NFC", value).upper()
    if not value.strip():
        raise InvalidConceptLabel("La etiqueta del concepto es obligatoria.")

    components = value.split("/")
    if any(not component.strip(" -\t\r\n\f\v") for component in components):
        if len(components) == 1:
            raise InvalidConceptLabel("La etiqueta del concepto es obligatoria.")
        raise InvalidConceptLabel(
            "La etiqueta debe tener contenido a ambos lados de cada '/'."
        )

    normalized_components = []
    for component in components:
        if any(
            not (character.isalnum() or character.isspace() or character == "-")
            for character in component
        ):
            raise InvalidConceptLabel(
                "La etiqueta solo admite letras, números, espacios, '-' y '/'."
            )
        normalized = re.sub(r"[\s-]+", "-", component).strip("-")
        if not normalized:
            raise InvalidConceptLabel("La etiqueta del concepto es obligatoria.")
        normalized_components.append(normalized)

    result = "/".join(normalized_components)
    if not result:
        raise InvalidConceptLabel("La etiqueta del concepto es obligatoria.")
    return result


def human_concept_label(preferred_label):
    if preferred_label is None:
        return None
    return preferred_label.replace("-", " ")


def alternative_display_label(preferred_label, working_label):
    if not working_label:
        return None
    if not preferred_label:
        return working_label
    return f"{preferred_label}-{working_label}"
