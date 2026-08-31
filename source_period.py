def format_source_period(start_year, end_year):
    if start_year is None and end_year is None:
        return ""
    if start_year is not None and end_year == start_year:
        return str(start_year)
    if start_year is not None and end_year is not None:
        return f"{start_year}–{end_year}"
    return str(start_year or end_year)


def validate_occurrence_year(connection, source_id, value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text.isdigit() or len(text) != 4 or int(text) < 1:
        raise ValueError("El año de la ocurrencia no es válido.")
    source = connection.execute(
        "SELECT start_year,end_year FROM source WHERE source_id=?", (source_id,)
    ).fetchone()
    if source is None:
        raise ValueError("La fuente no existe.")
    year = int(text)
    if source[0] is not None and year < source[0]:
        raise ValueError("El año de la ocurrencia está fuera del periodo de la fuente.")
    if source[1] is not None and year > source[1]:
        raise ValueError("El año de la ocurrencia está fuera del periodo de la fuente.")
    return year
