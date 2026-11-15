"""SSH plumbing shared by envstat, SMART status, and df, plus the combined fetch.

Everything in here is blocking (paramiko has no asyncio API) and must be run
via `hass.async_add_executor_job`, never awaited directly.
"""

from __future__ import annotations

import base64
import hashlib
import socket
from collections.abc import Callable
from dataclasses import dataclass
from threading import Timer
from typing import TypeVar

import paramiko

from .const import (
    BACKUP_DEVICE,
    BASE_STATION_NAME_COMMAND,
    CONNECT_TIMEOUT,
    DF_COMMAND,
    ENVSTAT_COMMAND,
    HARDWARE_CHECKS,
    MOUNT_COMMAND,
    SMART_COMMAND,
    SSH_HOST_KEY_ALGORITHM,
    SSH_KEX_ALGORITHMS,
)
from .df import DiskUsage, is_volume_mounted, parse_df
from .envstat import EnvstatReading, parse_envstat
from .smart import SmartStatus, parse_smart_status


class TimeCapsuleConnectionError(Exception):
    """Raised when the SSH connection or a remote command fails."""


class TimeCapsuleIdentityError(TimeCapsuleConnectionError):
    """The SSH server's key differs from the saved device key."""


class TimeCapsuleAuthError(TimeCapsuleConnectionError):
    """Raised when the SSH server rejects the username/password."""


@dataclass(frozen=True)
class TimeCapsuleData:
    """Everything fetched from one poll: envstat, SMART status, and disk usage."""

    envstat: dict[str, EnvstatReading]
    smart: SmartStatus
    disk: DiskUsage | None
    fingerprint: str | None
    errors: dict[str, str]
    connected: bool = True
    base_station_name: str | None = None
    volume_mounted: bool | None = None

    @property
    def successful_checks(self) -> set[str]:
        return set(HARDWARE_CHECKS) - self.errors.keys() if self.connected else set()

    @property
    def has_readings(self) -> bool:
        return bool(self.envstat or self.smart.status != "Unknown" or self.disk)


def _connect(
    host: str, port: int, username: str, password: str, fingerprint: str | None
) -> tuple[paramiko.Transport, str]:
    """Open and authenticate an SSH transport to the Time Capsule."""
    try:
        sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    except OSError as err:
        raise TimeCapsuleConnectionError(
            f"Could not reach {host}:{port}: {err}"
        ) from err

    transport = paramiko.Transport(sock)
    try:
        # This device's SSH server only offers a legacy kex algorithm and
        # signs its host key with plain ssh-rsa, neither of which Paramiko
        # offers by default any more. Ask for them explicitly, same as
        # `-o KexAlgorithms=... -o HostKeyAlgorithms=+ssh-rsa` on the CLI.
        options = transport.get_security_options()
        options.kex = SSH_KEX_ALGORITHMS
        options.key_types = tuple(
            dict.fromkeys((*options.key_types, SSH_HOST_KEY_ALGORITHM))
        )
        transport.banner_timeout = CONNECT_TIMEOUT
        transport.auth_timeout = CONNECT_TIMEOUT
        transport.start_client(timeout=CONNECT_TIMEOUT)
        key = transport.get_remote_server_key()
        digest = hashlib.sha256(key.asbytes()).digest()
        actual = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
        if fingerprint is not None and fingerprint != actual:
            transport.close()
            raise TimeCapsuleIdentityError(f"SSH host key changed for {host}")
        transport.auth_password(username, password)
    except paramiko.AuthenticationException as err:
        transport.close()
        raise TimeCapsuleAuthError(f"Authentication to {host} failed: {err}") from err
    except (paramiko.SSHException, OSError, EOFError) as err:
        transport.close()
        raise TimeCapsuleConnectionError(
            f"SSH handshake with {host} failed: {err}"
        ) from err

    return transport, actual


def _run_command(transport: paramiko.Transport, command: str) -> str:
    """Run one command over an already-authenticated transport."""
    try:
        with transport.open_session(timeout=CONNECT_TIMEOUT) as channel:
            channel.settimeout(CONNECT_TIMEOUT)
            if command == SMART_COMMAND:
                # atactl writes its ATA failure verdict and unsupported message to stderr.
                channel.set_combine_stderr(True)
            channel.exec_command(command)
            chunks: list[bytes] = []
            while chunk := channel.recv(4096):
                chunks.append(chunk)
            exit_status = channel.recv_exit_status()
    except (paramiko.SSHException, OSError, EOFError) as err:
        raise TimeCapsuleConnectionError(f"'{command}' failed: {err}") from err

    output = b"".join(chunks).decode("utf-8", errors="replace")
    # atactl can report the drive's verdict before failing to read its table.
    if exit_status != 0 and not (
        command == SMART_COMMAND and parse_smart_status(output).threshold_exceeded
    ):
        raise TimeCapsuleConnectionError(
            f"'{command}' exited with status {exit_status}"
        )

    return output


_Reading = TypeVar("_Reading")


def _read_check(
    transport: paramiko.Transport,
    command: str,
    parse: Callable[[str], _Reading],
    check: str,
    errors: dict[str, str],
) -> _Reading | None:
    try:
        return parse(_run_command(transport, command))
    except TimeCapsuleConnectionError as err:
        errors[check] = str(err)
        return None


def fetch_time_capsule_data(
    host: str,
    port: int,
    username: str,
    password: str,
    fingerprint: str | None = None,
    *,
    include_name: bool = False,
) -> TimeCapsuleData:
    """Read all checks through one SSH transport and retain partial results."""
    transport, actual = _connect(host, port, username, password, fingerprint)
    errors: dict[str, str] = {}
    base_station_name = None
    volume_mounted = None
    # A stuck exec request or missing exit status can outlive a read timeout.
    deadline = Timer(3 * CONNECT_TIMEOUT, transport.close)
    deadline.daemon = True
    deadline.start()
    try:
        readings = _read_check(
            transport, ENVSTAT_COMMAND, parse_envstat, "environment", errors
        )
        smart = _read_check(
            transport, SMART_COMMAND, parse_smart_status, "smart", errors
        )
        disk = _read_check(transport, DF_COMMAND, parse_df, "disk", errors)
        if disk is not None and disk.filesystem == BACKUP_DEVICE:
            volume_mounted = True
        else:
            disk = None
            try:
                volume_mounted = is_volume_mounted(
                    _run_command(transport, MOUNT_COMMAND), BACKUP_DEVICE
                )
            except TimeCapsuleConnectionError:
                pass
        if include_name:
            try:
                name = _run_command(transport, BASE_STATION_NAME_COMMAND).strip()
            except TimeCapsuleConnectionError:
                # This optional metadata must not mark any sensor check as failed.
                pass
            else:
                if len(name.splitlines()) == 1 and "\x00" not in name:
                    base_station_name = name
    finally:
        deadline.cancel()
        transport.close()

    if not readings:
        errors.setdefault("environment", "No temperature or fan readings returned")
    if smart is None:
        smart = SmartStatus(supported=None, enabled=None, attributes=())
    if smart.status == "Unknown":
        errors.setdefault("smart", "Incomplete or unreadable SMART report")
    if disk is None:
        errors.setdefault("disk", "No disk usage reading returned")

    return TimeCapsuleData(
        envstat={reading.key: reading for reading in readings or ()},
        smart=smart,
        disk=disk,
        fingerprint=actual,
        errors=errors,
        base_station_name=base_station_name,
        volume_mounted=volume_mounted,
    )
