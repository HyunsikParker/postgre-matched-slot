"""Project raw -> 'values' only on statements that do not assign a stored identity.

table1 and table3 keep the official text. Their identity values are copied into
later foreign keys, and a plan change there changes those keys. The remaining
statements keep the official source scan and read one LATERAL projection.
"""

from __future__ import annotations

from services.lateral_values_projection import project_recipe


MECHANISM_ID = "identity-stable-values-projection-v1"
OFFICIAL_TABLES = frozenset({"public.table1", "public.table3"})
PROJECTED_TABLES = frozenset({"public.table2", "public.table4", "public.table5", "public.table6"})


def project_stable_recipe(table_name: str, sql: str) -> str:
    """Return candidate SQL. Official table1 and table3 text is returned unchanged."""
    if table_name in OFFICIAL_TABLES:
        return sql
    if table_name not in PROJECTED_TABLES:
        raise ValueError(f"unexpected destination table: {table_name}")
    return project_recipe(sql)


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
