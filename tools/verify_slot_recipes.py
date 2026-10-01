#!/usr/bin/env python3
"""Check 48 generated rows in a temporary cluster, not a performance benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from generate_database import DatasetGenerator, RAW_SCHEMA_NAME, SCHEMA_SQL
from services.postgres_service import PostgresAdminService
from tests.test_challenge_expansion_recipes import RECIPE_DESTINATIONS, RECIPE_DIR
from utils import sql_ident

ROW_COUNT = 48
HASHES = {
    "01_table3.sql": "060d6bae032f4c4edf0f2db91660b493a0f0711965e7c2a56953c7f18c0fd925",
    "02_table4.sql": "806afb28566b6a227a8410e9ec8a8fa1c5a0f8c055a67575a51bb06afd955d94",
    "03_table1.sql": "acd7866048ee447783b8fe6d5f7921a8dd2861cda22883bd9300919d7721a999",
    "04_table5.sql": "e227c0c9d51674d805aa99bf58248392e58940b095940e24a5d4bbb1f3011d7c",
    "05_table6.sql": "e9d5a66b033685d416b0a2c6572cd4a9fd2d64d07e8e2cc024f1f9417eb70970",
    "06_table2.sql": "3954afa05d595de9c3efb2526a9b8f82b3c56d39460dc60776e87f29ab18cc28",
}


def run_check(pg_bin: Path) -> dict:
    for filename, expected in HASHES.items():
        if hashlib.sha256((RECIPE_DIR / filename).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Official recipe changed: {filename}")
    generator_hash = hashlib.sha256((ROOT / "generate_database.py").read_bytes()).hexdigest()
    if generator_hash != "2b57fa720ebc49a0afde17fd6c213f239e7bd65487eb47e425d8efd85f01eadf":
        raise RuntimeError("Official generator changed")

    destinations = [
        {"table_name": table, "version_title": "Synthetic expansion",
         "sql": (RECIPE_DIR / filename).read_text(encoding="utf-8")}
        for filename, table in RECIPE_DESTINATIONS
    ]
    tables = [table.split(".", 1)[1] for _, table in RECIPE_DESTINATIONS]

    def prepare():
        return PostgresAdminService._prepare_cross_table_expansion(
            "public.raw_data", [RAW_SCHEMA_NAME], destinations
        )

    with patch("services.postgres_service.optimize_validated_slot_statement",
               side_effect=lambda sql, schema, table: sql):
        baseline = prepare()
    candidate = prepare()
    if "CASE chosen.position_number" not in candidate["destinations"][-1]["statements"][0]:
        raise RuntimeError("Application did not select the slot rewrite")

    def recipe_sql(prepared):
        return ";\n".join(sql for item in prepared["destinations"]
                           for sql in item["statements"]) + ";"

    env = dict(os.environ)
    for name in ("PGPASSWORD", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"):
        env.pop(name, None)

    def command(argv, input_text=None):
        result = subprocess.run(argv, input=input_text, text=True, encoding="utf-8",
                                capture_output=True, env=env)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout)[-2000:])
        return result.stdout

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="slot-functional-") as temp:
        cluster = Path(temp) / "cluster"
        command([str(pg_bin / "initdb"), "-D", str(cluster), "-A", "trust",
                 "--username=slot_check", "--no-locale", "--encoding=UTF8"])
        options = shlex.join(["-p", str(port), "-h", "127.0.0.1", "-k", temp])
        started = False
        try:
            command([str(pg_bin / "pg_ctl"), "-D", str(cluster), "-l",
                     str(Path(temp) / "server.log"), "-o", options, "-w", "start"])
            started = True

            def psql(sql, database="kaggle_accept"):
                return command([str(pg_bin / "psql"), "-h", "127.0.0.1", "-p", str(port),
                                "-U", "slot_check", "-d", database, "-X", "-q", "-A", "-t",
                                "-v", "ON_ERROR_STOP=1"], sql)

            psql("CREATE DATABASE kaggle_accept;", "postgres")
            psql("SET TIME ZONE 'UTC';\n" + SCHEMA_SQL)
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            for row in DatasetGenerator.build(ROW_COUNT):
                writer.writerow(row.copy_values())
            psql("COPY public.raw_data (raw_id, raw_hash, raw_ingested_at, raw_schema, "
                 "raw_tab, raw) FROM STDIN WITH (FORMAT csv);\n" + buffer.getvalue() + "\\.\n")

            service = PostgresAdminService.__new__(PostgresAdminService)
            service.sql_username = "slot_check"

            def local_catalog_query(remote_command, cancel_event=None):
                parts = shlex.split(remote_command)
                if not parts or parts[0] != "psql" or "-c" not in parts:
                    raise RuntimeError("Unexpected function-validation command")
                return psql(parts[parts.index("-c") + 1])

            service.run_remote_command = local_catalog_query
            service._validate_cross_table_expansion_functions("kaggle_accept",
                                                              candidate["destinations"])
            table_list = ", ".join("'" + table + "'" for table in tables)
            fields = psql(f"""
SELECT c.relname || E'\\t' || a.attname
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
JOIN pg_attribute a ON a.attrelid=c.oid
WHERE n.nspname='public' AND c.relname IN ({table_list})
AND a.attnum>0 AND NOT a.attisdropped AND a.attidentity=''
ORDER BY c.relname,a.attnum;
""")
            # Identity counters are bookkeeping; foreign-key columns stay in the comparison.
            columns = {table: [] for table in tables}
            for line in fields.splitlines():
                table, column = line.split("\t", 1)
                columns[table].append(column)
            if any(not fields for fields in columns.values()):
                raise RuntimeError("Missing destination columns")
            selected = {table: ", ".join(map(sql_ident, columns[table])) for table in tables}
            snapshots = "\n".join(
                f"CREATE TEMP TABLE {sql_ident('expected_' + table)} AS SELECT "
                f"{selected[table]} FROM public.{sql_ident(table)};" for table in tables)

            def compare(label):
                return "\n".join(
                    f"SELECT '{label}|{table}|' || count(*) FROM ((SELECT {selected[table]} "
                    f"FROM public.{sql_ident(table)} EXCEPT ALL SELECT * FROM "
                    f"{sql_ident('expected_' + table)}) UNION ALL (SELECT * FROM "
                    f"{sql_ident('expected_' + table)} EXCEPT ALL SELECT {selected[table]} "
                    f"FROM public.{sql_ident(table)})) differences;" for table in tables)

            truncate = "TRUNCATE TABLE " + ", ".join("public." + sql_ident(t)
                                                       for t in tables) + " RESTART IDENTITY;"
            count = "+".join("(SELECT count(*) FROM public." + sql_ident(t) + ")"
                             for t in tables)
            source_sql = ("SELECT md5(string_agg(raw_id::text || raw::text || raw_hash::text, "
                          "'|' ORDER BY raw_id)) FROM public.raw_data;")
            source_before = psql(source_sql).strip()
            output = psql("\n".join([
                "SET TIME ZONE 'UTC';", "BEGIN;", recipe_sql(baseline), "COMMIT;", snapshots,
                truncate, "BEGIN;", recipe_sql(candidate), "COMMIT;", compare("normal"),
                truncate, "BEGIN;", candidate["destinations"][0]["statements"][0] + ";",
                "ROLLBACK;", f"SELECT 'rollback|' || ({count});",
                # nextval allocations survive rollback; use the documented reset/full rerun.
                truncate, "BEGIN;", recipe_sql(candidate), "COMMIT;", compare("recovery"),
                f"SELECT 'rows|' || ({count});",
            ]))
            source_after = psql(source_sql).strip()
            gaps = {label: {} for label in ("normal", "recovery")}
            rollback = rows = None
            for line in output.splitlines():
                parts = line.split("|")
                if parts[0] in gaps:
                    gaps[parts[0]][parts[1]] = int(parts[2])
                elif parts[0] == "rollback":
                    rollback = int(parts[1])
                elif parts[0] == "rows":
                    rows = int(parts[1])
            passed = (all(len(v) == 6 and sum(v.values()) == 0 for v in gaps.values())
                      and rollback == 0 and rows is not None and rows > 0
                      and source_before == source_after)
            return {"passed": passed, "fixture_rows": ROW_COUNT, "destination_rows": rows,
                    "row_gaps": gaps, "rollback_rows": rollback,
                    "source_unchanged": source_before == source_after,
                    "native_function_validator_passed": True,
                    "full_windows_ssh_linux_path_exercised": False,
                    "performance_or_official_score": False,
                    "postgres_version": command([str(pg_bin / "psql"), "--version"]).strip()}
        finally:
            if started:
                command([str(pg_bin / "pg_ctl"), "-D", str(cluster), "-m", "immediate",
                         "-w", "stop"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", type=Path, help="Directory containing PostgreSQL 17 tools")
    args = parser.parse_args()
    found = shutil.which("initdb")
    pg_bin = args.pg_bin or (Path(found).parent if found else None)
    if pg_bin is None:
        parser.error("Install PostgreSQL 17 tools or pass --pg-bin")
    result = run_check(pg_bin)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
