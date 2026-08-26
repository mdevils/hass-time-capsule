"""The Time Capsule Sensors integration.

Config-entry based (see config_flow.py) -- one entry per Time Capsule, each
backed by a single coordinator shared by all of its sensors so envstat and
SMART status are only fetched once per poll regardless of how many readings
they produce between them.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import TimeCapsuleConnectionError, TimeCapsuleData, fetch_time_capsule_data
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Time Capsule entry: create its coordinator and load sensor.py."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    async def _async_update() -> TimeCapsuleData:
        try:
            return await hass.async_add_executor_job(
                fetch_time_capsule_data, host, port, username, password
            )
        except TimeCapsuleConnectionError as err:
            raise UpdateFailed(str(err)) from err

    coordinator: DataUpdateCoordinator[TimeCapsuleData] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN} ({host})",
        update_method=_async_update,
        update_interval=DEFAULT_SCAN_INTERVAL,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Time Capsule entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after the options flow updates connection details."""
    await hass.config_entries.async_reload(entry.entry_id)
