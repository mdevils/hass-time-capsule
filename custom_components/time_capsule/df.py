"""Parsing of `df` output for the internal drive.

See client.py for the SSH session that runs it.

    Filesystem   512-blocks       Used      Avail %Cap Mounted on
    /dev/dk2     5856338784  458384792 5397953992   7% /Volumes/dk2

Querying a single, specific device (rather than `df` with no arguments)
means the output is always exactly a header line plus one data line.
"""

from __future__ import annotations

from dataclasses import dataclass

_BLOCK_SIZE = 512


@dataclass(frozen=True)
class DiskUsage:
    """Disk usage for one filesystem, converted from 512-byte blocks to bytes."""

    filesystem: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    use_percent: int
    mounted_on: str


def parse_df(output: str) -> DiskUsage | None:
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    tokens = lines[1].split()
    if len(tokens) < 6:
        return None

    try:
        blocks = int(tokens[1])
        used = int(tokens[2])
        avail = int(tokens[3])
        use_percent = int(tokens[4].rstrip("%"))
    except ValueError:
        return None

    return DiskUsage(
        filesystem=tokens[0],
        total_bytes=blocks * _BLOCK_SIZE,
        used_bytes=used * _BLOCK_SIZE,
        free_bytes=avail * _BLOCK_SIZE,
        use_percent=use_percent,
        mounted_on=" ".join(tokens[5:]),
    )
