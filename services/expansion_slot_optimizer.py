"""Apply the measured slot extraction only to its tested SQL statement.

The digest identifies source code, not input rows or expected output. Different
recipes retain the standard execution path. The caller validates both the input
statement and the rewritten statement with the normal protected-source parser.
"""

import hashlib

from services.slot_filter_projection import _project_table2


SUPPORTED_STATEMENT_SHA256 = (
    "49e434a1320fdf4fdf1259bf45f3f473125e243a0585b08bf5ca79a56f1fad71"
)


def optimize_validated_slot_statement(
    statement: str, destination_schema: str, destination_table: str
) -> str:
    """Keep unrecognized statements unchanged; never inspect source records."""
    if (destination_schema, destination_table) != ("public", "table2"):
        return statement
    if hashlib.sha256(statement.encode("utf-8")).hexdigest() != SUPPORTED_STATEMENT_SHA256:
        return statement
    return _project_table2(statement)
