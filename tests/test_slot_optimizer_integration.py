import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from generate_database import RAW_SCHEMA_NAME
from services.expansion_slot_optimizer import optimize_validated_slot_statement
from services.postgres_service import PostgresAdminService
from services.table2_only_projection import project_table2_only
from tests.test_challenge_expansion_recipes import RECIPE_DESTINATIONS


ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "SQL files"


def destinations(project=lambda table, sql: sql):
    return [
        {"table_name": table, "version_title": "Slot extraction", "sql": project(table, (RECIPES / name).read_text())}
        for name, table in RECIPE_DESTINATIONS
    ]


def prepare(items):
    return PostgresAdminService._prepare_cross_table_expansion(
        "public.raw_data", [RAW_SCHEMA_NAME], items
    )


class SlotOptimizerIntegrationTests(unittest.TestCase):
    def test_application_reaches_the_frozen_measured_candidate(self):
        actual = prepare(destinations())
        with patch("services.postgres_service.optimize_validated_slot_statement", side_effect=lambda statement, *_: statement):
            expected = prepare(destinations(project_table2_only))
            baseline = prepare(destinations())
        self.assertEqual(actual, expected)
        for before, after in zip(baseline["destinations"], actual["destinations"]):
            if after["table_name"] != "public.table2":
                self.assertEqual(before, after)
            else:
                self.assertIn("CASE chosen.position_number", after["statements"][0])
                self.assertNotEqual(before["statements"], after["statements"])
                self.assertEqual(before["destination_columns"], after["destination_columns"])
                self.assertEqual(before["read_relations"], after["read_relations"])

    def test_custom_recipe_does_not_silently_use_the_specialization(self):
        sql = (RECIPES / "06_table2.sql").read_text().strip().rstrip(";")
        custom = sql.replace("position_data.position_number", "position_data.position_number + 1")
        self.assertEqual(optimize_validated_slot_statement(custom, "public", "table2"), custom)
        self.assertEqual(optimize_validated_slot_statement(sql, "custom", "table2"), sql)
        self.assertEqual(optimize_validated_slot_statement(sql, "public", "another_table"), sql)

    def test_original_validation_precedes_optimizer(self):
        invalid = destinations()
        invalid[-1]["sql"] += "\nDELETE FROM public.raw_data;"
        calls = []
        def observed(statement, schema, table):
            calls.append(statement)
            return optimize_validated_slot_statement(statement, schema, table)
        with patch("services.postgres_service.optimize_validated_slot_statement", side_effect=observed):
            with self.assertRaises(RuntimeError):
                prepare(invalid)
        self.assertFalse(any("DELETE FROM public.raw_data" in item for item in calls))

    def test_rewritten_statement_cannot_bypass_protected_source_validation(self):
        def malicious(statement, schema, table):
            if (schema, table) == ("public", "table2"):
                return statement + "\nDELETE FROM public.raw_data;"
            return statement
        with patch("services.postgres_service.optimize_validated_slot_statement", side_effect=malicious):
            with self.assertRaises(RuntimeError):
                prepare(destinations())

    def test_official_table2_recipe_remains_upstream_bytes(self):
        expected = {
            "06_table2.sql": "3954afa05d595de9c3efb2526a9b8f82b3c56d39460dc60776e87f29ab18cc28",
        }
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((RECIPES / name).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
