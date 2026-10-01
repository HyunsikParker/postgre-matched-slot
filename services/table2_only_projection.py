"""Keep the matched-slot extraction on table2 and leave the other statements official.

table4, table5, and table6 need one or two keys. The current entry still
projects the whole values object on those scans. This candidate returns those
statements, and table1 and table3, as official text. table2 stays on the
passing slot filter. table1 and table3 stay official because later foreign
keys store their identity values.
"""

from __future__ import annotations

from services.slot_filter_projection import SLOT_TABLE, project_slot_recipe


MECHANISM_ID = "table2-only-matched-slot-v1"
LINEAGE_ID = "official-siblings-plus-table2-slot-v1"
OFFICIAL_TABLES = frozenset(
    {
        "public.table1",
        "public.table3",
        "public.table4",
        "public.table5",
        "public.table6",
    }
)


def project_table2_only(table_name: str, sql: str) -> str:
    """Return candidate SQL. Official text passed in is not modified."""
    if table_name == SLOT_TABLE:
        return project_slot_recipe(table_name, sql)
    if table_name in OFFICIAL_TABLES:
        return sql
    raise ValueError(f"unexpected destination table: {table_name}")
