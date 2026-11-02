#!/usr/bin/env python3
"""Focused tests for named ECS loader-container status handling."""

from __future__ import annotations

import unittest

from run_data_loader import application_container, loader_exit_status


class LoaderExitStatusTest(unittest.TestCase):
    def test_failed_guardduty_sidecar_does_not_override_loader_success(self) -> None:
        task = {
            "stoppedReason": "Essential container in task exited",
            "containers": [
                {
                    "name": "GuardDutyAgent",
                    "reason": "CannotPullContainerError: image pull returned 403",
                },
                {"name": "DataLoaderContainer", "exitCode": 0},
            ],
        }
        self.assertEqual(loader_exit_status(task), (0, task["stoppedReason"]))

    def test_nonzero_loader_exit_is_reported(self) -> None:
        task = {
            "containers": [
                {"name": "DataLoaderContainer", "exitCode": 2, "reason": "load failed"}
            ]
        }
        self.assertEqual(loader_exit_status(task), (2, "load failed"))

    def test_missing_loader_container_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            application_container({"containers": [{"name": "GuardDutyAgent"}]})

    def test_duplicate_loader_container_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            application_container(
                {
                    "containers": [
                        {"name": "DataLoaderContainer"},
                        {"name": "DataLoaderContainer"},
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
