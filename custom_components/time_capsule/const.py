"""Constants for the Time Capsule Sensors integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "time_capsule"

DEFAULT_PORT = 22
DEFAULT_USERNAME = "root"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
CONNECT_TIMEOUT = 15

# Full paths, not just "envstat" / "atactl": a non-interactive SSH command
# runs with a minimal PATH that doesn't include /usr/sbin or /sbin on this
# device, even though an interactive shell's profile does.
ENVSTAT_COMMAND = "/usr/sbin/envstat"
SMART_COMMAND = "/sbin/atactl wd0 smart status"

# This hardware's SSH server is old enough that current OpenSSH and Paramiko
# releases no longer offer these by default; they have to be requested
# explicitly, matching `-o KexAlgorithms=diffie-hellman-group14-sha1
# -o HostKeyAlgorithms=+ssh-rsa` on the command line. Paramiko 4.0 dropped
# both algorithms entirely, hence the "<4" pin in manifest.json.
SSH_KEX_ALGORITHMS = ("diffie-hellman-group14-sha1",)
SSH_HOST_KEY_ALGORITHM = "ssh-rsa"

UNIT_TEMPERATURE = "degC"
UNIT_FAN_RPM = "RPM"

# envstat labels that should never become sensors even though they carry a
# unit we otherwise pick up, e.g. T_hdd, which is unpopulated on this
# hardware and always reads 0.
IGNORED_LABELS = {"T_hdd"}

# Cosmetic only -- friendly names for the labels envstat is known to report.
# Anything not listed here still becomes a sensor, just with a less polished
# name derived from its raw label.
FRIENDLY_NAMES = {
    "T_internal": "Internal Temperature",
    "T_external1": "External Temperature 1",
    "T_external2": "External Temperature 2",
    "T_external3": "External Temperature 3",
    "Fan_rpm": "Fan Speed",
}
