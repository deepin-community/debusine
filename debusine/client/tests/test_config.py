# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test for the Config class."""

import io
from configparser import ConfigParser
from pathlib import Path
from typing import Any

from debusine.client.config import ConfigHandler
from debusine.test import TestCase


class ConfigHandlerTests(TestCase):
    """Tests for ConfigHandler class."""

    def setUp(self) -> None:
        """Set up ConfigHandlerTests."""
        self.stderr = io.StringIO()

    @staticmethod
    def valid_configuration() -> ConfigParser:
        """Return a valid configuration."""
        configuration = ConfigParser()
        configuration['General'] = {'default-server': 'debian'}
        configuration['server:debian'] = {
            'api-url': 'https://debusine.debian.org/api',
            'scope': 'debian',
            'token': 'the-token-for-debian',
        }
        configuration['server:kali'] = {
            'api-url': 'https://debusine.kali.org/api',
            'scope': 'kali',
            'token': 'the-token-for-kali',
        }

        return configuration

    def write_configuration(self, configuration: ConfigParser) -> Path:
        """Return filename with the configuration."""
        temp_file = self.create_temporary_file(suffix=".ini")

        with open(temp_file, "w") as fp:
            configuration.write(fp)

        return temp_file

    def build_config_handler(
        self, config_parser: ConfigParser, **config_kwargs: Any
    ) -> ConfigHandler:
        """Return ConfigHandler for config_parser."""
        temporary_config = self.write_configuration(config_parser)

        return ConfigHandler(
            config_file_path=temporary_config,
            stderr=self.stderr,
            **config_kwargs,
        )

    def test_default_server_name(self) -> None:
        """ConfigHandler._default_server_name() returns expected name."""
        config = self.build_config_handler(self.valid_configuration())
        self.assertEqual(
            config._default_server_name(), config["General"]["default-server"]
        )

    def test_default_server_name_missing_general(self) -> None:
        """ConfigHandler._default_server_name() cannot read [General]."""
        config = self.build_config_handler(ConfigParser())

        with self.assertRaisesSystemExit(3):
            config._default_server_name()

        self.assertEqual(
            self.stderr.getvalue(),
            f'[General] section and default-server key must exist in '
            f'{config._config_file_path} to use the default server. Add '
            f'them or specify the server in the command line.\n',
        )

    def test_default_server_name_missing_key(self) -> None:
        """ConfigHandler._default_server_name() default-server key missing."""
        config_parser = ConfigParser()
        config_parser.add_section('General')

        config = self.build_config_handler(config_parser)

        with self.assertRaisesSystemExit(3):
            config._default_server_name()

        self.assertEqual(
            self.stderr.getvalue(),
            f'default-server key must exist in [General] in '
            f'{config._config_file_path} . Add it or specify the server'
            f' in the command line.\n',
        )

    def test_server(self) -> None:
        """ConfigHandler._server_configuration(server) returns the config."""
        config = self.build_config_handler(self.valid_configuration())

        server_name = "kali"

        server_config = self.valid_configuration()[f"server:{server_name}"]

        self.assertEqual(
            config._server_configuration(server_name),
            {
                'api-url': server_config["api-url"],
                'scope': server_config["scope"],
                'token': server_config["token"],
            },
        )

    def test_server_configuration_default(self) -> None:
        """ConfigHandler.server_configuration() uses the default server."""
        configuration = self.valid_configuration()

        config_handler = self.build_config_handler(
            configuration, server_name=None
        )

        default_server = configuration[
            f"server:{configuration['General']['default-server']}"
        ]

        self.assertEqual(
            config_handler.server_configuration(),
            {
                'api-url': default_server["api-url"],
                'scope': default_server["scope"],
                'token': default_server["token"],
            },
        )

    def test_server_configuration_kali(self) -> None:
        """ConfigHandler.server_configuration() return Kali."""
        config = self.valid_configuration()
        server_name = "kali"

        config_handler = self.build_config_handler(
            config, server_name=server_name
        )

        config_server = config[f"server:{server_name}"]

        self.assertEqual(
            config_handler.server_configuration(),
            {
                'api-url': config_server["api-url"],
                'scope': config_server["scope"],
                'token': config_server["token"],
            },
        )

    def test_server_non_existing_error(self) -> None:
        """ConfigHandler._server_configuration('does-not-exist') aborts."""
        config = self.build_config_handler(self.valid_configuration())

        with self.assertRaisesSystemExit(3):
            config._server_configuration('does-not-exist')

        self.assertEqual(
            self.stderr.getvalue(),
            f'[server:does-not-exist] section not found '
            f'in {config._config_file_path} .\n',
        )

    def test_server_incomplete_configuration_error(self) -> None:
        """ConfigHandler._server_configuration('incomplete-server') aborts."""
        config_parser = ConfigParser()
        config_parser.add_section('server:incomplete-server')

        config = self.build_config_handler(config_parser)

        with self.assertRaisesSystemExit(3):
            config._server_configuration('incomplete-server')

        self.assertEqual(
            self.stderr.getvalue(),
            'Missing required keys in the section '
            '[server:incomplete-server]: api-url, scope, token in '
            f'{config._config_file_path} .\n',
        )

    def test_server_extra_configuration_error(self) -> None:
        """ConfigHandler._server_configuration('too-many-keys') aborts."""
        config_parser = self.valid_configuration()
        server_name = "debian"
        server_section = f"server:{server_name}"

        config_parser[server_section]['port'] = '80'
        config_parser[server_section]['priority'] = 'high'

        config = self.build_config_handler(config_parser)

        with self.assertRaisesSystemExit(3):
            config._server_configuration(server_name)

        self.assertEqual(
            self.stderr.getvalue(),
            'Invalid keys in the section '
            f'[{server_section}]: port, priority in '
            f'{config._config_file_path} .\n',
        )

    def test_config_file_cannot_be_opened(self) -> None:
        """ConfigHandler.__init__() cannot open the file: aborts."""
        with self.assertRaisesSystemExit(3):
            ConfigHandler(
                config_file_path='/tmp/does_not_exist101001.ini',
                stderr=self.stderr,
            )
