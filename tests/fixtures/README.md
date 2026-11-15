# Time Capsule command output fixtures

The NetBSD reference fixtures are synthetic examples based on output formats
already documented by the integration. They cover the current internal disk
layout, SMART temperature and sector readings, and environment readings with
and without thresholds. They do not verify any additional hardware model.

## Contributing a device sample

Include the model number from the label on the base station, its firmware
version, and whether the disk is original or a replacement. In Home Assistant,
open the integration's entry menu and select **Download diagnostics** for a
snapshot of the normalized readings. If the integration cannot parse a
reading, capture the output of these read-only commands over SSH as well.

- /usr/sbin/envstat
- /sbin/atactl wd0 smart status
- /bin/df /dev/dk2
- /sbin/mount

Include any errors and whether a USB disk was connected. Remove IP addresses,
MAC addresses, hostnames, share and volume names, usernames, and serial
numbers before sharing. Keep the columns, device paths such as /dev/dk2, and
the output format intact so the sample can be tested.

Open a [repository issue](https://github.com/mdevils/hass-time-capsule/issues)
with the device details and sanitized files.
