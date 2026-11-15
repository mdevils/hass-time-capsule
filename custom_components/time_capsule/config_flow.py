"""Manual setup, Bonjour discovery, and connection settings."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import (
    TimeCapsuleAuthError,
    TimeCapsuleConnectionError,
    TimeCapsuleIdentityError,
    fetch_time_capsule_data,
)
from .const import (
    AIRPORT_TIME_CAPSULE_MODEL,
    CONF_AIRPORT_MAC,
    CONF_ENTITY_KEY,
    CONF_MONITOR_SMB,
    CONF_SMB_PASSWORD,
    CONF_SMB_SHARE,
    CONF_SMB_USERNAME,
    CONF_SSH_FINGERPRINT,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
_CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


def _airport_mac(properties: Mapping[str, Any]) -> str | None:
    """Read Apple's comma-separated TXT string and accept only known Time Capsules."""
    fields = {}
    for name, value in properties.items():
        if not isinstance(value, str):
            continue
        for item in f"{name}={value}".split(","):
            key, separator, field = item.partition("=")
            if separator:
                fields[key.casefold()] = field
    mac = fields.get("wama", "")
    if fields.get("syap") != AIRPORT_TIME_CAPSULE_MODEL:
        return None
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", mac):
        return None
    return mac.replace("-", ":").lower()


def _same_host(first: str, second: str) -> bool:
    return first.rstrip(".").casefold() == second.rstrip(".").casefold()


def _matches_discovery(host: str, discovery: ZeroconfServiceInfo) -> bool:
    return any(
        _same_host(host, address)
        for address in (discovery.hostname, discovery.host, *discovery.addresses)
    )


def _title_for_host(entry: config_entries.ConfigEntry, host: str) -> str:
    """Update the address on generic titles while retaining existing names."""
    if entry.title == f"Time Capsule ({entry.data[CONF_HOST]})":
        return f"Time Capsule ({host})"
    return entry.title


class TimeCapsuleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a Time Capsule and verify its identity before reusing a password."""

    VERSION = 2
    MINOR_VERSION = 2
    _discovery: ZeroconfServiceInfo | None = None
    _mac: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return TimeCapsuleOptionsFlow()

    async def _async_validate(
        self, data: Mapping[str, Any], fingerprint: str | None = None
    ) -> str:
        if not data[CONF_HOST]:
            raise TimeCapsuleConnectionError("Enter a hostname or IP address")
        result = await self.hass.async_add_executor_job(
            fetch_time_capsule_data,
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            fingerprint,
        )
        if result.fingerprint is None or not result.has_readings:
            raise TimeCapsuleConnectionError(
                "SSH worked but no Time Capsule readings were found"
            )
        return result.fingerprint

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        """Offer setup, or update the address of a verified Time Capsule."""
        mac = _airport_mac(discovery_info.properties)
        if mac is None:
            return self.async_abort(reason="not_time_capsule")

        # Reserve this discovery while the confirmation form is open.
        await self.async_set_unique_id(f"airport:{mac}")
        self._abort_if_unique_id_configured()
        for entry in self._async_current_entries():
            data = entry.data
            same_address = _matches_discovery(data[CONF_HOST], discovery_info)
            if data.get(CONF_AIRPORT_MAC) != mac and (
                not same_address or CONF_AIRPORT_MAC in data
            ):
                continue

            update_host = not same_address or _same_host(
                data[CONF_HOST], discovery_info.hostname
            )
            fingerprint = data.get(CONF_SSH_FINGERPRINT)
            if fingerprint and (update_host or CONF_AIRPORT_MAC not in data):
                # Check the saved SSH key before authentication, including when the
                # address is sourced from an unauthenticated Bonjour announcement.
                candidate = {**data, CONF_HOST: discovery_info.host}
                try:
                    await self._async_validate(candidate, fingerprint)
                except TimeCapsuleConnectionError:
                    return self.async_abort(reason="cannot_verify_discovery")
                if entry.data is not data:
                    return self.async_abort(reason="already_configured")
                updates = {CONF_AIRPORT_MAC: mac}
                if update_host:
                    updates[CONF_HOST] = discovery_info.host
                    return self.async_update_reload_and_abort(
                        entry,
                        title=_title_for_host(entry, discovery_info.host),
                        data_updates=updates,
                        reason="already_configured",
                    )
                self.hass.config_entries.async_update_entry(
                    entry, data={**data, **updates}
                )
            return self.async_abort(reason="already_configured")

        self._discovery = discovery_info
        self._mac = mac
        self.context["title_placeholders"] = {
            "name": discovery_info.name.removesuffix(discovery_info.type).rstrip("."),
        }
        return await self.async_step_connect()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Point users to the discovery dashboard before entering an address."""
        return self.async_show_menu(step_id="user", menu_options=["connect"])

    async def async_step_connect(self, user_input: dict[str, Any] | None = None):
        """Set up manually or confirm a discovered device."""
        return await self._async_connection_step("connect", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Change connection details without creating a new device."""
        return await self._async_connection_step(
            "reconfigure", user_input, self._get_reconfigure_entry()
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Ask for new credentials when SSH authentication fails."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        """Check replacement credentials against the saved SSH key."""
        return await self._async_connection_step(
            "reauth_confirm", user_input, self._get_reauth_entry()
        )

    async def _async_connection_step(
        self,
        step: str,
        user_input: dict[str, Any] | None,
        entry: config_entries.ConfigEntry | None = None,
    ):
        errors: dict[str, str] = {}
        current = dict(entry.data) if entry else {}
        if self._discovery:
            current.setdefault(CONF_HOST, self._discovery.host)
        schema = _CONNECTION_SCHEMA
        if step == "reauth_confirm":
            schema = vol.Schema(
                {
                    vol.Required(CONF_USERNAME): cv.string,
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            )
        if user_input is not None:
            data = {**current, **user_input}
            data[CONF_HOST] = data[CONF_HOST].strip()
            candidates = [
                other for other in self._async_current_entries() if other is not entry
            ]
            if any(
                _same_host(other.data[CONF_HOST], data[CONF_HOST])
                for other in candidates
            ):
                return self.async_abort(reason="already_configured")
            try:
                fingerprint = await self._async_validate(
                    data, data.get(CONF_SSH_FINGERPRINT)
                )
            except TimeCapsuleIdentityError:
                errors["base"] = "device_changed"
            except TimeCapsuleAuthError:
                errors["base"] = "invalid_auth"
            except TimeCapsuleConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Time Capsule connection")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    fingerprint, raise_on_progress=entry is None
                )
                discovered = self._discovery is not None and _matches_discovery(
                    data[CONF_HOST], self._discovery
                )
                for other in candidates:
                    if other.data.get(CONF_SSH_FINGERPRINT) == fingerprint:
                        if (
                            discovered
                            and self._mac
                            and CONF_AIRPORT_MAC not in other.data
                        ):
                            self.hass.config_entries.async_update_entry(
                                other, data={**other.data, CONF_AIRPORT_MAC: self._mac}
                            )
                        return self.async_abort(reason="already_configured")
                data[CONF_SSH_FINGERPRINT] = fingerprint
                if entry:
                    return self.async_update_reload_and_abort(
                        entry, title=_title_for_host(entry, data[CONF_HOST]), data=data
                    )
                self._abort_if_unique_id_configured()
                data[CONF_ENTITY_KEY] = fingerprint
                if self._mac and discovered:
                    data[CONF_AIRPORT_MAC] = self._mac
                return self.async_create_entry(
                    title=f"Time Capsule ({data[CONF_HOST]})", data=data
                )
            current = data
        current.pop(CONF_PASSWORD, None)
        return self.async_show_form(
            step_id=step,
            data_schema=self.add_suggested_values_to_schema(schema, current),
            errors=errors,
        )


class TimeCapsuleOptionsFlow(config_entries.OptionsFlowWithReload):
    """Enable the optional SMB checks and set separate share credentials."""

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input[CONF_MONITOR_SMB]:
                return self.async_create_entry(data={})

            share = user_input.get(CONF_SMB_SHARE, "").strip()
            username = user_input.get(CONF_SMB_USERNAME, "").strip()
            password = user_input.get(CONF_SMB_PASSWORD, "")
            saved = self.config_entry.options
            if (share, username) == (
                saved.get(CONF_SMB_SHARE),
                saved.get(CONF_SMB_USERNAME),
            ) and not password:
                password = saved.get(CONF_SMB_PASSWORD, "")
            if any(char in share for char in ("/", "\\", "\x00")) or share in (".", ".."):
                errors[CONF_SMB_SHARE] = "invalid_share"
            elif bool(share) != bool(username and password) or (
                not share and (username or password)
            ):
                errors["base"] = "incomplete_share"
            else:
                return self.async_create_entry(data={
                    CONF_MONITOR_SMB: True,
                    CONF_SMB_SHARE: share,
                    CONF_SMB_USERNAME: username,
                    CONF_SMB_PASSWORD: password,
                })

        saved = self.config_entry.options
        defaults = dict(user_input if user_input is not None else saved)
        # A blank password on an existing share keeps the saved password.
        defaults.pop(CONF_SMB_PASSWORD, None)
        schema = vol.Schema({
            vol.Required(CONF_MONITOR_SMB, default=False): cv.boolean,
            vol.Optional(CONF_SMB_SHARE): cv.string,
            vol.Optional(CONF_SMB_USERNAME): cv.string,
            vol.Optional(CONF_SMB_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        })
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
        )
