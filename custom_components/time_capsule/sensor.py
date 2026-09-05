"""Sensor entities for a modded Time Capsule: envstat, SMART, and disk usage.

Set up from the UI (Settings -> Devices & Services -> Add Integration ->
Time Capsule Sensors); see config_flow.py. Exposes every degC envstat
reading (other than T_hdd, which this hardware never populates) as a
temperature sensor, the fan RPM reading as a fan-speed sensor, every SMART
attribute as its own sensor, one overall SMART pass/fail sensor, and disk
total/used/free/percent-used sensors for the internal drive. Which envstat
sensors show up is driven entirely by what envstat reports, not a hardcoded
list; same for the SMART attributes.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    PERCENTAGE,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .client import TimeCapsuleData
from .const import DOMAIN, FRIENDLY_NAMES, UNIT_FAN_RPM, UNIT_TEMPERATURE
from .smart import SmartStatus

DISK_METRIC_NAMES = {
    "total": "Disk Total",
    "used": "Disk Used",
    "free": "Disk Free",
    "percent": "Disk Used Percentage",
}

SMART_TEMPERATURE_DESCRIPTIONS = ("Temperature", "Airflow Temperature")
SMART_POWER_ON_HOURS_DESCRIPTION = "Power-on hours count"


def _friendly_name(label: str) -> str:
    return FRIENDLY_NAMES.get(label, label.replace("_", " ").strip())


def _smart_status_text(smart: SmartStatus) -> str:
    if not smart.supported:
        return "Not Supported"
    if not smart.enabled:
        return "Disabled"
    return "Passed" if smart.healthy else "Failed"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Time Capsule sensors for a config entry."""
    coordinator: DataUpdateCoordinator[TimeCapsuleData] = hass.data[DOMAIN][entry.entry_id]
    host = entry.data[CONF_HOST]

    entities: list[SensorEntity] = [
        TimeCapsuleEnvstatSensor(coordinator, host, key) for key in coordinator.data.envstat
    ]
    entities.extend(
        TimeCapsuleSmartAttributeSensor(coordinator, host, attr.id)
        for attr in coordinator.data.smart.attributes
    )
    entities.append(TimeCapsuleSmartStatusSensor(coordinator, host))

    if coordinator.data.disk is not None:
        entities.extend(
            TimeCapsuleDiskSensor(coordinator, host, metric) for metric in DISK_METRIC_NAMES
        )

    async_add_entities(entities)


class _TimeCapsuleEntity(CoordinatorEntity[DataUpdateCoordinator[TimeCapsuleData]]):
    """Shared device grouping for all Time Capsule entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DataUpdateCoordinator[TimeCapsuleData], host: str) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, host)},
            name="Time Capsule",
            manufacturer="Apple",
            model="Time Capsule (modded)",
        )


class TimeCapsuleEnvstatSensor(_TimeCapsuleEntity, SensorEntity):
    """A single envstat reading (temperature or fan speed)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: DataUpdateCoordinator[TimeCapsuleData], host: str, key: str
    ) -> None:
        super().__init__(coordinator, host)
        self._key = key

        reading = coordinator.data.envstat[key]
        self._attr_unique_id = f"{DOMAIN}_{host}_{key}"
        self._attr_name = _friendly_name(reading.label)

        if reading.unit == UNIT_TEMPERATURE:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif reading.unit == UNIT_FAN_RPM:
            self._attr_native_unit_of_measurement = UNIT_FAN_RPM
            self._attr_icon = "mdi:fan"

    @property
    def available(self) -> bool:
        return super().available and self._key in self.coordinator.data.envstat

    @property
    def native_value(self) -> Any:
        reading = self.coordinator.data.envstat.get(self._key)
        return reading.value if reading else None


class TimeCapsuleSmartAttributeSensor(_TimeCapsuleEntity, SensorEntity):
    """A single row of the SMART attribute table (its raw value)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: DataUpdateCoordinator[TimeCapsuleData], host: str, attr_id: int
    ) -> None:
        super().__init__(coordinator, host)
        self._smart_id = attr_id

        attr = self._attribute()
        self._attr_unique_id = f"{DOMAIN}_{host}_{attr.key}"
        self._attr_name = attr.description

        if attr.description in SMART_TEMPERATURE_DESCRIPTIONS:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif attr.description == SMART_POWER_ON_HOURS_DESCRIPTION:
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.HOURS
        else:
            self._attr_icon = "mdi:harddisk"

    def _attribute(self):
        return next(
            a for a in self.coordinator.data.smart.attributes if a.id == self._smart_id
        )

    @property
    def available(self) -> bool:
        return super().available and any(
            a.id == self._smart_id for a in self.coordinator.data.smart.attributes
        )

    @property
    def native_value(self) -> Any:
        return self._attribute().raw if self.available else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.available:
            return None
        attr = self._attribute()
        return {
            "normalized_value": attr.value,
            "threshold": attr.thresh,
            "critical": attr.critical,
            "collect": attr.collect,
            "reliability": attr.reliability,
        }


class TimeCapsuleSmartStatusSensor(_TimeCapsuleEntity, SensorEntity):
    """Overall SMART pass/fail, derived from the critical attributes' thresholds."""

    def __init__(self, coordinator: DataUpdateCoordinator[TimeCapsuleData], host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{DOMAIN}_{host}_smart_status"
        self._attr_name = "SMART Status"

    @property
    def native_value(self) -> str:
        return _smart_status_text(self.coordinator.data.smart)

    @property
    def icon(self) -> str:
        return "mdi:harddisk-alert" if not self.coordinator.data.smart.healthy else "mdi:harddisk"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        smart = self.coordinator.data.smart
        return {
            "failed_attributes": [a.description for a in smart.failed_attributes],
        }


class TimeCapsuleDiskSensor(_TimeCapsuleEntity, SensorEntity):
    """Total/used/free/percent-used usage for the internal drive."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: DataUpdateCoordinator[TimeCapsuleData], host: str, metric: str
    ) -> None:
        super().__init__(coordinator, host)
        self._metric = metric
        self._attr_unique_id = f"{DOMAIN}_{host}_disk_{metric}"
        self._attr_name = DISK_METRIC_NAMES[metric]

        if metric == "percent":
            self._attr_native_unit_of_measurement = PERCENTAGE
        else:
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
            self._attr_native_unit_of_measurement = UnitOfInformation.BYTES
            self._attr_suggested_unit_of_measurement = UnitOfInformation.GIGABYTES
            self._attr_suggested_display_precision = 2

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.disk is not None

    @property
    def native_value(self) -> Any:
        disk = self.coordinator.data.disk
        if disk is None:
            return None
        return {
            "total": disk.total_bytes,
            "used": disk.used_bytes,
            "free": disk.free_bytes,
            "percent": disk.use_percent,
        }[self._metric]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        disk = self.coordinator.data.disk
        if disk is None:
            return None
        return {"filesystem": disk.filesystem, "mounted_on": disk.mounted_on}
