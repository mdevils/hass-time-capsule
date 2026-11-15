"""Backup volume and SMB status for Time Capsules."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SmbCoordinator, TimeCapsuleCoordinator
from .const import CONF_ENTITY_KEY, CONF_SMB_SHARE, DOMAIN
from .entity import TimeCapsuleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Expose backup storage and optional SMB checks."""
    runtime = entry.runtime_data
    key = entry.data[CONF_ENTITY_KEY]
    entities: list[BinarySensorEntity] = [
        TimeCapsuleVolumeMounted(runtime.hardware, key),
    ]
    if runtime.smb is not None:
        entities.append(TimeCapsuleSmbCheck(runtime.smb, key, check_share=False))
        if entry.options.get(CONF_SMB_SHARE):
            entities.append(TimeCapsuleSmbCheck(runtime.smb, key, check_share=True))
    async_add_entities(entities)


class TimeCapsuleVolumeMounted(TimeCapsuleEntity, BinarySensorEntity):
    """Report the internal backup volume mount over SSH."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Backup Volume Mounted"

    def __init__(self, coordinator: TimeCapsuleCoordinator, device_key: str) -> None:
        super().__init__(coordinator, device_key)
        self._attr_unique_id = f"{DOMAIN}_{device_key}_volume_mounted"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.volume_mounted


class TimeCapsuleSmbCheck(CoordinatorEntity[SmbCoordinator], BinarySensorEntity):
    """Report either SMB connectivity or access to the configured share."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self, coordinator: SmbCoordinator, device_key: str, *, check_share: bool
    ) -> None:
        super().__init__(coordinator)
        self._check_share = check_share
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_key)})
        if check_share:
            self._attr_unique_id = f"{DOMAIN}_{device_key}_smb_share_accessible"
            self._attr_name = "SMB Share Accessible"
        else:
            self._attr_unique_id = f"{DOMAIN}_{device_key}_smb_port_open"
            self._attr_name = "SMB Port Open"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        return data.share_accessible if self._check_share else data.port_open
