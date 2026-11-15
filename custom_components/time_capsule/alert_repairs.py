"""Help users replace retired Time Capsule sensors in their automations."""

from collections.abc import Collection

from homeassistant.components.automation import (
    DATA_COMPONENT as AUTOMATION_COMPONENT,
    EVENT_AUTOMATION_RELOADED,
    automations_with_entity,
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.start import async_at_started

from .const import CONF_ENTITY_KEY, DOMAIN, SMART_TEMPERATURE_ID

_ISSUE_PREFIX = "retired_alert_sensor_"
_TEMPERATURE_LABELS = {"T_internal", "T_external1", "T_external2", "T_external3"}


@callback
def async_watch_retired_alert_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Check existing automations after startup and after automation edits."""
    registry = er.async_get(hass)
    prefix = f"{DOMAIN}_{entry.data[CONF_ENTITY_KEY]}_"
    issue_prefix = f"{_ISSUE_PREFIX}{entry.entry_id}_"

    @callback
    def refresh_repairs(_event_or_hass: Event | HomeAssistant) -> None:
        component = hass.data.get(AUTOMATION_COMPONENT)
        if component is None:
            return

        # Older copies of the blueprint pass the selections as template variables.
        # Home Assistant's reference lookup doesn't inspect those variables.
        connection_sensors: set[str] = set()
        temperature_sensors: set[str] = set()
        for automation in component.entities:
            if not automation.referenced_blueprint or not automation.raw_config:
                continue
            variables = automation.raw_config.get("variables")
            if not isinstance(variables, dict) or "state_helper" not in variables:
                continue
            if isinstance(connections := variables.get("connectivity_entities"), list):
                connection_sensors.update(
                    item for item in connections if isinstance(item, str)
                )
                if isinstance(temperature := variables.get("temperature_entity"), str):
                    temperature_sensors.add(temperature)

        active: set[str] = set()
        # The registry keeps the exact old entity IDs, including user renames,
        # even if the integration has already removed the entities.
        for deleted in registry.deleted_entities.values():
            if (
                deleted.platform != DOMAIN
                or deleted.config_entry_id != entry.entry_id
                or not deleted.unique_id.startswith(prefix)
                or registry.async_get(deleted.entity_id) is not None
                or hass.states.get(deleted.entity_id) is not None
            ):
                continue
            key = deleted.unique_id.removeprefix(prefix)
            if deleted.domain == "binary_sensor" and key == "ssh_connected":
                replacement_key = "volume_mounted"
                translation_key = "retired_alert_connection"
            elif deleted.domain == "sensor" and (
                deleted.device_class == SensorDeviceClass.TEMPERATURE
                or deleted.entity_id in temperature_sensors
                or key == "smart_190"
                or key.partition("_")[2] in _TEMPERATURE_LABELS
            ):
                replacement_key = f"smart_{SMART_TEMPERATURE_ID}"
                translation_key = "retired_alert_temperature"
            else:
                continue

            if (
                deleted.entity_id not in connection_sensors
                and deleted.entity_id not in temperature_sensors
                and not automations_with_entity(hass, deleted.entity_id)
            ):
                continue
            replacement = registry.async_get_entity_id(
                deleted.domain, DOMAIN, f"{prefix}{replacement_key}"
            )
            if replacement is None:
                continue

            issue_id = f"{issue_prefix}{deleted.id}"
            active.add(issue_id)
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=translation_key,
                translation_placeholders={
                    "entity_id": deleted.entity_id,
                    "replacement": replacement,
                },
            )
        async_clear_retired_alert_repairs(hass, entry, keep=active)

    entry.async_on_unload(async_at_started(hass, refresh_repairs))
    entry.async_on_unload(hass.bus.async_listen(EVENT_AUTOMATION_RELOADED, refresh_repairs))


@callback
def async_clear_retired_alert_repairs(
    hass: HomeAssistant, entry: ConfigEntry, *, keep: Collection[str] = ()
) -> None:
    """Remove issues whose automations no longer reference the retired sensor."""
    prefix = f"{_ISSUE_PREFIX}{entry.entry_id}_"
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix) and issue_id not in keep:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
