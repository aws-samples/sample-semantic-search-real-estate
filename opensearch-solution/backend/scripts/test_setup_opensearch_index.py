#!/usr/bin/env python3
"""Focused tests for loader data-directory resolution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from setup_opensearch_index import load_records, resolve_data_dir


class DataDirectoryTest(unittest.TestCase):
    def test_explicit_data_directory_is_honored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "records"
            data_dir.mkdir()
            self.assertEqual(resolve_data_dir(data_dir), data_dir.resolve())

    def test_container_data_directory_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "setup_opensearch_index.py"
            data_dir = Path(temp_dir) / "data"
            data_dir.mkdir()
            self.assertEqual(resolve_data_dir(None, script_path), data_dir.resolve())

    def test_repository_data_directory_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script_path = root / "backend" / "scripts" / "setup_opensearch_index.py"
            script_path.parent.mkdir(parents=True)
            data_dir = root / "data"
            data_dir.mkdir()
            self.assertEqual(resolve_data_dir(None, script_path), data_dir.resolve())

    def test_missing_data_directory_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"
            with self.assertRaisesRegex(FileNotFoundError, "data directory does not exist"):
                resolve_data_dir(missing)

    def test_repository_dataset_contains_exactly_80_records(self) -> None:
        data_dir = Path(__file__).resolve().parents[2] / "data"
        self.assertEqual(len(load_records(data_dir)), 80)


if __name__ == "__main__":
    unittest.main()
