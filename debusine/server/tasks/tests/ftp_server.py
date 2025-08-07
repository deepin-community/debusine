# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
A fake FTP server for testing.

Do not use this in production!  It has only the features required for the
tests that currently use it, and no security.

Parts of this file have no test coverage due to difficulties getting
coverage.py to handle multiprocessing while also handling Django channels
elsewhere.
"""

from multiprocessing import Process
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer


class FakeFTPServerProcess(Process):
    """Process that runs a fake FTP server for testing."""

    def __init__(self, username: str, home_directory: Path) -> None:
        """Initialize the server and start listening."""
        super().__init__()
        self.name = "FakeFTPServer"
        authorizer = DummyAuthorizer()
        authorizer.add_user(username, "", str(home_directory), perm="ew")
        handler = FTPHandler
        handler.authorizer = authorizer
        self.server = FTPServer(("", 0), handler)

    @property
    def address(self) -> tuple[str, int]:
        """Return the (host, port) to be used by clients."""
        host, port = self.server.socket.getsockname()
        assert isinstance(host, str)
        assert isinstance(port, int)
        return host, port

    def run(self) -> None:  # pragma: no cover
        """Run the server."""
        self.server.serve_forever()

    def stop(self) -> None:
        """Stop the server."""
        self.terminate()
        self.join()
