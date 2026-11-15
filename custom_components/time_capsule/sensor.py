"""Expose Time Capsule drive health, fan speed, and disk usage."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.dt import parse_datetime

from . import TimeCapsuleCoordinator
from .const import (
    CONF_ENTITY_KEY,
    DOMAIN,
    HARDWARE_CHECKS,
    SMART_HEALTH_IDS,
    SMART_POWER_ON_HOURS_ID,
    SMART_TEMPERATURE_ID,
    UNIT_FAN_RPM,
    UNIT_TEMPERATURE,
)
from .entity import TimeCapsuleEntity
from .smart import SmartAttribute

DISK_METRIC_NAMES = {
    "total": "Disk Total",
    "used": "Disk Used",
    "free": "Disk Free",
    "percent": "Disk Used Percentage",
    "free_percent": "Disk Free Percentage",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add fixed sensors and readings as they first arrive."""
    coordinator: TimeCapsuleCoordinator = entry.runtime_data.hardware
    device_key = entry.data[CONF_ENTITY_KEY]
    fan_keys: set[str] = set()
    smart_ids: set[int] = set()

    @callback
    def async_add_new_readings() -> None:
        if not coordinator.last_update_success:
            return
        entities: list[SensorEntity] = []
        for key, reading in coordinator.data.envstat.items():
            if reading.unit == UNIT_FAN_RPM and key not in fan_keys:
                entities.append(TimeCapsuleFanSensor(coordinator, device_key, key))
                fan_keys.add(key)
        for attr in coordinator.data.smart.attributes:
            if (
                attr.id in SMART_HEALTH_IDS
                and attr.id not in smart_ids
                and attr.description.casefold() != "unknown"
            ):
                entities.append(
                    TimeCapsuleSmartAttributeSensor(coordinator, device_key, attr)
                )
                smart_ids.add(attr.id)
        if entities:
            async_add_entities(entities)

    entities: list[SensorEntity] = [
        TimeCapsuleSmartStatusSensor(coordinator, device_key),
        TimeCapsuleTemperatureSensor(coordinator, device_key),
        TimeCapsuleLastReadingSensor(coordinator, device_key),
    ]
    entities.extend(
        TimeCapsuleDiskSensor(coordinator, device_key, metric)
        for metric in DISK_METRIC_NAMES
    )
    async_add_entities(entities)
    async_add_new_readings()
    entry.async_on_unload(coordinator.async_add_listener(async_add_new_readings))


class TimeCapsuleFanSensor(TimeCapsuleEntity, SensorEntity):
    """Fan speed reported by envstat."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_FAN_RPM
    _attr_icon = "mdi:fan"

    def __init__(
        self, coordinator: TimeCapsuleCoordinator, device_key: str, key: str
    ) -> None:
        super().__init__(coordinator, device_key)
        self._key = key

        reading = coordinator.data.envstat[key]
        self._attr_unique_id = f"{DOMAIN}_{device_key}_{key}"
        self._attr_name = (
            "Fan Speed"
            if reading.label == "Fan_rpm"
            else reading.label.replace("_", " ").strip()
        )

    @property
    def available(self) -> bool:
        return super().available and self._key in self.coordinator.data.envstat

    @property
    def native_value(self) -> Any:
        reading = self.coordinator.data.envstat.get(self._key)
        return reading.value if reading else None


class TimeCapsuleSmartAttributeSensor(TimeCapsuleEntity, SensorEntity):
    """A single row of the SMART attribute table (its raw value)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: TimeCapsuleCoordinator, device_key: str, attr: SmartAttribute
    ) -> None:
        super().__init__(coordinator, device_key)
        self._smart_id = attr.id
        self._attr_unique_id = f"{DOMAIN}_{device_key}_{attr.key}"
        self._attr_name = attr.description
        if attr.id == SMART_POWER_ON_HOURS_ID:
            self._attr_name = "Power-on Days"
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.DAYS
            self._attr_suggested_display_precision = 0
        else:
            self._attr_icon = "mdi:harddisk"

    def _attribute(self) -> SmartAttribute | None:
        return next(
            (
                attr for attr in self.coordinator.data.smart.attributes
                if attr.id == self._smart_id and attr.description.casefold() != "unknown"
            ),
            None,
        )

    @property
    def available(self) -> bool:
        return super().available and self._attribute() is not None

    @property
    def native_value(self) -> int | float | None:
        attr = self._attribute()
        if attr is None:
            return None
        if self._smart_id == SMART_POWER_ON_HOURS_ID:
            return attr.raw / 24
        return attr.raw

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attr = self._attribute()
        return _smart_details(attr) if attr is not None else None


def _smart_details(attr: SmartAttribute) -> dict[str, Any]:
    return {
        "normalized_value": attr.value,
        "threshold": attr.thresh,
        "critical": attr.critical,
        "collect": attr.collect,
        "reliability": attr.reliability,
    }


class TimeCapsuleTemperatureSensor(TimeCapsuleEntity, SensorEntity):
    """Prefer drive temperature and fall back to available hardware readings."""

    _attr_name = "Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: TimeCapsuleCoordinator, device_key: str) -> None:
        super().__init__(coordinator, device_key)
        self._attr_unique_id = f"{DOMAIN}_{device_key}_smart_{SMART_TEMPERATURE_ID}"

    def _smart_temperature(self) -> SmartAttribute | None:
        readings = self.coordinator.data.smart.attributes
        for description in ("Temperature", "Airflow Temperature"):
            for attr in readings:
                if attr.description == description:
                    return attr
        return None

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> int | float | None:
        if attr := self._smart_temperature():
            return attr.raw
        readings = [
            reading
            for reading in self.coordinator.data.envstat.values()
            if reading.unit == UNIT_TEMPERATURE
        ]
        if readings:
            return next(
                (reading.value for reading in readings if reading.label == "T_internal"),
                readings[0].value,
            )
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attr = self._smart_temperature()
        return _smart_details(attr) if attr is not None else None


class TimeCapsuleSmartStatusSensor(TimeCapsuleEntity, SensorEntity):
    """Overall SMART pass/fail, derived from the critical attributes' thresholds."""

    def __init__(self, coordinator: TimeCapsuleCoordinator, device_key: str) -> None:
        super().__init__(coordinator, device_key)
        self._attr_unique_id = f"{DOMAIN}_{device_key}_smart_status"
        self._attr_name = "SMART Status"

    @property
    def native_value(self) -> str:
        return self.coordinator.data.smart.status

    @property
    def icon(self) -> str:
        return (
            "mdi:harddisk-alert"
            if not self.coordinator.data.smart.healthy
            else "mdi:harddisk"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        smart = self.coordinator.data.smart
        return {
            "failed_attributes": [a.description for a in smart.failed_attributes],
        }


class TimeCapsuleDiskSensor(TimeCapsuleEntity, SensorEntity):
    """Capacity and usage sensors for the internal drive."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: TimeCapsuleCoordinator, device_key: str, metric: str
    ) -> None:
        super().__init__(coordinator, device_key)
        self._metric = metric
        self._attr_unique_id = f"{DOMAIN}_{device_key}_disk_{metric}"
        self._attr_name = DISK_METRIC_NAMES[metric]

        if metric in ("percent", "free_percent"):
            self._attr_icon = "mdi:database"
            self._attr_native_unit_of_measurement = PERCENTAGE
        else:
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
            self._attr_native_unit_of_measurement = UnitOfInformation.BYTES
            self._attr_suggested_unit_of_measurement = UnitOfInformation.TERABYTES
            self._attr_suggested_display_precision = 1

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
            "free_percent": max(0, 100 - disk.use_percent),
        }[self._metric]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        disk = self.coordinator.data.disk
        if disk is None:
            return None
        return {"filesystem": disk.filesystem, "mounted_on": disk.mounted_on}


class TimeCapsuleLastReadingSensor(TimeCapsuleEntity, SensorEntity, RestoreEntity):
    """Timestamp of the latest usable reading, retained through connection loss."""

    _attr_name = "Last Successful Reading"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TimeCapsuleCoordinator, device_key: str) -> None:
        super().__init__(coordinator, device_key)
        self._attr_unique_id = f"{DOMAIN}_{device_key}_last_reading"

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        previous = await self.async_get_last_state()
        if previous is None:
            return
        saved = previous.attributes.get("last_success_by_check", {})
        for check in HARDWARE_CHECKS:
            value = saved.get(check)
            if isinstance(value, str) and (timestamp := parse_datetime(value)):
                self.coordinator.last_success.setdefault(check, timestamp)

    @property
    def native_value(self) -> datetime | None:
        return max(self.coordinator.last_success.values(), default=None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        checks = HARDWARE_CHECKS
        last_success = {
            check: self.coordinator.last_success[check].isoformat()
            for check in checks
            if check in self.coordinator.last_success
        }
        return {
            "last_success_by_check": last_success,
            "failed_checks": sorted(self.coordinator.data.errors)
            if super().available
            else list(checks),
        }
