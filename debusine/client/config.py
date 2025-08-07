# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Config reader for the debusine clients."""

import os
import sys
from collections.abc import MutableMapping
from configparser import ConfigParser
from pathlib import Path
from typing import NoReturn, TextIO
from urllib.parse import urlparse


class ConfigHandler(ConfigParser):
    """
    ConfigHandler for the configuration (.ini file) of the Debusine client.

    If the configuration file is not valid it writes an error message
    to stderr and aborts.
    """

    DEFAULT_CONFIG_FILE_PATH = Path.home() / Path(
        '.config/debusine/client/config.ini'
    )

    def __init__(
        self,
        *,
        server_name: str | None = None,
        config_file_path: str | os.PathLike[str] = DEFAULT_CONFIG_FILE_PATH,
        stdout: TextIO = sys.stdout,
        stderr: TextIO = sys.stderr,
    ) -> None:
        """
        Initialize variables and reads the configuration file.

        :param server_name: look up configuration matching this server name
          or FQDN/scope (or None for the default server from the
          configuration)
        :param config_file_path: location of the configuration file
        """
        super().__init__()

        self._server_name = server_name
        self._config_file_path = config_file_path

        self._stdout = stdout
        self._stderr = stderr

        if not self.read(self._config_file_path):
            self._fail(f'Cannot read {self._config_file_path} .')

    def server_configuration(self) -> MutableMapping[str, str]:
        """
        Return the server configuration.

        Uses the server specified in the __init__() or the default server
        in the configuration file.
        """
        if self._server_name is None:
            server_name = self._default_server_name()
        else:
            server_name = self._server_name

        return self._server_configuration(server_name)

    def _fail(self, message: str) -> NoReturn:
        """Write message to self._stderr and aborts with exit code 3."""
        self._stderr.write(message + "\n")
        raise SystemExit(3)

    def _default_server_name(self) -> str:
        """Return default server name or aborts."""
        if 'General' not in self:
            self._fail(
                f'[General] section and default-server key must exist '
                f'in {self._config_file_path} to use the default server. '
                f'Add them or specify the server in the command line.'
            )

        if 'default-server' not in self['General']:
            self._fail(
                f'default-server key must exist in [General] in '
                f'{self._config_file_path} . Add it or specify the server '
                f'in the command line.'
            )

        return self['General']['default-server']

    def _server_configuration(
        self, server_name: str
    ) -> MutableMapping[str, str]:
        """Return configuration for server_name or aborts."""
        section_name: str | None
        if "/" in server_name:
            # Look up the section by FQDN and scope.
            server_fqdn, scope_name = server_name.split("/")
            for section_name in self.sections():
                if (
                    section_name.startswith("server:")
                    and (
                        (api_url := self[section_name].get("api-url"))
                        is not None
                    )
                    and urlparse(api_url).hostname == server_fqdn
                    and self[section_name].get("scope") == scope_name
                ):
                    break
            else:
                section_name = None
        else:
            # Look up the section by name.
            section_name = f'server:{server_name}'

        if section_name is None or section_name not in self:
            raise ValueError(
                f"No Debusine client configuration for {server_name!r}; "
                f"run 'debusine setup' to configure it"
            )
        server_configuration = self[section_name]

        self._ensure_server_configuration(server_configuration, section_name)

        return server_configuration

    def _ensure_server_configuration(
        self,
        server_configuration: MutableMapping[str, str],
        server_section_name: str,
    ) -> None:
        """Check keys for server_section_name. Aborts if there are errors."""
        keys = ('api-url', 'scope', 'token')

        missing_keys = keys - server_configuration.keys()

        if len(missing_keys) > 0:
            self._fail(
                f'Missing required keys in the section '
                f'[{server_section_name}]: {", ".join(sorted(missing_keys))} '
                f'in {self._config_file_path} .'
            )

        extra_keys = server_configuration.keys() - keys

        if len(extra_keys) > 0:
            self._fail(
                f'Invalid keys in the section '
                f'[{server_section_name}]: {", ".join(sorted(extra_keys))} '
                f'in {self._config_file_path} .'
            )
