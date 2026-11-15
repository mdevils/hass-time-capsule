"""Exercise migration, polling and setup flows using Home Assistant Core."""

import unittest
from dataclasses import replace
from ipaddress import ip_address
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

try:
    from homeassistant.bootstrap import async_from_config_dict
except ModuleNotFoundError as err:
    if err.name != "homeassistant":
        raise
    raise unittest.SkipTest(
        "Install Home Assistant Core for the integration tests"
    ) from err

from homeassistant import loader
from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.time_capsule.client import (
    TimeCapsuleAuthError,
    TimeCapsuleConnectionError,
    TimeCapsuleData,
    TimeCapsuleIdentityError,
    fetch_time_capsule_data,
)
from custom_components.time_capsule.const import (
    BASE_STATION_NAME_COMMAND,
    CONF_AIRPORT_MAC,
    CONF_ENTITY_KEY,
    CONF_SSH_FINGERPRINT,
    DF_COMMAND,
    DOMAIN,
    ENVSTAT_COMMAND,
    MOUNT_COMMAND,
    CONF_MONITOR_SMB,
    CONF_SMB_SHARE,
    CONF_SMB_USERNAME,
    CONF_SMB_PASSWORD,
    SMART_COMMAND,
)
from custom_components.time_capsule.df import is_volume_mounted, parse_df
from custom_components.time_capsule.diagnostics import async_get_config_entry_diagnostics
from custom_components.time_capsule.smb import SmbStatus
from custom_components.time_capsule.envstat import parse_envstat
from custom_components.time_capsule.smart import parse_smart_status

CONNECTION = {"host": "192.0.2.10", "port": 22, "username": "root", "password": "test"}
FINGERPRINT = "SHA256:fake-test-key"
ENVSTAT = "[tsensor0]\nT_internal: 45.32 degC"
SMART = "SMART supported, SMART enabled\n197 100 0 no online positive Current pending sector 0"
DF = "Filesystem 512-blocks Used Avail %Cap Mounted on\n/dev/dk2 10000 2500 7500 25% /Volumes/dk2"


def poll_result(*, no_disk=False):
    return TimeCapsuleData(
        envstat={reading.key: reading for reading in parse_envstat(ENVSTAT)},
        smart=parse_smart_status(SMART),
        disk=None if no_disk else parse_df(DF),
        fingerprint=FINGERPRINT,
        volume_mounted=not no_disk,
        errors={"disk": "Disk offline"} if no_disk else {},
    )


def discovery(address, model="119"):
    ip = ip_address(address)
    return ZeroconfServiceInfo(
        ip_address=ip,
        ip_addresses=[ip],
        port=5009,
        hostname="Time-Capsule-Test.local.",
        type="_airport._tcp.local.",
        name="Time Capsule Test._airport._tcp.local.",
        properties={"waMA": f"02-00-00-00-00-01,raSt=0,syAP={model},syVs=7.9.1"},
    )


class TestPolling(unittest.TestCase):
    def test_disk_failure_preserves_other_readings(self):
        transport = MagicMock()
        outcomes = {ENVSTAT_COMMAND: ENVSTAT, SMART_COMMAND: SMART}

        def run_command(_, command):
            if command == DF_COMMAND:
                raise TimeCapsuleConnectionError("Disk offline")
            if command == MOUNT_COMMAND:
                return "/dev/md0a on / type ffs (local)"
            if command == BASE_STATION_NAME_COMMAND:
                raise TimeCapsuleConnectionError("ACP unavailable")
            return outcomes[command]

        with (
            patch(
                "custom_components.time_capsule.client._connect",
                return_value=(transport, FINGERPRINT),
            ),
            patch(
                "custom_components.time_capsule.client._run_command",
                side_effect=run_command,
            ),
        ):
            result = fetch_time_capsule_data(**CONNECTION, include_name=True)

        self.assertIsNone(result.base_station_name)
        self.assertIs(result.volume_mounted, False)
        self.assertEqual(set(result.errors), {"disk"})
        self.assertEqual(result.successful_checks, {"environment", "smart"})
        self.assertEqual(result.smart.status, "Passed")
        self.assertEqual(len(result.envstat), 1)
        transport.close.assert_called_once()

    def test_smart_failure_verdict_survives_command_error(self):
        from custom_components.time_capsule.client import _run_command

        transport = MagicMock()
        channel = transport.open_session.return_value.__enter__.return_value
        channel.recv_exit_status.return_value = 1

        channel.recv.side_effect = [b"Threshold exceeds condition\n", b""]
        self.assertEqual(
            parse_smart_status(_run_command(transport, SMART_COMMAND)).status,
            "Failed",
        )

        channel.recv.side_effect = [SMART.encode(), b""]
        with self.assertRaises(TimeCapsuleConnectionError):
            _run_command(transport, SMART_COMMAND)

    def test_mount_output_distinguishes_missing_and_unreadable(self):
        listing = "/dev/md0a on / type ffs (local)\n"
        self.assertIs(is_volume_mounted(listing, "/dev/dk2"), False)
        self.assertIs(
            is_volume_mounted(
                listing + "/dev/dk2 on /Volumes/Backup Data type hfs (local)",
                "/dev/dk2",
            ),
            True,
        )
        self.assertIsNone(is_volume_mounted("mount: permission denied", "/dev/dk2"))

    def test_changed_ssh_key_is_rejected_before_password_is_sent(self):
        from custom_components.time_capsule.client import _connect

        with (
            patch("custom_components.time_capsule.client.socket.create_connection"),
            patch(
                "custom_components.time_capsule.client.paramiko.Transport"
            ) as transport_class,
        ):
            transport = transport_class.return_value
            transport.get_security_options.return_value.key_types = ()
            transport.get_remote_server_key.return_value.asbytes.return_value = (
                b"wrong key"
            )
            with self.assertRaises(TimeCapsuleIdentityError):
                _connect(**CONNECTION, fingerprint=FINGERPRINT)
            transport.auth_password.assert_not_called()
            transport.close.assert_called_once()


class TestHomeAssistant(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "custom_components").symlink_to(
            Path(__file__).resolve().parents[1] / "custom_components"
        )
        self.hass = HomeAssistant(directory.name)
        loader.async_setup(self.hass)
        self.hass.config.skip_pip = True
        await async_from_config_dict({"homeassistant": {}}, self.hass)
        self.addAsyncCleanup(self.hass.async_stop, force=True)

    async def async_add_legacy_entry(self, *, version=1, sensors=(), options=None):
        entry = ConfigEntry(
            domain=DOMAIN,
            title=f"Time Capsule ({CONNECTION['host']})",
            data=(
                CONNECTION if version == 1
                else {**CONNECTION, CONF_ENTITY_KEY: CONNECTION["host"]}
            ),
            options=options or {},
            source="user",
            version=version,
            minor_version=1,
            unique_id=CONNECTION["host"],
            discovery_keys={},
            subentries_data=None,
            disabled_by=ConfigEntryDisabler.USER if sensors else None,
        )
        await self.hass.config_entries.async_add(entry)
        registry = er.async_get(self.hass)
        for platform, suffix, attributes in sensors:
            registry.async_get_or_create(
                platform, DOMAIN, f"{DOMAIN}_{CONNECTION['host']}_{suffix}",
                config_entry=entry, **attributes,
            )
        if sensors:
            await self.hass.config_entries.async_set_disabled_by(entry.entry_id, None)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        return entry

    def entity_id(self, platform, key, suffix):
        value = er.async_get(self.hass).async_get_entity_id(
            platform, DOMAIN, f"{DOMAIN}_{key}_{suffix}"
        )
        self.assertIsNotNone(value)
        return value

    async def test_legacy_entity_ids_survive_reconfigure(self):
        with patch(
            "custom_components.time_capsule.fetch_time_capsule_data",
            return_value=poll_result(),
        ):
            entry = await self.async_add_legacy_entry()
            self.assertEqual(entry.version, 2)
            key = CONNECTION["host"]
            self.assertEqual(entry.data[CONF_ENTITY_KEY], key)
            old_id = self.entity_id("sensor", key, "smart_status")

            flow = await self.hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
            )
            self.assertEqual(flow["step_id"], "reconfigure")
            new_address = "192.0.2.20"
            with patch(
                "custom_components.time_capsule.config_flow.fetch_time_capsule_data",
                return_value=poll_result(),
            ) as validate:
                result = await self.hass.config_entries.flow.async_configure(
                    flow["flow_id"], {**CONNECTION, "host": new_address}
                )
                self.assertEqual(validate.call_args.args[-1], FINGERPRINT)
            self.assertEqual(result["reason"], "reconfigure_successful")
            await self.hass.async_block_till_done()
            self.assertEqual(entry.data["host"], new_address)
            self.assertEqual(self.entity_id("sensor", key, "smart_status"), old_id)
            self.assertEqual(len(self.hass.config_entries.async_entries(DOMAIN)), 1)

    async def test_offline_then_partial_then_complete_poll(self):
        with patch(
            "custom_components.time_capsule.fetch_time_capsule_data",
            side_effect=[
                TimeCapsuleConnectionError("offline"),
                replace(poll_result(no_disk=True), base_station_name="Time Capsule Media"),
                poll_result(),
                TimeCapsuleConnectionError("offline"),
            ],
        ) as fetch:
            entry = await self.async_add_legacy_entry()
            key = CONNECTION["host"]
            mounted = self.entity_id("binary_sensor", key, "volume_mounted")
            last = self.entity_id("sensor", key, "last_reading")
            disk = self.entity_id("sensor", key, "disk_free")
            self.assertEqual(self.hass.states.get(mounted).state, "unavailable")
            registry = dr.async_get(self.hass)
            (device,) = dr.async_entries_for_config_entry(registry, entry.entry_id)
            registry.async_update_device(device.id, name_by_user="My Capsule")

            await entry.runtime_data.hardware.async_refresh()
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(mounted).state, "off")
            self.assertTrue(fetch.call_args.kwargs["include_name"])
            self.assertEqual(entry.title, "Time Capsule Media")
            self.assertEqual(registry.async_get(device.id).name, entry.title)
            self.assertEqual(registry.async_get(device.id).name_by_user, "My Capsule")
            self.assertEqual(
                self.hass.states.get(last).attributes["failed_checks"], ["disk"]
            )
            self.assertEqual(self.hass.states.get(disk).state, "unavailable")
            smart = self.entity_id("sensor", key, "smart_197")
            self.assertEqual(self.hass.states.get(smart).state, "0")

            await entry.runtime_data.hardware.async_refresh()
            self.assertFalse(fetch.call_args.kwargs["include_name"])
            self.assertEqual(registry.async_get(device.id).name, entry.title)
            last_value = self.hass.states.get(last).state
            self.assertNotEqual(self.hass.states.get(disk).state, "unavailable")
            await entry.runtime_data.hardware.async_refresh()
            self.assertEqual(self.hass.states.get(mounted).state, "unavailable")
            self.assertEqual(self.hass.states.get(disk).state, "unavailable")
            self.assertEqual(self.hass.states.get(last).state, last_value)

    async def test_sensor_upgrade_keeps_the_ten_health_readings(self):
        key = CONNECTION["host"]
        attributes = {
            1: ("Raw read error rate", 120779000),
            5: ("Reallocated sector count", 0),
            9: ("Power-on hours count", 16363),
            10: ("Spin retry count", 0),
            12: ("Device power cycle count", 4236),
            184: ("Unknown", 0),
            187: ("Reported uncorrect", 0),
            190: ("Airflow Temperature", 52),
            194: ("Temperature", 49),
            197: ("Current pending sector", 0),
            198: ("Offline uncorrectable", 0),
            199: ("Ultra DMA CRC error count", 0),
        }

        def smart_without(*excluded):
            rows = [
                f"{attr_id} 100 0 no online positive {label} {raw}"
                for attr_id, (label, raw) in attributes.items()
                if attr_id not in excluded
            ]
            return parse_smart_status("\n".join(["SMART supported, SMART enabled", *rows]))

        fan = parse_envstat("[tsensor0]\nFan_rpm: 1757 RPM")[0]
        readings = poll_result()
        full = replace(
            readings,
            envstat={**readings.envstat, fan.key: fan},
            smart=smart_without(),
        )
        legacy_sensors = (
            ("binary_sensor", "ssh_connected", {}),
            ("sensor", "smart_1", {}),
            ("sensor", "smart_184", {}),
            ("sensor", "smart_190", {"original_device_class": "temperature"}),
            ("sensor", "tsensor0_T_internal", {"original_device_class": "temperature"}),
            ("sensor", "smart_194", {
                "suggested_object_id": "my_drive_temp",
                "entity_category": EntityCategory.DIAGNOSTIC,
            }),
            ("sensor", fan.key, {"suggested_object_id": "my_fan_speed"}),
            ("sensor", "smart_9", {
                "suggested_object_id": "my_drive_runtime",
                "original_device_class": "duration",
                "unit_of_measurement": "h",
            }),
        )
        with patch(
            "custom_components.time_capsule.fetch_time_capsule_data",
            side_effect=[
                full,
                replace(full, smart=smart_without(194)),
                replace(full, smart=smart_without(190, 194)),
            ],
        ):
            entry = await self.async_add_legacy_entry(version=2, sensors=legacy_sensors)
            self.assertEqual(entry.minor_version, 2)
            registry = er.async_get(self.hass)
            entity_keys = {
                entity.unique_id.removeprefix(f"{DOMAIN}_{key}_")
                for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
            }
            self.assertEqual(entity_keys, {
                "smart_5", "smart_9", "smart_10", "smart_12", "smart_187",
                "smart_194", "smart_197", "smart_198", "smart_199", "last_reading",
                "smart_status", "volume_mounted", "disk_free", "disk_used",
                "disk_total", "disk_percent", "disk_free_percent", fan.key,
            })

            runtime = self.hass.states.get("sensor.my_drive_runtime")
            self.assertEqual(runtime.attributes["unit_of_measurement"], "d")
            self.assertAlmostEqual(float(runtime.state), 16363 / 24)

            fan_speed = "sensor.my_fan_speed"
            self.assertEqual(
                registry.async_get(fan_speed).entity_category, EntityCategory.DIAGNOSTIC
            )
            self.assertEqual(float(self.hass.states.get(fan_speed).state), 1757)

            temperature = "sensor.my_drive_temp"
            self.assertIsNone(registry.async_get(temperature).entity_category)
            self.assertEqual(float(self.hass.states.get(temperature).state), 49)
            await entry.runtime_data.hardware.async_refresh()
            self.assertEqual(float(self.hass.states.get(temperature).state), 52)
            await entry.runtime_data.hardware.async_refresh()
            self.assertEqual(float(self.hass.states.get(temperature).state), 45.32)

    async def test_upgrade_reports_retired_sensor_in_existing_blueprint(self):
        root = Path(self.hass.config.config_dir)
        blueprint = root / "blueprints/automation/time-capsule/monitoring.yaml"
        blueprint.parent.mkdir(parents=True)
        blueprint.write_text("""
blueprint:
  name: Stored Time Capsule alert
  domain: automation
  input:
    saved_state: {}
    connectivity: {}
    temperature: {}
variables:
  state_helper: !input saved_state
  connectivity_entities: !input connectivity
  temperature_entity: !input temperature
triggers:
  - trigger: homeassistant
    event: start
actions: []
""")
        old_link = "binary_sensor.my_old_connection"
        old_temperature = "sensor.my_old_temperature"
        (root / "configuration.yaml").write_text("""
input_text:
  capsule_alerts:
    max: 255
automation:
  - id: capsule_alerts
    alias: Capsule alerts
    use_blueprint:
      path: time-capsule/monitoring.yaml
      input:
        saved_state: input_text.capsule_alerts
        connectivity: [binary_sensor.my_old_connection]
        temperature: sensor.my_old_temperature
""")
        await self.hass.services.async_call("automation", "reload", blocking=True)
        with patch(
            "custom_components.time_capsule.fetch_time_capsule_data",
            return_value=poll_result(),
        ):
            entry = await self.async_add_legacy_entry(
                version=2,
                sensors=(
                    ("binary_sensor", "ssh_connected", {
                        "suggested_object_id": "my_old_connection",
                    }),
                    ("sensor", "smart_190", {
                        "suggested_object_id": "my_old_temperature",
                        "original_device_class": "temperature",
                    }),
                    ("sensor", "tsensor0_T_external1", {
                        "original_device_class": "temperature",
                    }),
                ),
            )
        await self.hass.async_start()
        await self.hass.async_block_till_done()

        issues = ir.async_get(self.hass)
        repairs = {
            issue.translation_placeholders["entity_id"]: issue
            for (domain, _), issue in issues.issues.items()
            if domain == DOMAIN
        }
        self.assertEqual(set(repairs), {old_link, old_temperature})
        device_key = entry.data[CONF_ENTITY_KEY]
        replacement_link = self.entity_id("binary_sensor", device_key, "volume_mounted")
        replacement_temperature = self.entity_id("sensor", device_key, "smart_194")
        self.assertEqual(
            repairs[old_link].translation_placeholders["replacement"], replacement_link
        )
        self.assertEqual(
            repairs[old_temperature].translation_placeholders["replacement"],
            replacement_temperature,
        )

        config_file = root / "configuration.yaml"
        config_file.write_text(
            config_file.read_text()
            .replace(old_link, replacement_link)
            .replace(old_temperature, replacement_temperature)
        )
        await self.hass.services.async_call("automation", "reload", blocking=True)
        await self.hass.async_block_till_done()
        for repair in repairs.values():
            self.assertIsNone(issues.async_get_issue(DOMAIN, repair.issue_id))

    async def test_smb_and_mount_checks_and_diagnostics(self):
        with (
            patch(
                "custom_components.time_capsule.fetch_time_capsule_data",
                side_effect=[
                    poll_result(),
                    poll_result(no_disk=True),  # SSH works while the volume and SMB fail.
                    TimeCapsuleConnectionError("offline"),  # SMB can recover first.
                    poll_result(),
                ],
            ),
            patch(
                "custom_components.time_capsule.probe_smb",
                side_effect=[SmbStatus(False, False), SmbStatus(True, True), SmbStatus(True, True)],
            ) as probe,
        ):
            entry = await self.async_add_legacy_entry()
            key = CONNECTION["host"]
            mounted = self.entity_id("binary_sensor", key, "volume_mounted")
            self.assertEqual(self.hass.states.get(mounted).state, "on")
            probe.assert_not_called()

            flows = self.hass.config_entries.options
            flow = await flows.async_init(entry.entry_id)
            data = {
                CONF_MONITOR_SMB: True,
                CONF_SMB_SHARE: "Backup Data",
                CONF_SMB_USERNAME: "smb-test-account",
                CONF_SMB_PASSWORD: "smb-test-password",
            }
            result = await flows.async_configure(flow["flow_id"], data)
            self.assertEqual(result["data"], data)
            await self.hass.async_block_till_done()
            probe.assert_called_with(CONNECTION["host"], "Backup Data", "smb-test-account", "smb-test-password")
            port = self.entity_id("binary_sensor", key, "smb_port_open")
            share = self.entity_id("binary_sensor", key, "smb_share_accessible")
            self.assertEqual(self.hass.states.get(mounted).state, "off")
            self.assertEqual(self.hass.states.get(port).state, "off")
            self.assertEqual(self.hass.states.get(share).state, "off")

            await entry.runtime_data.hardware.async_refresh()
            self.assertEqual(self.hass.states.get(mounted).state, "unavailable")
            self.assertEqual(self.hass.states.get(share).state, "off")

            await entry.runtime_data.smb.async_refresh()
            self.assertEqual(self.hass.states.get(share).state, "on")

            await entry.runtime_data.hardware.async_refresh()
            self.assertEqual(self.hass.states.get(mounted).state, "on")

            diagnostics = await async_get_config_entry_diagnostics(self.hass, entry)
            self.assertTrue(diagnostics["checks"]["smb"]["share_accessible"])
            for secret in (CONNECTION["host"], FINGERPRINT, *list(data.values())[1:]):
                self.assertNotIn(secret, str(diagnostics))

    async def test_smb_stays_active_during_ssh_reauth(self):
        options = {
            CONF_MONITOR_SMB: True,
            CONF_SMB_SHARE: "Backup Data",
            CONF_SMB_USERNAME: "smb-user",
            CONF_SMB_PASSWORD: "smb-password",
        }
        with (
            patch(
                "custom_components.time_capsule.fetch_time_capsule_data",
                return_value=poll_result(),
            ) as fetch,
            patch(
                "custom_components.time_capsule.probe_smb",
                return_value=SmbStatus(True, True),
            ) as probe,
        ):
            entry = await self.async_add_legacy_entry(options=options)
            key = entry.data[CONF_ENTITY_KEY]
            mount = self.entity_id("binary_sensor", key, "volume_mounted")
            port = self.entity_id("binary_sensor", key, "smb_port_open")
            share = self.entity_id("binary_sensor", key, "smb_share_accessible")
            self.assertEqual(self.hass.states.get(mount).state, "on")
            self.assertEqual(self.hass.states.get(share).state, "on")

            fetch.side_effect = TimeCapsuleAuthError("SSH password rejected")
            await entry.runtime_data.hardware.async_refresh()
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(mount).state, "unavailable")
            self.assertEqual(self.hass.states.get(port).state, "on")
            self.assertEqual(self.hass.states.get(share).state, "on")
            [reauth] = entry.async_get_active_flows(self.hass, {"reauth"})
            self.hass.config_entries.flow.async_abort(reauth["flow_id"])

            await self.hass.config_entries.async_reload(entry.entry_id)
            await self.hass.async_block_till_done()
            self.assertEqual(entry.state, ConfigEntryState.LOADED)
            self.assertEqual(self.hass.states.get(mount).state, "unavailable")
            self.assertEqual(self.hass.states.get(share).state, "on")

            fetches = fetch.call_count
            probe.return_value = SmbStatus(True, False)
            await self.hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": share}, blocking=True
            )
            self.assertEqual(fetch.call_count, fetches)
            self.assertEqual(self.hass.states.get(port).state, "on")
            self.assertEqual(self.hass.states.get(share).state, "off")
            diagnostics = await async_get_config_entry_diagnostics(self.hass, entry)
            self.assertFalse(diagnostics["checks"]["ssh_connected"])
            self.assertEqual(
                diagnostics["checks"]["smb"],
                {"port_open": True, "share_accessible": False},
            )

            flows = entry.async_get_active_flows(self.hass, {"reauth"})
            [reauth] = flows
            fetch.side_effect = None
            with patch(
                "custom_components.time_capsule.config_flow.fetch_time_capsule_data",
                return_value=poll_result(),
            ):
                result = await self.hass.config_entries.flow.async_configure(
                    reauth["flow_id"], {"username": "root", "password": "replacement"}
                )
            self.assertEqual(result["reason"], "reauth_successful")
            await self.hass.async_block_till_done()
            self.assertEqual(self.hass.states.get(mount).state, "on")
            self.assertEqual(self.hass.states.get(share).state, "off")

    async def test_user_setup_offers_manual_connection_from_the_discovery_menu(self):
        flows = self.hass.config_entries.flow
        flow = await flows.async_init(DOMAIN, context={"source": "user"})
        self.assertEqual(flow["type"], "menu")
        self.assertEqual(flow["step_id"], "user")
        self.assertEqual(flow["menu_options"], ["connect"])

        form = await flows.async_configure(flow["flow_id"], {"next_step_id": "connect"})
        self.assertEqual(form["type"], "form")
        self.assertEqual(form["step_id"], "connect")
        self.assertEqual(
            set(form["data_schema"].schema), {"host", "username", "port", "password"}
        )

    async def test_discovery_confirms_ssh_credentials_and_sets_up_one_entry(self):
        flows = self.hass.config_entries.flow
        flow = await flows.async_init(
            DOMAIN, context={"source": "zeroconf"}, data=discovery(CONNECTION["host"])
        )
        self.assertEqual(flow["step_id"], "connect")
        host_field = next(key for key in flow["data_schema"].schema if key == "host")
        host = host_field.description["suggested_value"]
        self.assertEqual(host, CONNECTION["host"])

        with (
            patch(
                "custom_components.time_capsule.fetch_time_capsule_data",
                return_value=poll_result(),
            ),
            patch(
                "custom_components.time_capsule.config_flow.fetch_time_capsule_data",
                return_value=poll_result(),
            ),
        ):
            result = await flows.async_configure(
                flow["flow_id"], {**CONNECTION, "host": host}
            )
            await self.hass.async_block_till_done()
        entry = result["result"]
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        self.assertEqual(entry.data["host"], host)
        self.assertEqual(entry.data[CONF_AIRPORT_MAC], "02:00:00:00:00:01")
        self.assertEqual(entry.unique_id, FINGERPRINT)
        self.assertEqual(len(self.hass.config_entries.async_entries(DOMAIN)), 1)

    async def test_discovery_replaces_saved_hostname_after_ssh_verification(self):
        info = discovery(CONNECTION["host"])
        hostname = info.hostname.rstrip(".")
        flows = self.hass.config_entries.flow
        with patch(
            "custom_components.time_capsule.fetch_time_capsule_data",
            return_value=poll_result(),
        ):
            entry = await self.async_add_legacy_entry()
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "host": hostname,
                    CONF_AIRPORT_MAC: "02:00:00:00:00:01",
                },
                title=f"Time Capsule ({hostname})",
            )
            entity_key = entry.data[CONF_ENTITY_KEY]
            with patch(
                "custom_components.time_capsule.config_flow.fetch_time_capsule_data",
                side_effect=[TimeCapsuleIdentityError("changed"), poll_result()],
            ) as validate:
                rejected = await flows.async_init(
                    DOMAIN, context={"source": "zeroconf"}, data=info
                )
                self.assertEqual(rejected["reason"], "cannot_verify_discovery")
                self.assertEqual(entry.data["host"], hostname)

                updated = await flows.async_init(
                    DOMAIN, context={"source": "zeroconf"}, data=info
                )
                await self.hass.async_block_till_done()
            self.assertEqual(updated["reason"], "already_configured")
            validate.assert_called_with(
                CONNECTION["host"],
                CONNECTION["port"],
                CONNECTION["username"],
                CONNECTION["password"],
                FINGERPRINT,
            )
            self.assertEqual(entry.data["host"], CONNECTION["host"])
            self.assertEqual(entry.title, f"Time Capsule ({CONNECTION['host']})")
            self.assertEqual(entry.data[CONF_ENTITY_KEY], entity_key)
            self.assertEqual(self.hass.config_entries.async_entries(DOMAIN), [entry])

    async def test_last_reading_is_restored_when_reloaded_offline(self):
        with patch(
            "custom_components.time_capsule.fetch_time_capsule_data",
            return_value=poll_result(),
        ):
            entry = await self.async_add_legacy_entry()
        last = self.entity_id("sensor", CONNECTION["host"], "last_reading")
        last_value = self.hass.states.get(last).state

        with patch(
            "custom_components.time_capsule.fetch_time_capsule_data",
            side_effect=TimeCapsuleConnectionError("offline"),
        ):
            await self.hass.config_entries.async_reload(entry.entry_id)
            await self.hass.async_block_till_done()

        self.assertEqual(self.hass.states.get(last).state, last_value)
        self.assertEqual(
            self.hass.states.get(last).attributes["failed_checks"],
            ["environment", "smart", "disk"],
        )

    async def test_discovery_filters_models_and_updates_only_the_verified_device(self):
        flows = self.hass.config_entries.flow
        with patch(
            "custom_components.time_capsule.fetch_time_capsule_data",
            return_value=poll_result(),
        ):
            wrong = await flows.async_init(
                DOMAIN,
                context={"source": "zeroconf"},
                data=discovery("192.0.2.10", "120"),
            )
            self.assertEqual(wrong["reason"], "not_time_capsule")
            entry = await self.async_add_legacy_entry()
            with patch(
                "custom_components.time_capsule.config_flow.fetch_time_capsule_data",
                return_value=poll_result(),
            ) as validate:
                found = await flows.async_init(
                    DOMAIN, context={"source": "zeroconf"}, data=discovery("192.0.2.10")
                )
                self.assertEqual(found["reason"], "already_configured")
                self.assertIn(CONF_AIRPORT_MAC, entry.data)
                moved = await flows.async_init(
                    DOMAIN, context={"source": "zeroconf"}, data=discovery("192.0.2.22")
                )
                self.assertEqual(moved["reason"], "already_configured")
                self.assertEqual(
                    validate.call_args.args[-1], entry.data[CONF_SSH_FINGERPRINT]
                )
                self.assertEqual(entry.data["host"], "192.0.2.22")
            with patch(
                "custom_components.time_capsule.config_flow.fetch_time_capsule_data",
                side_effect=TimeCapsuleIdentityError("changed"),
            ):
                rejected = await flows.async_init(
                    DOMAIN, context={"source": "zeroconf"}, data=discovery("192.0.2.23")
                )
            self.assertEqual(rejected["reason"], "cannot_verify_discovery")
            self.assertEqual(entry.data["host"], "192.0.2.22")
