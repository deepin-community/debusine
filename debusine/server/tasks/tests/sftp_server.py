# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
A fake SFTP server for testing.

Do not use this in production!  It has only the features required for the
tests that currently use it, and no security.

Parts of this file have no test coverage due to difficulties getting
coverage.py to handle multiprocessing while also handling Django channels
elsewhere.
"""

import io
import os
import socket
import time
from multiprocessing import Process
from typing import NoReturn, Self

from cryptography.hazmat.primitives import asymmetric, serialization
from paramiko import (
    Ed25519Key,
    PKey,
    SFTPAttributes,
    SFTPHandle,
    SFTPServer,
    SFTPServerInterface,
    ServerInterface,
    Transport,
)
from paramiko.common import (
    AUTH_FAILED,
    AUTH_SUCCESSFUL,
    OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED,
    OPEN_SUCCEEDED,
)
from paramiko.sftp import SFTP_FAILURE, SFTP_OK


class FakeSSHServer(ServerInterface):  # pragma: no cover
    """A fake SSH server for testing."""

    def __init__(self, user_key: Ed25519Key) -> None:
        """Construct the server interface with a single permitted user key."""
        self.user_key = user_key

    def check_channel_request(self, kind: str, chanid: int) -> int:
        """Check if a channel request is granted."""
        chanid  # fake usage for vulture
        return (
            OPEN_SUCCEEDED
            if kind in {"session", "subsystem"}
            else OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        )

    def get_allowed_auths(self, username: str) -> str:  # noqa: U100
        """Return supported authentication methods."""
        return "publickey"

    def check_auth_publickey(
        self, username: str, key: PKey  # noqa: U100
    ) -> int:
        """Determine if a client key is acceptable for authentication."""
        return AUTH_SUCCESSFUL if key == self.user_key else AUTH_FAILED


class FakeSFTPServer(SFTPServerInterface):  # pragma: no cover
    """A fake SFTP subsystem for testing."""

    def open(
        self, path: str, flags: int, attr: SFTPAttributes | None
    ) -> SFTPHandle | int:
        """Open a file on the server and create a handle."""
        assert flags & os.O_WRONLY
        assert flags & os.O_CREAT
        assert attr is not None
        SFTPServer.set_file_attr(path, attr)
        f = open(path, "wb")  # will be closed by SFTPHandle.close
        handle = SFTPHandle(flags)
        handle.writefile = f  # type: ignore[attr-defined]
        return handle

    def stat(self, path: str) -> SFTPAttributes | int:
        """Return attributes of a path on the server."""
        try:
            return SFTPAttributes.from_stat(os.stat(path))
        except OSError as e:
            return (
                SFTPServer.convert_errno(e.errno)
                if e.errno is not None
                else SFTP_FAILURE
            )

    def chattr(self, path: str, attr: SFTPAttributes) -> int:
        """Change the attributes of a file."""
        SFTPServer.set_file_attr(path, attr)
        return SFTP_OK


class WrappedEd25519Key:  # pragma: no cover
    """A wrapper for the various formats of Ed25519 key that we need."""

    def __init__(
        self, private_key: asymmetric.ed25519.Ed25519PrivateKey
    ) -> None:
        """Construct a wrapped key object."""
        self.private_key = private_key

    @classmethod
    def generate(cls) -> Self:
        """Generate an Ed25519 private key."""
        return cls(asymmetric.ed25519.Ed25519PrivateKey.generate())

    @property
    def private_openssh(self) -> str:
        """Return the OpenSSH private key representation of this key."""
        return self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    @property
    def public_openssh(self) -> str:
        """Return the OpenSSH public key representation of this key."""
        return (
            self.private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            )
            .decode()
        )

    @property
    def public_paramiko(self) -> Ed25519Key:
        """Return the Paramiko public key representation of this key."""
        return Ed25519Key.from_private_key(io.StringIO(self.private_openssh))


class FakeSFTPServerProcess(Process):
    """Process that runs a fake SFTP server for testing."""

    def __init__(self) -> None:
        """Initialize the server and start listening."""
        super().__init__()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("", 0))
        self.socket.listen()
        self.host_key = WrappedEd25519Key.generate()
        self.user_key = WrappedEd25519Key.generate()

    @property
    def address(self) -> tuple[str, int]:
        """Return the (host, port) to be used by clients."""
        host, port = self.socket.getsockname()
        assert isinstance(host, str)
        assert isinstance(port, int)
        return host, port

    def run(self) -> NoReturn:  # pragma: no cover
        """Run the server."""
        while True:
            conn, _ = self.socket.accept()
            transport = Transport(conn)
            transport.add_server_key(self.host_key.public_paramiko)
            transport.set_subsystem_handler("sftp", SFTPServer, FakeSFTPServer)
            server = FakeSSHServer(self.user_key.public_paramiko)
            transport.start_server(server=server)
            channel = transport.accept()
            if channel is not None:
                try:
                    while transport.is_active():
                        time.sleep(0.1)
                finally:
                    channel.close()

    def stop(self) -> None:
        """Stop the server."""
        self.terminate()
        self.join()
        self.socket.close()
