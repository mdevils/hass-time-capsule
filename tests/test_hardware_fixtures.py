"""Check the NetBSD output formats the integration currently expects."""

import unittest
from pathlib import Path

try:
    import homeassistant  # noqa: F401
except ModuleNotFoundError as err:
    if err.name != "homeassistant":
        raise
    raise unittest.SkipTest("Install Home Assistant Core for the fixture tests") from err

from custom_components.time_capsule.const import BACKUP_DEVICE
from custom_components.time_capsule.df import is_volume_mounted, parse_df
from custom_components.time_capsule.envstat import parse_envstat
from custom_components.time_capsule.smart import parse_smart_status

FIXTURES = Path(__file__).parent / "fixtures" / "netbsd-reference"


class TestHardwareFixtures(unittest.TestCase):
    def test_environment_with_optional_thresholds(self):
        readings = {
            reading.label: reading.value
            for reading in parse_envstat((FIXTURES / "envstat.txt").read_text())
        }
        self.assertEqual(readings, {"T_internal": 45.32, "T_external1": 39.25, "Fan_rpm": 1757})

    def test_smart_with_temperature_reading(self):
        status = parse_smart_status((FIXTURES / "smart.txt").read_text())
        self.assertEqual(status.status, "Passed")
        self.assertEqual({attribute.id: attribute.raw for attribute in status.attributes}, {
            5: 2, 190: 52, 197: 0,
        })

    def test_disk_capacity_and_mount(self):
        disk = parse_df((FIXTURES / "df.txt").read_text())
        self.assertIsNotNone(disk)
        self.assertEqual(disk.filesystem, BACKUP_DEVICE)
        self.assertEqual(disk.free_bytes, 5397953992 * 512)
        self.assertIs(
            is_volume_mounted((FIXTURES / "mount.txt").read_text(), BACKUP_DEVICE),
            True,
        )
