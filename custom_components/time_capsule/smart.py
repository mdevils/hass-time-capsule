"""Parsing of `atactl wd0 smart status` output.

See client.py for the SSH session that runs it.

    SMART supported, SMART enabled
    id value thresh crit collect reliability description            raw
      1 200   51     yes online  positive    Raw read error rate         0
      3 182   21     yes online  positive    Spin-up time                5858
      ...

Each row is a normalized SMART attribute: `value` is the vendor-normalized
health score (higher is generally better, 0-253 ish), `thresh` is the
failure threshold, and `raw` is the actual physical quantity (hours,
sectors, degrees...) most people care about. An attribute has failed once
its `value` drops to or below `thresh` and `crit` is "yes" -- that's the
same logic tools like smartctl use for their overall pass/fail verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ATTR_RE = re.compile(
    r"^\s*(?P<id>\d+)\s+(?P<value>\d+)\s+(?P<thresh>\d+)\s+(?P<crit>yes|no)\s+"
    r"(?P<collect>\S+)\s+(?P<reliability>\S+)\s+(?P<description>.+?)\s+(?P<raw>-?\d+)\s*$"
)


@dataclass(frozen=True)
class SmartAttribute:
    """A single row of the SMART attribute table."""

    id: int
    value: int
    thresh: int
    critical: bool
    collect: str
    reliability: str
    description: str
    raw: int

    @property
    def key(self) -> str:
        return f"smart_{self.id}"

    @property
    def failed(self) -> bool:
        return self.critical and self.value <= self.thresh


@dataclass(frozen=True)
class SmartStatus:
    """The parsed `smart status` output: header line plus attribute table."""

    supported: bool
    enabled: bool
    attributes: tuple[SmartAttribute, ...]

    @property
    def healthy(self) -> bool:
        return self.supported and self.enabled and not any(a.failed for a in self.attributes)

    @property
    def failed_attributes(self) -> tuple[SmartAttribute, ...]:
        return tuple(a for a in self.attributes if a.failed)


def parse_smart_status(output: str) -> SmartStatus:
    lines = output.splitlines()
    header = lines[0] if lines else ""
    supported = "SMART supported" in header
    enabled = "SMART enabled" in header

    attributes: list[SmartAttribute] = []
    for line in lines[1:]:
        match = _ATTR_RE.match(line)
        if not match:
            continue
        attributes.append(
            SmartAttribute(
                id=int(match["id"]),
                value=int(match["value"]),
                thresh=int(match["thresh"]),
                critical=match["crit"] == "yes",
                collect=match["collect"],
                reliability=match["reliability"],
                description=match["description"].strip(),
                raw=int(match["raw"]),
            )
        )

    return SmartStatus(supported=supported, enabled=enabled, attributes=tuple(attributes))
