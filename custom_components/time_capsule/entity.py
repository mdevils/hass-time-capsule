"""Shared device registry information for Time Capsule entities."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TimeCapsuleCoordinator
from .const import DOMAIN


class TimeCapsuleEntity(CoordinatorEntity[TimeCapsuleCoordinator]):
    """Group all readings under a device whose identity survives address changes."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TimeCapsuleCoordinator, device_key: str) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_key)})

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.connected
