"""Report device and parser status without including connection details."""

from dataclasses import asdict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_AIRPORT_MAC, HARDWARE_CHECKS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return only the specific fields needed to diagnose monitoring problems."""
    coordinator = entry.runtime_data.hardware
    smb = entry.runtime_data.smb
    smb_status = smb.data if smb is not None and smb.last_update_success else None
    data = coordinator.data
    return {
        "checks": {
            "ssh_connected": data.connected and coordinator.last_update_success,
            "discovered_airport": CONF_AIRPORT_MAC in entry.data,
            "backup_volume_mounted": data.volume_mounted,
            "smb": asdict(smb_status) if smb_status is not None else None,
            "failed_checks": [
                check for check in HARDWARE_CHECKS if check not in data.successful_checks
            ],
            "last_success_by_check": {
                check: recorded.isoformat()
                for check, recorded in coordinator.last_success.items()
            },
        },
        "readings": {
            "environment": [
                {"label": reading.label, "unit": reading.unit, "value": reading.value}
                for reading in data.envstat.values()
            ],
            "smart_status": data.smart.status,
            "smart": [asdict(attribute) for attribute in data.smart.attributes],
            "capacity": {
                "total_bytes": data.disk.total_bytes,
                "free_bytes": data.disk.free_bytes,
                "use_percent": data.disk.use_percent,
            } if data.disk is not None else None,
        },
    }
