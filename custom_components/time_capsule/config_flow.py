"""Config flow for Time Capsule Sensors.

Setup and later edits both go through the same schema and the same live
connection check (see `_async_validate`), so a saved entry always corresponds
to a Time Capsule we've actually reached with envstat and SMART status at
least once.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv

from .client import TimeCapsuleAuthError, TimeCapsuleConnectionError, fetch_time_capsule_data
from .const import DEFAULT_PORT, DEFAULT_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _build_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): cv.string,
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)
            ): cv.string,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): cv.port,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): cv.string,
        }
    )


async def _async_validate(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Try an actual fetch; raises on failure."""
    await hass.async_add_executor_job(
        fetch_time_capsule_data,
        data[CONF_HOST],
        data[CONF_PORT],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
    )


class TimeCapsuleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Time Capsule Sensors."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()

            try:
                await _async_validate(self.hass, user_input)
            except TimeCapsuleAuthError:
                errors["base"] = "invalid_auth"
            except TimeCapsuleConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Time Capsule connection")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"Time Capsule ({user_input[CONF_HOST]})", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=_build_schema(user_input), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TimeCapsuleOptionsFlow:
        return TimeCapsuleOptionsFlow(config_entry)


class TimeCapsuleOptionsFlow(config_entries.OptionsFlow):
    """Edit an existing entry's connection details (host, user, port, password)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        current = user_input or self.config_entry.data

        if user_input is not None:
            try:
                await _async_validate(self.hass, user_input)
            except TimeCapsuleAuthError:
                errors["base"] = "invalid_auth"
            except TimeCapsuleConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Time Capsule connection")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=user_input,
                    title=f"Time Capsule ({user_input[CONF_HOST]})",
                )
                # Data (not options) holds the connection details, and
                # async_update_entry above already triggers the listener that
                # reloads the entry -- nothing further to store here.
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init", data_schema=_build_schema(current), errors=errors
        )
