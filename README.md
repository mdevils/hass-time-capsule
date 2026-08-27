# Time Capsule Sensors

A Home Assistant integration for modded Apple Time Capsules. It polls the
device once a minute and reports:

- Temperature sensors: internal, and external 1-3.
- Fan speed (RPM).
- A sensor for every SMART attribute the drive reports (raw value, with the
  normalized value/threshold/criticality as attributes) -- reallocated
  sectors, power-on hours, pending sectors, temperature, and so on.
- An overall SMART Status sensor (`Passed` / `Failed` / `Disabled` /
  `Not Supported`).
- Disk usage for the internal drive: total, used, free, and percent used.

It may also work with an AirPort Extreme, since it shares the same internal
tooling, but that hasn't been tested -- if you try it, let me know via an
issue.

## Prerequisites

### 1. Enable SSH on the Time Capsule

Stock Time Capsule firmware doesn't allow SSH logins. Use
[TimeCapsuleSMB](https://github.com/jamesyc/TimeCapsuleSMB) first -- besides
installing modern Samba, its setup step enables SSH on the device for you
(it needs your Time Capsule's admin password to do that). Run its
`configure` step (or the equivalent step in its macOS GUI app) and confirm
SSH comes up before moving on.

### 2. If you'll use an IP address, make it static

The integration accepts either a hostname or an IP address for the Time
Capsule. If you use an IP, reserve it in your router (DHCP reservation,
sometimes called a "static lease" or "address reservation" -- look up your
router's admin UI for the exact term), keyed to the Time Capsule's MAC
address, so it doesn't change on you later. A hostname doesn't need this,
as long as it reliably resolves to the device.

### 3. Know your Time Capsule's password

The SSH password is your Time Capsule's own admin password -- the same one
you used with TimeCapsuleSMB above.

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
   - **Host**: the Time Capsule's hostname or IP address.
   - **Username**: `root` by default.
   - **Port**: `22` by default.
   - **Password**: the Time Capsule's admin password, from the prerequisites above.
3. The integration connects and fetches real data before saving, so a wrong
   host/password shows an inline error immediately instead of failing
   silently later.

To change any of these later, open the integration's entry and click
**Configure** -- no need to remove and re-add it.

The poll interval is fixed at 60 seconds and isn't currently exposed in the
UI.

## How it works

The integration authenticates over SSH and runs three commands: `envstat`,
for the temperature and fan readings; `atactl wd0 smart status`, for the
SMART table; and `df /dev/dk2`, for disk usage. The SMART Status sensor's
pass/fail is computed the same way `smartctl`'s health verdict is: an
attribute has failed once its normalized value drops to or below its
threshold on a row marked critical.

These Time Capsules run an old NetBSD-based `sshd` that only offers a
`diffie-hellman-group14-sha1` key exchange and signs its host key with
plain `ssh-rsa` -- both long dropped as defaults by current OpenSSH and
Paramiko. The integration requests them explicitly, so no manual SSH
configuration is needed on your end.
