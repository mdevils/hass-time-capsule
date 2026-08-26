"""Parsing of `envstat` output. See client.py for the SSH session that runs it."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .const import IGNORED_LABELS, UNIT_FAN_RPM, UNIT_TEMPERATURE

_SECTION_RE = re.compile(r"^\[(?P<section>\S+)\]$")


@dataclass(frozen=True)
class EnvstatReading:
    """A single row parsed out of `envstat` output."""

    section: str
    label: str
    value: float
    unit: str

    @property
    def key(self) -> str:
        return f"{self.section}_{self.label}"


def parse_envstat(output: str) -> list[EnvstatReading]:
    """Parse `envstat` text output into temperature and fan-speed readings.

    envstat prints fixed-width columns (Current/CritMax/WarnMax/WarnMin/
    CritMin/Unit) but leaves unset threshold columns blank rather than
    zero-padded, so splitting each row on whitespace reliably yields the
    current value as the first token and the unit as the last, regardless of
    how many threshold columns are actually populated.
    """
    readings: list[EnvstatReading] = []
    section = ""
    for line in output.splitlines():
        section_match = _SECTION_RE.match(line.strip())
        if section_match:
            section = section_match.group("section")
            continue

        if ":" not in line or not section:
            continue

        label, rest = line.split(":", 1)
        label = label.strip()
        if label in IGNORED_LABELS:
            continue

        tokens = rest.split()
        if len(tokens) < 2:
            continue

        value_str, unit = tokens[0], tokens[-1]
        if unit not in (UNIT_TEMPERATURE, UNIT_FAN_RPM):
            continue

        try:
            value = float(value_str)
        except ValueError:
            continue

        readings.append(EnvstatReading(section=section, label=label, value=value, unit=unit))

    return readings
