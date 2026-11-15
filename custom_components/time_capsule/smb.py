"""Optional read-only checks from Home Assistant to the Time Capsule's SMB service."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from uuid import uuid4

from smbprotocol.connection import Connection
from smbprotocol.exceptions import SMBException
from smbprotocol.open import (
    CreateDisposition,
    CreateOptions,
    DirectoryAccessMask,
    ImpersonationLevel,
    Open,
    ShareAccess,
)
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect
from spnego.exceptions import SpnegoError

SMB_PORT = 445
SMB_TIMEOUT = 5


@dataclass(frozen=True)
class SmbStatus:
    port_open: bool
    share_accessible: bool | None = None


class _ProbeConnection(Connection):
    """Apply a timeout to session and tree requests as well as negotiation."""

    def receive(self, request, wait=True, timeout=None, resolve_symlinks=True):
        return super().receive(
            request, wait, SMB_TIMEOUT if timeout is None else timeout, resolve_symlinks
        )


def probe_smb(
    host: str, share: str = "", username: str = "", password: str = ""
) -> SmbStatus:
    """Check TCP port 445, then optionally open the share root for reading."""
    try:
        with socket.create_connection((host, SMB_PORT), timeout=SMB_TIMEOUT):
            pass
    except OSError:
        return SmbStatus(False, False if share else None)

    if not share:
        return SmbStatus(True)

    connection = _ProbeConnection(uuid4(), host, SMB_PORT, require_signing=True)
    try:
        connection.connect(timeout=SMB_TIMEOUT)
        session = Session(
            connection, username, password, require_encryption=False, auth_protocol="ntlm"
        )
        session.connect()
        tree = TreeConnect(session, "\\\\" + host + "\\" + share)
        tree.connect()
        directory = Open(tree, "")
        directory.create(
            ImpersonationLevel.Impersonation,
            DirectoryAccessMask.FILE_READ_ATTRIBUTES,
            0,
            ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
            CreateDisposition.FILE_OPEN,
            CreateOptions.FILE_DIRECTORY_FILE,
        )
        directory.close()
    except (OSError, SMBException, SpnegoError):
        return SmbStatus(True, False)
    finally:
        if connection.transport is not None:
            connection.disconnect(close=False)

    return SmbStatus(True, True)
