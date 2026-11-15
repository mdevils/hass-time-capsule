"""Monitor a Time Capsule using separate SSH and SMB checks."""

from __future__ import annotations

import logging
from asyncio import gather
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.dt import utcnow

from .alert_repairs import (
    async_clear_retired_alert_repairs,
    async_watch_retired_alert_sensors,
)
from .client import (
    TimeCapsuleAuthError,
    TimeCapsuleConnectionError,
    TimeCapsuleData,
    fetch_time_capsule_data,
)
from .const import (
    CONF_ENTITY_KEY,
    CONF_MONITOR_SMB,
    CONF_SMB_PASSWORD,
    CONF_SMB_SHARE,
    CONF_SMB_USERNAME,
    CONF_SSH_FINGERPRINT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SMART_HEALTH_IDS,
    SMART_POWER_ON_HOURS_ID,
    SMART_TEMPERATURE_ID,
)
from .smb import SmbStatus, probe_smb
from .smart import SmartStatus

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


class TimeCapsuleCoordinator(DataUpdateCoordinator[TimeCapsuleData]):
    """Poll the Capsule and remember when each check last returned usable data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} ({entry.data[CONF_HOST]})",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.entry = entry
        self.last_success: dict[str, datetime] = {}
        self._read_name = True

    async def _async_update_data(self) -> TimeCapsuleData:
        data = self.entry.data
        try:
            result = await self.hass.async_add_executor_job(
                partial(
                    fetch_time_capsule_data,
                    data[CONF_HOST],
                    data[CONF_PORT],
                    data[CONF_USERNAME],
                    data[CONF_PASSWORD],
                    data.get(CONF_SSH_FINGERPRINT),
                    include_name=self._read_name,
                )
            )
        except TimeCapsuleConnectionError as err:
            if isinstance(err, TimeCapsuleAuthError):
                if not self.entry.options.get(CONF_MONITOR_SMB):
                    raise ConfigEntryAuthFailed from err
                # Keep the entry loaded for SMB and pause automatic SSH attempts.
                self.update_interval = None
                self.entry.async_start_reauth(self.hass)
            elif self.data is None or self.data.connected:
                _LOGGER.warning("Time Capsule SSH unavailable: %s", err)
            return TimeCapsuleData(
                envstat={},
                smart=SmartStatus(None, None, ()),
                disk=None,
                fingerprint=None,
                errors={},
                connected=False,
            )

        self.update_interval = DEFAULT_SCAN_INTERVAL
        if self.data is not None and not self.data.connected:
            _LOGGER.info("Time Capsule SSH connection restored")

        if self.entry.data is data:
            if self._read_name and result.connected:
                self._read_name = False
                _async_register_device(self.hass, self.entry, result.base_station_name)
            if CONF_SSH_FINGERPRINT not in data and result.has_readings:
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**data, CONF_SSH_FINGERPRINT: result.fingerprint}
                )

        now = utcnow()
        for check in result.successful_checks:
            self.last_success[check] = now
        return result


class SmbCoordinator(DataUpdateCoordinator[SmbStatus]):
    """Check the SMB port and share using their own polling schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} SMB ({entry.data[CONF_HOST]})",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.entry = entry

    async def _async_update_data(self) -> SmbStatus:
        options = self.entry.options
        return await self.hass.async_add_executor_job(
            probe_smb,
            self.entry.data[CONF_HOST],
            options.get(CONF_SMB_SHARE, ""),
            options.get(CONF_SMB_USERNAME, ""),
            options.get(CONF_SMB_PASSWORD, ""),
        )


@dataclass
class TimeCapsuleRuntimeData:
    """The independent checks for one Time Capsule entry."""

    hardware: TimeCapsuleCoordinator
    smb: SmbCoordinator | None


def _async_register_device(
    hass: HomeAssistant, entry: ConfigEntry, base_station_name: str | None
) -> None:
    """Register the stable device and apply any name read from AirPort Utility."""
    registry = dr.async_get(hass)
    identifier = (DOMAIN, entry.data[CONF_ENTITY_KEY])
    previous_name = next(
        (
            device.name
            for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
            if identifier in device.identifiers
        ),
        None,
    )
    generic_title = f"Time Capsule ({entry.data[CONF_HOST]})"
    if base_station_name and entry.title in (generic_title, previous_name):
        hass.config_entries.async_update_entry(entry, title=base_station_name)

    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={identifier},
        name=base_station_name or previous_name or "Time Capsule",
        manufacturer="Apple",
        model="Apple Time Capsule",
    )


def _async_migrate_health_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove retired readings and update the default unit on the existing runtime sensor."""
    registry = er.async_get(hass)
    prefix = f"{DOMAIN}_{entry.data[CONF_ENTITY_KEY]}_"
    temperature_key = f"smart_{SMART_TEMPERATURE_ID}"
    retained_smart_keys = {f"smart_{attr_id}" for attr_id in SMART_HEALTH_IDS}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN or not entity.unique_id.startswith(prefix):
            continue
        key = entity.unique_id.removeprefix(prefix)
        if entity.domain == "binary_sensor" and key == "ssh_connected":
            registry.async_remove(entity.entity_id)
        if entity.domain != "sensor" or key == temperature_key:
            continue
        if (
            entity.original_device_class == SensorDeviceClass.TEMPERATURE
            or (
                key.startswith("smart_")
                and key.removeprefix("smart_").isdigit()
                and key not in retained_smart_keys
            )
        ):
            registry.async_remove(entity.entity_id)
        elif (
            key == f"smart_{SMART_POWER_ON_HOURS_ID}"
            and entity.unit_of_measurement == UnitOfTime.HOURS
        ):
            registry.async_update_entity(
                entity.entity_id, unit_of_measurement=UnitOfTime.DAYS
            )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Preserve retained entity IDs when updating the config and sensor list."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            version=2,
            data={**entry.data, CONF_ENTITY_KEY: entry.data[CONF_HOST]},
        )
    if entry.version != 2:
        return False
    if entry.minor_version < 2:
        _async_migrate_health_sensors(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the hardware and optional SMB checks."""
    hardware = TimeCapsuleCoordinator(hass, entry)
    smb = None
    if entry.options.get(CONF_MONITOR_SMB):
        smb = SmbCoordinator(hass, entry)
        # A failed optional check should still allow the entry to load and retry.
        await gather(hardware.async_config_entry_first_refresh(), smb.async_refresh())
    else:
        await hardware.async_config_entry_first_refresh()
    if not hardware.data.connected:
        _async_register_device(hass, entry, None)
    entry.runtime_data = TimeCapsuleRuntimeData(hardware, smb)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_watch_retired_alert_sensors(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload this Time Capsule."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear alert repairs when the Time Capsule is removed."""
    async_clear_retired_alert_repairs(hass, entry)
