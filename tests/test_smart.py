"""Tests for the standalone SMART status parser."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import unittest

_SMART_PATH = Path(__file__).parents[1] / "custom_components" / "time_capsule" / "smart.py"
_SPEC = importlib.util.spec_from_file_location("time_capsule_smart", _SMART_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
_SMART = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SMART
_SPEC.loader.exec_module(_SMART)


class TestParseSmartStatus(unittest.TestCase):
    """Verify supported NetBSD atactl SMART row formats."""

    def _attribute(self, row: str):
        status = _SMART.parse_smart_status(f"SMART supported, SMART enabled\n{row}")
        self.assertEqual(len(status.attributes), 1)
        return status.attributes[0]

    def test_parses_traditional_row(self) -> None:
        attribute = self._attribute(
            "197 100    0     no  online  positive    Current pending sector   8"
        )

        self.assertEqual(attribute.id, 197)
        self.assertEqual(attribute.description, "Current pending sector")
        self.assertEqual(attribute.raw, 8)

    def test_parses_long_description_with_single_space_before_raw(self) -> None:
        attribute = self._attribute(
            "173 100    0     no  online  positive    "
            "Wear Leveller Worst Case Erase Count 8"
        )

        self.assertEqual(attribute.id, 173)
        self.assertEqual(attribute.description, "Wear Leveller Worst Case Erase Count")
        self.assertEqual(attribute.raw, 8)

    def test_parses_airflow_temperature_metadata(self) -> None:
        attribute = self._attribute(
            "190  48   40     no  online  positive    Airflow Temperature      "
            "52 Lifetime min/max 52/159"
        )

        self.assertEqual(attribute.id, 190)
        self.assertEqual(attribute.description, "Airflow Temperature")
        self.assertEqual(attribute.raw, 52)

    def test_parses_temperature_metadata(self) -> None:
        attribute = self._attribute(
            "194  52    0     no  online  positive    Temperature              "
            "52 Lifetime min/max 0/20"
        )

        self.assertEqual(attribute.id, 194)
        self.assertEqual(attribute.description, "Temperature")
        self.assertEqual(attribute.raw, 52)


if __name__ == "__main__":
    unittest.main()
