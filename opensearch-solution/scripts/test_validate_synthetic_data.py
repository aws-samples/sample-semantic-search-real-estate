#!/usr/bin/env python3
"""Focused regression tests for the synthetic-data validator."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from validate_synthetic_data import PROHIBITED_TERMS, validate


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class SyntheticDataValidatorTest(unittest.TestCase):
    def test_rejects_each_prohibited_portal_or_broker_term(self) -> None:
        for term in PROHIBITED_TERMS:
            with self.subTest(term=term), tempfile.TemporaryDirectory() as temp_dir:
                test_data_dir = Path(temp_dir) / "data"
                shutil.copytree(DATA_DIR, test_data_dir)
                atlanta_path = test_data_dir / "atlanta.json"
                records = json.loads(atlanta_path.read_text(encoding="utf-8"))
                records[0]["propertyDescription"] += f" {term}"
                atlanta_path.write_text(
                    json.dumps(records, indent=2) + "\n",
                    encoding="utf-8",
                )

                errors = validate(test_data_dir)

                self.assertTrue(
                    any(
                        "contains prohibited listing or portal terminology" in error
                        for error in errors
                    ),
                    errors,
                )


if __name__ == "__main__":
    unittest.main()
