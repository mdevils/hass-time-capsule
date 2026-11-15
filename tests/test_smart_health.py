"""Tests for the standalone SMART status parser."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_SMART_PATH = (
    Path(__file__).parents[1] / "custom_components" / "time_capsule" / "smart.py"
)
_SPEC = importlib.util.spec_from_file_location("time_capsule_smart", _SMART_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
_SMART = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SMART
_SPEC.loader.exec_module(_SMART)


class TestParseSmartStatus(unittest.TestCase):
    """Verify supported NetBSD atactl SMART row formats."""

    def test_empty_table_is_unknown(self) -> None:
        status = _SMART.parse_smart_status("SMART supported, SMART enabled\n")
        self.assertEqual(status.status, "Unknown")

    def test_malformed_failing_row_prevents_a_pass(self) -> None:
        status = _SMART.parse_smart_status(
            "SMART supported, SMART enabled\n"
            "197 100 0 no online positive Current pending sector 0\n"
            "193 19 20 yes online positive Load cycle count unexpected"
        )
        self.assertEqual(len(status.attributes), 1)
        self.assertEqual(status.status, "Unknown")

    def test_readable_failure_beats_missing_readings(self) -> None:
        status = _SMART.parse_smart_status(
            "SMART supported, SMART enabled\n"
            "197 100 0 no online positive Current pending sector unexpected\n"
            "193 19 20 yes online positive Load cycle count 42"
        )
        self.assertEqual(status.status, "Failed")

    def test_ata_verdict_is_reported_even_with_no_rows(self) -> None:
        status = _SMART.parse_smart_status(
            "Threshold exceeds condition\nSMART supported, SMART enabled\n"
        )
        self.assertEqual(status.status, "Failed")

    def test_reports_explicit_disablement_and_unsupported(self) -> None:
        for text, expected in (
            ("SMART supported, SMART disabled", "Disabled"),
            ("SMART unsupported\nSMART not supported", "Not Supported"),
            ("SMART supported, SMART status unknown", "Unknown"),
        ):
            with self.subTest(text=text):
                self.assertEqual(_SMART.parse_smart_status(text).status, expected)


if __name__ == "__main__":
    unittest.main()
