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

    supported: bool | None
    enabled: bool | None
    attributes: tuple[SmartAttribute, ...]
    complete: bool = False
    threshold_exceeded: bool = False

    @property
    def status(self) -> str:
        if self.threshold_exceeded:
            return "Failed"
        if self.supported is False:
            return "Not Supported"
        if self.enabled is False:
            return "Disabled"
        if self.supported is True and self.enabled is True:
            if self.failed_attributes:
                return "Failed"
            if self.complete:
                return "Passed"
        return "Unknown"

    @property
    def healthy(self) -> bool:
        return self.status == "Passed"

    @property
    def failed_attributes(self) -> tuple[SmartAttribute, ...]:
        return tuple(a for a in self.attributes if a.failed)


def parse_smart_status(output: str) -> SmartStatus:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    messages = {line.casefold() for line in lines}
    supported = None
    enabled = None
    if messages & {"smart not supported", "smart unsupported"}:
        supported = False
    elif any(line.startswith("smart supported,") for line in messages):
        supported = True
        if "smart supported, smart enabled" in messages:
            enabled = True
        elif "smart supported, smart disabled" in messages:
            enabled = False
    unreadable_row = False

    attributes: list[SmartAttribute] = []
    for line in lines:
        match = _ATTR_RE.match(line)
        if not match:
            unreadable_row |= line[0].isdigit()
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

    return SmartStatus(
        supported=supported,
        enabled=enabled,
        attributes=tuple(attributes),
        complete=bool(attributes) and not unreadable_row,
        threshold_exceeded="threshold exceeds condition" in messages,
    )
