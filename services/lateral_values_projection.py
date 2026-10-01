"""Project raw -> 'values' once beside the official source scan.

The official recipes stay on disk. The source table remains the driving scan.
A one-row LATERAL subquery reads that jsonb path once behind OFFSET 0, and the
existing field operators read the projected value. This does not replace the
FROM clause with a second scan, copy raw_data, or build a record.
"""

from __future__ import annotations


MECHANISM_ID = "lateral-values-projection-v1"
SOURCE_FROM = "FROM {{source}} AS source_row"
SOURCE_PATH = "source_row.raw -> 'values'"
LATERAL_FROM = """FROM {{source}} AS source_row
CROSS JOIN LATERAL (
    SELECT source_row.raw -> 'values' AS values_json
    OFFSET 0
) AS projected"""
BANNED_SNIPPETS = (
    "jsonb_to_record",
    "jsonb_populate_record",
    "create function",
    "security definer",
    "set storage",
    "raw_data_plain",
    "as hoisted",
)


def project_recipe(sql: str) -> str:
    """Return candidate SQL. The official text passed in is not modified."""
    if sql.count(SOURCE_FROM) != 1:
        raise ValueError("recipe must read {{source}} once")
    path_count = sql.count(SOURCE_PATH)
    if path_count < 1:
        raise ValueError("recipe does not walk source_row.raw -> 'values'")
    rewritten = sql.replace(SOURCE_PATH, "projected.values_json")
    rewritten = rewritten.replace(SOURCE_FROM, LATERAL_FROM, 1)
    lowered = rewritten.lower()
    for snippet in BANNED_SNIPPETS:
        if snippet in lowered:
            raise ValueError(f"projected recipe contains a closed construct: {snippet}")
    if rewritten.count("{{source}}") != 1:
        raise ValueError("projected recipe must keep one source placeholder")
    if rewritten.count(SOURCE_PATH) != 1:
        raise ValueError("the driving scan must project raw -> 'values' once")
    if rewritten.count("projected.values_json") != path_count:
        raise ValueError("projected jsonb references do not match the official paths")
    if "OFFSET 0" not in rewritten:
        raise ValueError("projection is missing its optimization fence")
    return rewritten


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
