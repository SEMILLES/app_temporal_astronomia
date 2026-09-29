"""Component row parsing shared by classification and alternative management."""

def component_rows(form):
    if form.get("record_components") != "yes":
        return []
    rows=[]
    fields = ("position", "type", "alternative_id", "note")
    row_ids = form.getlist("component_row_id")
    if row_ids:
        if len(set(row_ids)) != len(row_ids) or any(not key.isdigit() for key in row_ids):
            raise ValueError("Identidad de componente inválida o repetida.")
        expected = {f"component_{key}_{field}" for key in row_ids for field in fields}
        actual = {key for key in form if key.startswith("component_") and key != "component_row_id"}
        if actual != expected or any(len(form.getlist(key)) != 1 for key in expected):
            raise ValueError("Estructura de componentes incompleta o desalineada.")
        values = [[form[f"component_{key}_{field}"] for field in fields] for key in row_ids]
    else:
        # Already-open legacy forms must supply aligned lists, without truncation.
        expected = {"component_" + field for field in fields}
        actual = {key for key in form if key.startswith("component_")}
        lists = [form.getlist("component_" + field) for field in fields]
        if actual != expected or not lists[0] or len({len(items) for items in lists}) != 1:
            raise ValueError("Estructura de componentes incompleta o desalineada.")
        values = [[items[index] for items in lists] for index in range(len(lists[0]))]
    for position,component_type,alternative,note in values:
        note=(note or "").strip();alternative=(alternative or "").strip()
        if component_type == "existing":
            if not alternative:
                raise ValueError("Seleccione una Alternativa vigente para el componente.")
            rows.append({"position":position,"component_alternative_id":alternative,
                         "component_label":None,"note":note})
        elif component_type == "unapproved":
            if not note or alternative:
                raise ValueError("El componente no aprobado o con dudas requiere una nota.")
            rows.append({"position":position,"component_alternative_id":None,
                         "component_label":None,"note":note})
        else:
            raise ValueError("Seleccione el tipo de componente.")
    return rows
