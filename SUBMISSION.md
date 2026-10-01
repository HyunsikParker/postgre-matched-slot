# Matched-slot extraction for table2

## Change

The official `table2` recipe expands 14 possible positions from each Raw record. This entry projects `raw -> 'values'` once, checks which positions have a matching declared number, and extracts type, weight, and gap through `CASE` expressions for those positions. An `OFFSET 0` fence keeps the values projection separate.

The other five recipes stay on their official SQL. In particular, the `table1` and `table3` insertion paths remain unchanged because later tables store their generated identities as foreign keys.

The application first validates the original statement, checks its normalized SQL digest and destination, applies the rewrite, and validates the result again. A different recipe or destination uses the original path. The digest identifies SQL text, not dataset contents or expected answers. The optimizer does not inspect data, select a benchmark-specific answer, or cache expanded rows.

The hook is inside the existing measured preparation phase. The SSH architecture, Raw validation, function checks, transaction, locks, manifests, version registration, dump logic, and report instrumentation remain in the upstream execution path. No migrations or database configuration changes are needed for this entry. The additional projection modules retain the measured candidate's source; the production hook calls only the table2 transformation.

## Install and run

The source is based on upstream commit `5dfde9d967425982229b92d6ffe98efcd74612e9`, under Apache 2.0. The official generator and all six SQL files are byte-identical to that commit. AI coding assistants were used during development.

On Windows, create an isolated environment with Python 3.14.6:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.verified.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe main.py
```

The verified top-level packages are listed in `requirements.verified.txt`. The original `requirements.txt` is also retained. Setup of the Linux PostgreSQL server, dedicated role, SSH connection, generator, and initial metadata follows [GENERATE_DATABASE_README.md](./GENERATE_DATABASE_README.md). Do not change its generated inputs, schemas, seed, or Raw records.

In the Manager, choose `public.raw_data`, Raw scope `group001` to `group001`, and the six destinations in filename order: `table3`, `table4`, `table1`, `table5`, `table6`, `table2`. Load the original recipes and run them as one Expand batch. The application applies the table2 rewrite during preparation; there is no alternate recipe to install. Export the detailed performance report and retain its version and dependency records.

For an official comparison, use identical clean generated databases and the same Windows/Linux hardware for upstream and entry. Record the report's `MEASURED PROCESSING TOTAL`, every destination count, version/dump consistency, PostgreSQL configuration, peak memory, and temporary storage. This repository does not contain an official score or a completed full-application performance comparison.

## Validation

The complete unit suite passed on Windows with Python 3.14.6: 11 tests. Application compilation also passed. Tests cover the actual application hook, unchanged sibling recipes, custom-recipe fallback, validation before rewriting, and rejection of an unsafe rewritten statement.

The functional database check uses 48 rows from the unchanged generator in a temporary PostgreSQL 17 cluster. It checks the production-prepared SQL with the native function validator, compares all six tables' non-identity columns (including foreign keys), checks that Raw data did not change, and tests a rollback followed by reset and full rerun. The current result has 273 destination rows, zero differences in normal and recovery passes, and zero rows persisted from the rolled-back transaction.

On Linux or macOS, after installing the Python requirements and PostgreSQL 17 tools, run this check as a non-root user:

```bash
python tools/verify_slot_recipes.py --pg-bin /path/to/postgresql17/bin
```

It creates and removes its own temporary cluster. It does not connect to an existing database. This is a correctness check, not a performance measurement or a Windows-to-Linux SSH integration test. The full GUI/SSH workflow has not been exercised by the new validation run.

## Recorded local measurements

These measurements were recorded before app integration, using the same frozen SQL transformation. They time SQL execution in an isolated local PostgreSQL checker. They are not `MEASURED PROCESSING TOTAL`, official rankings, or a cross-machine speed comparison.

| Raw rows | First official SQL pass | Repeated official SQL pass | Table2-only candidate | Destination differences |
|---:|---:|---:|---:|---:|
| 100,000 | 6.556757 s | 6.476469 s | 5.389421 s | 0 |
| 1,000,000 | 89.734122 s | 91.745764 s | 75.621551 s | 0 |

The 1M pass produced 5,321,553 destination rows. The recorded peak PostgreSQL-process RSS was 1,090,640 KiB; that is not total client/server memory. The Raw relation occupied 2,112,102,400 bytes. Peak temporary storage and full-application resource use were not measured.

The 1M checker disabled autovacuum in its private cluster and disabled hash aggregation and parallel gather in its session to keep identity allocation repeatable between comparisons. Those are checker controls, not application or submitted SQL changes. Do not treat these timings as evidence for a default-configured sponsor system. The historical SQL checker is not bundled as an official benchmark harness; the app-level reproduction procedure above is the judging path.

## Interruption and recovery

No partial-work reuse or checkpoint optimization is claimed. The existing application executes the six destinations in one transaction. An abort before commit rolls back destination writes; PostgreSQL sequence allocations may still advance.

The local recovery checks therefore reset all six destinations with `TRUNCATE ... RESTART IDENTITY` before a full rerun on the same unchanged Raw source. They compare the final state with an uninterrupted baseline. The recorded 1M checker interrupted after table3 had committed, then reset and reran; its final digest matched the uninterrupted result. This is a reset/full-rerun check, not an application-level interrupted dump/version test.

On a real application interruption, inspect the committed table version, dependency records, and dumps before taking action. Do not reset an existing production database blindly. Preserve a committed complete batch. If there is no complete committed result, use a clean dedicated challenge fixture for the full rerun and retain the resulting version/dump records. Partial progress is not reusable, and this entry does not add automatic recovery behavior.

The [PostgreSQL sequence documentation](https://www.postgresql.org/docs/17/functions-sequence.html) explains why sequence values are not reclaimed after an aborted transaction.
