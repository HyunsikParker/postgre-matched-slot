"""Extract table2 payload fields only for slots whose declared number matches.

table1 and table3 keep the official text because later foreign keys store their
identity values. table4, table5, and table6 keep the passing one-row values
projection. table2 still scans the source once, reads raw->values once, and
then evaluates type, weight, and gap only for a slot that already matched.
"""

from __future__ import annotations

import re

from services.identity_stable_projection import project_stable_recipe


MECHANISM_ID = "table2-matched-slot-extraction-v1"
LINEAGE_ID = "filtered-slot-then-field-extraction-v1"
OFFICIAL_TABLES = frozenset({"public.table1", "public.table3"})
STABLE_TABLES = frozenset({"public.table4", "public.table5", "public.table6"})
SLOT_TABLE = "public.table2"
SOURCE_FROM = "FROM {{source}} AS source_row"
SOURCE_PATH = "source_row.raw -> 'values'"
POSITION_ALIAS = (
    ") AS position_data(position_number, declared_number_text, "
    "type_text, weight_text, gap_text)"
)
TUPLE_RE = re.compile(
    r"\((\d+)::smallint, "
    r"source_row\.raw -> 'values' ->> '([a-z0-9_]+)', "
    r"source_row\.raw -> 'values' ->> '([a-z0-9_]+)', "
    r"source_row\.raw -> 'values' ->> '([a-z0-9_]+)', "
    r"source_row\.raw -> 'values' ->> '([a-z0-9_]+)'\)"
)
BANNED_SNIPPETS = (
    "jsonb_to_record",
    "jsonb_populate_record",
    "create function",
    "security definer",
    "set storage",
    "raw_data_plain",
    "as hoisted",
)


def project_slot_recipe(table_name: str, sql: str) -> str:
    """Return candidate SQL. The official text passed in is not modified."""
    if table_name in OFFICIAL_TABLES:
        return sql
    if table_name in STABLE_TABLES:
        return project_stable_recipe(table_name, sql)
    if table_name != SLOT_TABLE:
        raise ValueError(f"unexpected destination table: {table_name}")
    return _project_table2(sql)


def candidate_beats_same_machine(
    candidate_seconds: float,
    first_official_seconds: float,
    repeat_official_seconds: float,
) -> bool:
    """True only when the candidate is strictly faster than both official passes."""
    measured = (candidate_seconds, first_official_seconds, repeat_official_seconds)
    if any(value < 0 for value in measured):
        raise ValueError("measured seconds must be non-negative")
    return (
        candidate_seconds < first_official_seconds
        and candidate_seconds < repeat_official_seconds
    )


def candidate_beats_control(candidate_seconds: float, control_seconds: float) -> bool:
    """True only when the candidate is strictly faster than the passing projection."""
    if candidate_seconds < 0 or control_seconds < 0:
        raise ValueError("measured seconds must be non-negative")
    return candidate_seconds < control_seconds


def _case(tuples: list[tuple[str, str, str, str, str]], key_index: int, alias: str) -> str:
    branches = "\n".join(
        f"            WHEN {item[0]} THEN projected.values_json ->> '{item[key_index]}'"
        for item in tuples
    )
    return (
        "CASE chosen.position_number\n"
        f"{branches}\n"
        f"        END AS {alias}"
    )


def _table2_from(tuples: list[tuple[str, str, str, str, str]]) -> str:
    slot_rows = ",\n        ".join(
        f"({item[0]}::smallint, projected.values_json ->> '{item[1]}')"
        for item in tuples
    )
    return (
        "FROM {{source}} AS source_row\n"
        "CROSS JOIN LATERAL (\n"
        "    SELECT source_row.raw -> 'values' AS values_json\n"
        "    OFFSET 0\n"
        ") AS projected\n"
        "CROSS JOIN LATERAL (\n"
        "    SELECT slot.position_number,\n"
        "           slot.declared_number_text\n"
        "    FROM (VALUES\n"
        f"        {slot_rows}\n"
        "    ) AS slot(position_number, declared_number_text)\n"
        "    WHERE NULLIF(BTRIM(slot.declared_number_text), '')::smallint "
        "= slot.position_number\n"
        ") AS chosen\n"
        "CROSS JOIN LATERAL (\n"
        "    SELECT chosen.position_number,\n"
        "           chosen.declared_number_text,\n"
        f"        {_case(tuples, 2, 'type_text')},\n"
        f"        {_case(tuples, 3, 'weight_text')},\n"
        f"        {_case(tuples, 4, 'gap_text')}\n"
        ") AS position_data"
    )


def _project_table2(sql: str) -> str:
    if sql.count(SOURCE_FROM) != 1:
        raise ValueError("table2 must read the source once")
    tuples = TUPLE_RE.findall(sql)
    if [int(item[0]) for item in tuples] != list(range(1, 15)):
        raise ValueError("table2 slot tuples are not positions 1 through 14")
    start = sql.index(SOURCE_FROM)
    end = sql.index(POSITION_ALIAS, start)
    if sql.find(POSITION_ALIAS, end + len(POSITION_ALIAS)) != -1:
        raise ValueError("table2 has more than one position_data alias")
    rewritten = sql[:start] + _table2_from(tuples) + sql[end + len(POSITION_ALIAS):]
    lowered = rewritten.lower()
    for snippet in BANNED_SNIPPETS:
        if snippet in lowered:
            raise ValueError(f"slot recipe contains a closed construct: {snippet}")
    if rewritten.count("{{source}}") != 1:
        raise ValueError("slot recipe must keep one source placeholder")
    if rewritten.count(SOURCE_PATH) != 1:
        raise ValueError("the driving scan must project raw -> 'values' once")
    if rewritten.count("CASE chosen.position_number") != 3:
        raise ValueError("type, weight, and gap must each sit behind one CASE")
    if "source_row.raw -> 'values' ->>" in rewritten:
        raise ValueError("table2 still extracts fields from the raw path")
    if "OFFSET 0" not in rewritten:
        raise ValueError("projection is missing its optimization fence")
    return rewritten
