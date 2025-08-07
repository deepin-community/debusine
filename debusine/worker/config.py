# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Config reader for the debusine workers."""

import logging
import os
from collections.abc import Sequence
from configparser import ConfigParser, NoOptionError, NoSectionError
from pathlib import Path
from typing import Literal, NoReturn, overload
from urllib.parse import urlparse


class ConfigHandler(ConfigParser):
    """Handle debusine worker configuration file (.ini format) and tokens."""

    default_directories = [
        str(Path.home() / '.config/debusine/worker'),
        '/etc/debusine/worker',
    ]

    def __init__(
        self,
        *,
        directories: Sequence[str] | None = None,
        require_https: bool = False,
    ) -> None:
        """
        Initialize variables and reads the configuration file.

        :param directories: if None the default directories are used
          (/etc/debusine/worker and ~/.config/debusine/worker)
        :param require_https: if True, require the configured api-url to use
          HTTPS
        """
        super().__init__()

        self._token: str | None = None
        self._api_url: str | None = None

        if directories is None:
            directories = self.default_directories

        self._configuration_directory = self._choose_directory(directories)
        self._require_https = require_https

        self.active_configuration_file = os.path.join(
            self._configuration_directory, 'config.ini'
        )

        self._read_file_or_fail(self.active_configuration_file)

        self._token_file = os.path.join(self._configuration_directory, 'token')
        self._activation_token_file = os.path.join(
            self._configuration_directory, 'activation-token'
        )

    def _read_file_or_fail(self, config_filename: str) -> None:
        """Read config_filename or fails."""
        files_read = self.read(config_filename)

        if len(files_read) != 1:
            self._fail(f'Cannot read {config_filename}')

    @staticmethod
    def _fail(message: str) -> NoReturn:
        """
        Print message and fail (exits) the execution of the Worker.

        Use only for non-recoverable errors.

        :param message: message to be logged in fatal
        :raises SystemExit(3): to finish the execution of the worker
        """
        logging.fatal(message)
        raise SystemExit(3)

    @classmethod
    def _choose_directory(cls, directories: Sequence[str]) -> str:
        for possible_directory in directories:
            if os.path.isdir(possible_directory):
                return possible_directory

        cls._fail(f'Configuration directory cannot be found in: {directories}')

    @property
    def token(self) -> str | None:
        """
        Return cached token or read it from the file.

        When accessing the file checks that the permissions are correct.

        :raises SystemExit: if the permissions are too open or the file is not
          accessible.
        """
        if self._token is not None:
            return self._token

        self._file_permissions_are_restricted_or_fail(self._token_file)

        try:
            with open(self._token_file) as f:
                self._token = f.read().rstrip('\n')
        except FileNotFoundError:
            pass
        except OSError as exc:
            self._fail(f'Cannot read {self._token_file}: {exc}')

        return self._token

    @property
    def activation_token(self) -> str | None:
        """
        Return activation token or read it from the file.

        When accessing the file checks that the permissions are correct.

        The activation token is deliberately uncached, since it is only used
        once during worker registration.

        :raises SystemExit: if the permissions are too open or the file is not
          accessible.
        """
        self._file_permissions_are_restricted_or_fail(
            self._activation_token_file
        )

        try:
            with open(self._activation_token_file) as f:
                return f.read().rstrip('\n')
        except FileNotFoundError:
            return None
        except OSError as exc:
            self._fail(f'Cannot read {self._activation_token_file}: {exc}')

    @property
    def api_url(self) -> str:
        """
        Return the value of api-url key in [General]. Caches it.

        :raises SystemExit: (via self._get_section_key) if
          [General] api-url is not in the configuration file, or (via
          self._fail) if this worker type requires HTTPS and the configured
          api-url does not use HTTPS
        """
        if self._api_url is not None:
            return self._api_url

        self._api_url = self._get_section_key('General', 'api-url')

        if self._require_https and urlparse(self._api_url).scheme != "https":
            self._fail(
                f'api-url in {self.active_configuration_file} does not use '
                f'HTTPS: {self._api_url}'
            )

        return self._api_url

    @property
    def log_file(self) -> str | None:
        """Return the log-file to use or None."""
        return self._get_section_key('General', 'log-file', required=False)

    @property
    def log_level(self) -> str | None:
        """Return the log-level or None."""
        return self._get_section_key('General', 'log-level', required=False)

    @overload
    def _get_section_key(
        self, section: str, key: str, *, required: Literal[True] = True
    ) -> str: ...

    @overload
    def _get_section_key(
        self, section: str, key: str, *, required: Literal[False]
    ) -> str | None: ...

    def _get_section_key(
        self, section: str, key: str, *, required: bool = True
    ) -> str | None:
        """
        Return value for the key in section.

        :param section: name of the section.
        :param key: key to retrieve.
        :param required: if it is True, if section or key does not exist
          it logs an error and exits.
        :raises SystemExit: via self._fail() if a required section or key
          is not found in the configuration file.
        """
        try:
            value = self.get(section, key)
        except NoSectionError:
            if required is False:
                return None
            self._fail(
                f'Missing required section "{section}" in the configuration '
                f'file ({self.active_configuration_file})'
            )
        except NoOptionError:
            if required is False:
                return None
            self._fail(
                f'Missing required key "api-url" in General section '
                f'in the configuration file '
                f'({self.active_configuration_file})'
            )

        return value

    def _file_permissions_are_restricted_or_fail(self, file_path: str) -> None:
        try:
            valid = not bool(os.stat(file_path).st_mode & 0o077)
        except FileNotFoundError:
            # File is not found: the file permissions are not too open
            return

        if not valid:
            self._fail(
                f'Permission too open for {file_path}. '
                f'Make sure that the file is not accessible by '
                f'group or others'
            )

    def write_token(self, token: str) -> None:
        """Write token into the token file."""
        try:
            fd = os.open(
                self._token_file,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                mode=0o600,
            )
        except OSError as exc:
            self._fail(f'Cannot open token file: {exc}')

        os.write(fd, token.encode('utf-8'))
        os.close(fd)

        self._token = token

    def validate_config_or_fail(self) -> None:
        """Validate the current configuration."""
        # The properties self.api_url, self.token, and self.activation_token
        # can call self._fail if some section or key does not exist, or if
        # the token file exists but is not accessible (permissions), etc.
        self.api_url
        self.token
        self.activation_token
