# Time Capsule Sensors

A Home Assistant integration for modded Apple Time Capsules that expose
`envstat` and SMART data over SSH. It polls once a minute and creates:

- Temperature sensors for every `degC` reading `envstat` reports (internal,
  external 1-3), skipping `T_hdd`, which this hardware never populates.
- A fan speed sensor (RPM).
- A sensor for every row of `atactl wd0 smart status` (raw value, with the
  normalized value/threshold/criticality as attributes).
- An overall SMART Status sensor (`Passed` / `Failed` / `Disabled` /
  `Not Supported`), computed the same way `smartctl`'s health verdict is:
  an attribute has failed once its normalized value drops to or below its
  threshold on a row marked critical.

It talks to the Time Capsule's old NetBSD-based `sshd`, which only offers a
`diffie-hellman-group14-sha1` key exchange and signs its host key with plain
`ssh-rsa` -- both are handled automatically, no manual SSH config needed.

## Prerequisites

### 1. Enable SSH on the Time Capsule

Stock Time Capsule firmware doesn't allow SSH logins. Use
[TimeCapsuleSMB](https://github.com/jamesyc/TimeCapsuleSMB) first -- besides
installing modern Samba, its setup step enables SSH on the device for you
(it needs your Time Capsule's admin password to do that). Run its
`configure` step (or the equivalent step in its macOS GUI app) and confirm
SSH comes up before moving on.

### 2. Give the Time Capsule a static IP

This integration is configured with a plain IP address, not a hostname, so
if the Time Capsule's address changes (e.g. after a DHCP lease renewal),
you'll need to update the integration. Avoid that by reserving a fixed IP
for it in your router (DHCP reservation, sometimes called a "static lease"
or "address reservation" -- look up your router's admin UI for the exact
term), keyed to the Time Capsule's MAC address.

### 3. Know your Time Capsule's root/admin password

The integration authenticates over SSH with a username (`root` by default)
and password -- the same one used above to enable SSH.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mdevils&repository=hass-time-capsule&category=integration)

Or manually: HACS -> the "..." menu (top right) -> **Custom repositories**
-> add `https://github.com/mdevils/hass-time-capsule` as an **Integration**
-> find "Time Capsule Sensors" in HACS and install it.

Restart Home Assistant after installing.

### Manual

Copy `custom_components/time_capsule` into your Home Assistant `config/custom_components/` directory, then restart Home Assistant.

## Configuration

Configuration is done entirely from the UI -- there's no YAML.

1. **Settings -> Devices & Services -> Add Integration -> Time Capsule Sensors.**
2. Enter:
   - **Host**: the Time Capsule's (static) IP address.
   - **Username**: `root` by default.
   - **Port**: `22` by default.
   - **Password**: the admin/root password from the prerequisites above.
3. The integration connects and runs `envstat` + `smart status` before
   saving, so a wrong host/password shows an inline error immediately
   instead of failing silently later.

To change any of these later, open the integration's entry and click
**Configure** -- no need to remove and re-add it.

The poll interval is fixed at 60 seconds and isn't currently exposed in the
UI.
