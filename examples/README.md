# Time Capsule alerts and Time Machine reporting

## Set up alerts

[![Import the Time Capsule alert blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmdevils%2Fhass-time-capsule%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ftime-capsule%2Fmonitoring.yaml)

1. In **Settings → Devices & services → Helpers**, create a **Text** helper
   with a maximum length of **255**. Give each alert automation its own helper
   so it can remember active alerts and sector counts across restarts.
2. Import the [Time Capsule alert blueprint](../blueprints/automation/time-capsule/monitoring.yaml).
   Create an automation from it, select the helper, and choose the sensor
   inputs you want to use. Select any matching sensors on your device page.
   When available, **Backup Volume Mounted** and the SMB sensors can be used
   under **Connection and mount sensors**.
3. Keep the default Home Assistant notification or set your own notification
   action. Use `{{ message }}` in the message field to include the fault or
   recovery details.

SMART failure and rising sector counts are checked each minute. Connection
outages, high temperatures, and low space use the configured delays. If you
enable temperature alerts, set the limit in the sensor's displayed unit and
use the drive's limits and your normal backup readings as a guide. The
[original YAML example](monitoring.yaml) is available for existing
setups. Disable any overlapping alerts when switching to the blueprint.

### Update existing alerts

1. If your version of the integration creates a Repair for a retired sensor,
   find the replacement in **Settings → System → Repairs**.
2. Open the affected automation in **Settings → Automations & scenes**. In
   the alert blueprint, replace **SSH Connected** with **Backup Volume Mounted**
   under **Connection and mount sensors**. Replace a retired temperature
   reading with **Temperature** and review the threshold in the displayed
   unit. Save the automation after updating the selections.

To update an imported blueprint, go to **Settings → Automations & scenes →
Blueprints**, find **Time Capsule health and backup alerts**, and choose
**⋮ → Re-import blueprint**. Your existing automations use the updated blueprint
and keep their sensor selections. If a selected sensor is missing, Home Assistant
shows a **Time Capsule alert needs attention** notification listing its entity ID.
The notification clears after you replace the sensor or clear that selection.

[Home Assistant blueprint update instructions](https://www.home-assistant.io/docs/automation/using_blueprints/#re-importing-a-blueprint)

## Monitor completed Time Machine backups

Set up one webhook and reporter per Mac and backup destination.

1. Copy the [Time Machine package](time_machine_backup.yaml) into your
   [Home Assistant packages folder](https://www.home-assistant.io/docs/configuration/packages/).
   Add a long random `time_machine_mac_webhook` value to `secrets.yaml` and
   restart Home Assistant. For each additional Mac or destination, copy the
   package and use distinct values for the webhook secret, sensor names,
   unique IDs, and example automation ID and name. Replace the last-backup
   and backup-age entity IDs wherever the new package refers to them.
2. On the Mac, run `tmutil destinationinfo` to identify the intended Time
   Capsule destination, then check its mounted volume in Finder or with
   `mount`. Run the [reporter](report-time-machine-backup.sh) in
   Terminal with Full Disk Access. Use the actual mount path and your Home
   Assistant address. If `tmutil` requires root, run it with `sudo` from the
   same Terminal.

   ```sh
   bash report-time-machine-backup.sh "/Volumes/Your Backup Volume" \
       "https://home-assistant.example/api/webhook/YOUR_WEBHOOK_ID"
   ```

3. After a successful backup, compare **Mac Last Time Capsule Backup** with
   Time Machine. Run the same reporter when the volume is unmounted to confirm
   that **Mac Time Capsule Reporter Status** changes to
   `destination_unavailable` and the last completed backup stays recorded.
   A mounted volume that `tmutil` can't read reports `read_error`.
4. To get alerts, select the Mac's backup age, last report, and reporter status
   in a blueprint automation. Disable the package's example overdue automation
   if the blueprint also monitors backup age. Set the report timeout based on
   how often you run the reporter and how long the Mac normally sleeps. The reporter
   sends its status and, when readable, the last completed backup timestamp
   to a local-only webhook.
