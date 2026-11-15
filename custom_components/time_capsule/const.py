"""Constants for the Apple Time Capsule integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "time_capsule"

DEFAULT_PORT = 22
DEFAULT_USERNAME = "root"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
CONNECT_TIMEOUT = 15

# Full paths, not just "envstat" / "atactl" / "df": a non-interactive SSH
# command runs with a minimal PATH that doesn't include /usr/sbin or /sbin
# on this device, even though an interactive shell's profile does.
ENVSTAT_COMMAND = "/usr/sbin/envstat"
SMART_COMMAND = "/sbin/atactl wd0 smart status"
BACKUP_DEVICE = "/dev/dk2"
DF_COMMAND = f"/bin/df {BACKUP_DEVICE}"
MOUNT_COMMAND = "/sbin/mount"
HARDWARE_CHECKS = ("environment", "smart", "disk")
BASE_STATION_NAME_COMMAND = "/usr/bin/acp -q syNm"

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

SMART_POWER_ON_HOURS_ID = 9
SMART_TEMPERATURE_ID = 194
SMART_HEALTH_IDS = {
    5,  # Reallocated sector count
    SMART_POWER_ON_HOURS_ID,
    10,  # Spin retry count
    12,  # Device power cycle count
    187,  # Reported uncorrectable errors
    197,  # Current pending sector
    198,  # Offline uncorrectable
    199,  # Ultra DMA CRC error count
}

CONF_ENTITY_KEY = "entity_key"
CONF_SSH_FINGERPRINT = "ssh_fingerprint"
CONF_AIRPORT_MAC = "airport_mac"
CONF_MONITOR_SMB = "monitor_smb"
CONF_SMB_SHARE = "smb_share"
CONF_SMB_USERNAME = "smb_username"
CONF_SMB_PASSWORD = "smb_password"
AIRPORT_TIME_CAPSULE_MODEL = "119"
