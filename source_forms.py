from source_details import SOURCE_TYPES

SOURCE_FIELDS = ("source_name", "source_type", "source_reference", "legacy_source_code", "source_scope",
                 "format_original", "format_detail", "start_year", "end_year", "end_year_status",
                 "region_description", "characterization", "reported_entry_count")


def parse_source_years(form):

    def parse(name):
        value = form.get(name, "").strip()
        if not value:
            return None
        if not value.isdigit() or len(value) != 4:
            raise ValueError
        return int(value)

    start_year = parse("start_year")
    end_year = parse("end_year")
    status = form.get("end_year_status", "").strip() or None
    if status not in (None, "known", "ongoing", "unknown"):
        raise ValueError
    if (start_year is not None or end_year is not None) and status is None:
        raise ValueError
    if status == "known" and end_year is None:
        raise ValueError
    if status in ("ongoing", "unknown") and end_year is not None:
        raise ValueError
    if start_year is not None and end_year is not None and start_year > end_year:
        raise ValueError
    return start_year, end_year, status


def source_form_values(form):
    start_year, end_year, status = parse_source_years(form)
    source_scope = form.get("source_scope", "").strip() or None
    if source_scope not in (None, "INSTITUTIONAL", "PERSONAL"):
        raise ValueError
    reported_entry_count_value = form.get("reported_entry_count", "").strip()
    if reported_entry_count_value:
        if not reported_entry_count_value.isdigit():
            raise ValueError
        reported_entry_count = int(reported_entry_count_value)
    else:
        reported_entry_count = None
    source_type=form.get("source_type", "").strip()
    if source_type not in SOURCE_TYPES: raise ValueError
    return (
        form.get("source_name", "").strip(), source_type,
        form.get("source_reference", "").strip() or None,
        form.get("legacy_source_code", "").strip() or None,
        source_scope,
        form.get("format_original", "").strip() or None,
        form.get("format_detail", "").strip() or None,
        start_year,
        end_year,
        status,
        form.get("region_description", "").strip() or None,
        form.get("characterization", "").strip() or None,
        reported_entry_count,
    )


def source_insert_values(form):
    values = source_form_values(form)
    if not values[0]:
        raise ValueError
    return values
