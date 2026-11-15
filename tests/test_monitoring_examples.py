"""Run the sample webhook and alert automation in Home Assistant."""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

try:
    from homeassistant.bootstrap import async_from_config_dict
except ModuleNotFoundError as err:
    if err.name != "homeassistant":
        raise
    raise unittest.SkipTest("Install Home Assistant Core for the monitoring tests") from err

from homeassistant import loader
from homeassistant.components.persistent_notification import DOMAIN as NOTIFICATION_DOMAIN
from homeassistant.components.webhook import async_handle_webhook
from homeassistant.core import HomeAssistant
from homeassistant.util.aiohttp import MockRequest
from homeassistant.util.dt import utcnow
from homeassistant.util.yaml import Secrets, load_yaml

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT_PATH = "time-capsule/monitoring.yaml"
WEBHOOK = "test-only-webhook"
SMART = "sensor.capsule_smart"
REALLOCATED = "sensor.capsule_reallocated"
CONNECTION = "binary_sensor.capsule_connection"
DISK = "sensor.capsule_disk_free"
TEMPERATURE = "sensor.capsule_temperature"
HELPER = "input_text.capsule_alerts"
AUTOMATION = "automation.capsule_alerts"


class TestMonitoringExamples(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        blueprint = self.root / "blueprints" / "automation" / BLUEPRINT_PATH
        blueprint.parent.mkdir(parents=True)
        copyfile(ROOT / "blueprints" / "automation" / BLUEPRINT_PATH, blueprint)
        copyfile(ROOT / "examples" / "time_machine_backup.yaml", self.root / "backup.yaml")
        (self.root / "secrets.yaml").write_text(f"time_machine_mac_webhook: {WEBHOOK}\n")
        self.messages = []
        self.hass = None
        self.addAsyncCleanup(self.stop_hass)

    async def start_hass(self):
        self.hass = HomeAssistant(str(self.root))
        loader.async_setup(self.hass)
        self.hass.config.skip_pip = True

        async def record(call):
            self.messages.append(call.data["message"])

        self.hass.services.async_register("test", "record", record)
        package = load_yaml(str(self.root / "backup.yaml"), Secrets(self.root))
        await async_from_config_dict(
            {
                "homeassistant": {},
                "input_text": {"capsule_alerts": {"max": 255}},
                "automation": [*package["automation"], {
                    "alias": "Capsule alerts",
                    "id": "capsule_alerts",
                    "use_blueprint": {
                        "path": BLUEPRINT_PATH,
                        "input": {
                            "saved_state": HELPER,
                            "smart": SMART,
                            "reallocated": REALLOCATED,
                            "connectivity": [CONNECTION],
                            "disk_free": DISK,
                            "temperature": TEMPERATURE,
                            "notification_actions": [{
                                "action": "test.record",
                                "data": {"message": "{{ message }}"},
                            }],
                        },
                    },
                }],
                "template": package["template"],
            },
            self.hass,
        )
        await self.hass.async_block_till_done()

    async def stop_hass(self):
        if self.hass is not None:
            await self.hass.async_stop(force=True)
            self.hass = None

    async def post_report(self, body):
        response = await async_handle_webhook(
            self.hass,
            WEBHOOK,
            MockRequest(
                json.dumps(body).encode(),
                "local test",
                method="POST",
                headers={"Content-Type": "application/json"},
                remote="127.0.0.1",
            ),
        )
        self.assertEqual(response.status, 200)
        await self.hass.async_block_till_done()

    async def run_alerts(self):
        await self.hass.services.async_call(
            "automation",
            "trigger",
            {"entity_id": AUTOMATION, "skip_condition": False},
            blocking=True,
        )
        await self.hass.async_block_till_done()

    async def test_report_status_and_heartbeat_preserve_latest_backup(self):
        await self.start_hass()
        await self.hass.async_start()
        await self.hass.async_block_till_done()
        latest = "sensor.mac_last_time_capsule_backup"
        status = "sensor.mac_time_capsule_reporter_status"
        heartbeat = "sensor.mac_time_capsule_last_report"
        self.assertEqual(self.hass.states.get(latest).state, "unknown")
        await self.post_report({"status": "destination_unavailable"})
        self.assertEqual(self.hass.states.get(status).state, "destination_unavailable")
        self.assertEqual(self.hass.states.get(latest).state, "unknown")
        first_report = datetime.fromisoformat(self.hass.states.get(heartbeat).state)
        self.assertLess(utcnow() - first_report, timedelta(seconds=5))

        completed = utcnow() - timedelta(hours=12)
        await self.post_report({"backup_at": completed.isoformat()})
        saved = self.hass.states.get(latest).state
        self.assertEqual(self.hass.states.get(status).state, "ok")

        await self.post_report({"status": "ok", "backup_at": (completed - timedelta(days=1)).isoformat()})
        await self.post_report({"status": "read_error"})
        self.assertEqual(self.hass.states.get(latest).state, saved)
        self.assertEqual(self.hass.states.get(status).state, "read_error")
        last_report = self.hass.states.get(heartbeat).state
        await self.post_report({"status": "ok", "backup_at": "invalid"})
        self.assertEqual(self.hass.states.get(heartbeat).state, last_report)

    async def test_faults_and_sector_baseline_survive_home_assistant_restart(self):
        await self.start_hass()
        self.hass.states.async_set(SMART, "Failed")
        self.hass.states.async_set(REALLOCATED, "12")
        await self.hass.async_start()
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.messages), 1)
        self.assertIn("SMART reported a drive failure", self.messages[0])
        self.assertEqual(json.loads(self.hass.states.get(HELPER).state)["r"], 12)

        await self.stop_hass()
        await self.start_hass()
        self.hass.states.async_set(SMART, "Failed")
        self.hass.states.async_set(REALLOCATED, "13")
        await self.hass.async_start()
        await self.hass.async_block_till_done()
        self.assertEqual(len(self.messages), 2)
        self.assertIn("Reallocated sectors increased from 12 to 13", self.messages[1])

        self.hass.states.async_set(SMART, "unavailable")
        await self.run_alerts()
        self.assertEqual(len(self.messages), 2)
        self.hass.states.async_set(SMART, "Passed")
        await self.run_alerts()
        self.assertEqual(len(self.messages), 3)
        self.assertIn("Cleared: SMART", self.messages[2])

    async def test_sustained_connection_temperature_and_disk_alerts(self):
        await self.start_hass()
        self.hass.states.async_set(CONNECTION, "unavailable")
        self.hass.states.async_set(TEMPERATURE, "58")
        self.hass.states.async_set(DISK, "9.5")
        await self.hass.async_start()
        await self.hass.async_block_till_done()
        self.assertEqual(self.messages, [])

        tracked = json.loads(self.hass.states.get(HELPER).state)
        self.assertEqual(set(tracked["p"]), {"c", "d", "t"})
        then = int((utcnow() - timedelta(minutes=16)).timestamp())
        tracked["p"] = {check: then for check in tracked["p"]}
        await self.hass.services.async_call(
            "input_text", "set_value",
            {"entity_id": HELPER, "value": json.dumps(tracked)},
            blocking=True,
        )
        await self.run_alerts()
        self.assertEqual(len(self.messages), 1)
        for description in ("connection", "Temperature", "Free disk space"):
            self.assertIn(description, self.messages[0])

        self.hass.states.async_set(DISK, "12.5")
        await self.run_alerts()
        self.assertIn("Cleared: Free disk space", self.messages[1])
        self.assertEqual(set(json.loads(self.hass.states.get(HELPER).state)["a"]), {"c", "t"})

    async def test_missing_selected_sensors_show_a_single_configuration_warning(self):
        await self.start_hass()
        self.hass.states.async_set(CONNECTION, "on")
        self.hass.states.async_set(SMART, "Unknown")
        self.hass.states.async_set(REALLOCATED, "0")
        self.hass.states.async_set(DISK, "unknown")
        self.hass.states.async_set(TEMPERATURE, "unavailable")
        await self.hass.async_start()
        await self.hass.async_block_till_done()
        notification_id = f"{AUTOMATION}_missing_sensors"
        await self.run_alerts()
        self.assertNotIn(notification_id, self.hass.data.get(NOTIFICATION_DOMAIN, {}))

        self.hass.states.async_remove(CONNECTION)
        self.hass.states.async_remove(TEMPERATURE)
        await self.run_alerts()
        notifications = self.hass.data[NOTIFICATION_DOMAIN]
        notice = notifications[notification_id]
        await self.run_alerts()
        self.assertIs(notifications[notification_id], notice)
        self.assertIn(CONNECTION, notice["message"])
        self.assertIn(TEMPERATURE, notice["message"])
        self.assertNotIn(DISK, notice["message"])
        self.assertEqual(self.messages, [])

        self.hass.states.async_set(CONNECTION, "on")
        self.hass.states.async_set(TEMPERATURE, "unavailable")
        await self.run_alerts()
        self.assertNotIn(notification_id, notifications)
