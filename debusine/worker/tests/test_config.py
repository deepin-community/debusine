# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test for the Config class."""

import contextlib
import os
import tempfile
from collections.abc import Generator
from configparser import ConfigParser
from pathlib import Path
from unittest import mock

from debusine.test import TestCase
from debusine.worker.config import ConfigHandler


class ConfigHandlerTests(TestCase):
    """Tests for ConfigHandler class."""

    @classmethod
    @contextlib.contextmanager
    def _temporary_config_directory(cls) -> Generator[str, None, None]:
        """Create a temporary directory with a config.ini inside."""
        with tempfile.TemporaryDirectory(
            prefix='debusine-Worker-ConfigHandlerTests-'
        ) as temp_directory:
            cls.create_configuration(temp_directory)
            yield temp_directory

    def test_get_section_key_missing_section(self) -> None:
        """
        ConfigHandler._get_section_key with a non-existing section aborts.

        ConfigHandler writes a message to the logs and raises SystemExit.
        """
        with self.configuration_temp_directory({}) as config_dir:
            config_handler = ConfigHandler(directories=[config_dir])

            log_message = (
                'Missing required section "non-existing-section" in '
                f'the configuration file ({config_dir}/config.ini)'
            )

            with (
                self.assertLogsContains(log_message),
                self.assertRaisesSystemExit(3),
            ):
                config_handler._get_section_key('non-existing-section', 'key')

    def test_get_section_key_missing_key(self) -> None:
        """
        ConfigHandler._get_section_key with a non-existing key aborts.

        ConfigHandler writes a message to the logs and raises SystemExit.
        """
        with self.configuration_temp_directory({}) as config_dir:
            config_handler = ConfigHandler(directories=[config_dir])

            log_message = (
                'Missing required key "api-url" in General section in '
                f'the configuration file ({config_dir}/config.ini)'
            )

            with (
                self.assertLogsContains(log_message),
                self.assertRaisesSystemExit(3),
            ):
                config_handler._get_section_key('General', 'non-existing-key')

    def test_get_section_key_section_missing_not_required(self) -> None:
        """ConfigHandler._get_section_key, non-existing section return None."""
        with self.configuration_temp_directory({}) as config_dir:
            config_handler = ConfigHandler(directories=[config_dir])

            value = config_handler._get_section_key(
                'non-existing-section', 'key', required=False
            )

            self.assertIsNone(value)

    def test_get_section_key_key_missing_not_required(self) -> None:
        """ConfigHandler._get_section_key, non-existing key return None."""
        with self.configuration_temp_directory({}) as config_dir:
            config_handler = ConfigHandler(directories=[config_dir])

            value = config_handler._get_section_key(
                'default', 'non-existing-key', required=False
            )

            self.assertIsNone(value)

    def test_choose_from_force_directory_succeeds(self) -> None:
        """ConfigHandler._choose_directory returns the passed directory."""
        with self._temporary_config_directory() as temp_directory:
            config = ConfigHandler(directories=[temp_directory])

            selected_directory = config._choose_directory(
                directories=[temp_directory]
            )

            self.assertEqual(temp_directory, selected_directory)

    def test_choose_from_force_directory_fails(self) -> None:
        """
        ConfigHandler._choose_directory aborts.

        The force_directory does not exist.
        """
        directory = '/this/directory/does/not/exist/024024024'
        log_message = (
            f"Configuration directory cannot be found in: ['{directory}']"
        )

        with (
            self.assertLogsContains(log_message),
            self.assertRaisesSystemExit(3),
        ):
            ConfigHandler._choose_directory(directories=[directory])

    def test_choose_from_default_directories_fails(self) -> None:
        """
        ConfigHandler._choose_directory from the default directories aborts.

        Default directories do not exist.
        """
        directory = '/this/directory/no/exist'
        log_message = (
            f"Configuration directory cannot be found in: ['{directory}']"
        )

        with mock.patch.object(
            ConfigHandler, 'default_directories', new=[directory]
        ):
            with (
                self.assertLogsContains(log_message),
                self.assertRaisesSystemExit(3),
            ):
                ConfigHandler()

    def test_choose_from_default_directories_succeeds(self) -> None:
        """
        ConfigHandler._choose_directory from the default directories works.

        Returns the directory that exist.
        """
        with self._temporary_config_directory() as temp_directory:
            default_directories = [
                '/this/directory/does/not/exist/242',
                temp_directory,
            ]
            with mock.patch.object(
                ConfigHandler, 'default_directories', new=default_directories
            ):
                config = ConfigHandler()

            self.assertEqual(temp_directory, config._configuration_directory)

    @classmethod
    @contextlib.contextmanager
    def _temporary_directory_with_token_file(
        cls, token: str, token_filename: str = "token"
    ) -> Generator[str, None, None]:
        with cls._temporary_config_directory() as temp_directory:
            token_path = os.path.join(temp_directory, token_filename)
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT, mode=0o600)
            os.write(fd, token.encode('utf-8'))
            os.close(fd)

            yield temp_directory

    def test_token_succeeds(self) -> None:
        """ConfigHandler.token returns the token."""
        token = 'a84b92e3c3379733bf0d2b25700c4c'

        for prop, token_filename in (
            ("token", "token"),
            ("activation_token", "activation-token"),
        ):
            with (
                self.subTest(prop=prop),
                self._temporary_directory_with_token_file(
                    token, token_filename=token_filename
                ) as temp_directory,
            ):
                config = ConfigHandler(directories=[temp_directory])

                self.assertEqual(getattr(config, prop), token)

    def test_token_strip_new_line(self) -> None:
        """
        ConfigHandler.token returns the token without a new line.

        If the token file had a newline after the token: it's stripped.
        """
        for prop, token_filename in (
            ("token", "token"),
            ("activation_token", "activation-token"),
        ):
            with (
                self.subTest(prop=prop),
                self._temporary_config_directory() as temp_directory,
            ):
                token = 'a84b92e3c3379733bf0d2b25700c4c'
                with open(
                    os.path.join(temp_directory, token_filename), 'w'
                ) as token_file:
                    token_file.write(token + '\n')

                    Path(token_file.name).chmod(0o600)

                    config = ConfigHandler(directories=[temp_directory])

                self.assertEqual(getattr(config, prop), token)

    def test_token_file_does_not_exist(self) -> None:
        """ConfigHandler.token returns None for non-existing file."""
        for prop in ("token", "activation_token"):
            with (
                self.subTest(prop=prop),
                self._temporary_config_directory() as temp_directory,
            ):
                config = ConfigHandler(directories=[temp_directory])
                # The property accesses the token file.
                self.assertIsNone(getattr(config, prop))

    def test_token_file_cannot_be_read(self) -> None:
        """Token file cannot be read (not enough permissions)."""
        for prop, token_filename in (
            ("token", "token"),
            ("activation_token", "activation-token"),
        ):
            with (
                self.subTest(prop=prop),
                self._temporary_config_directory() as temp_directory,
            ):
                token_file = os.path.join(temp_directory, token_filename)
                os.mkdir(token_file)
                Path(token_file).chmod(0o000)

                config = ConfigHandler(directories=[temp_directory])

                with (
                    self.assertLogsContains('Cannot read'),
                    self.assertRaisesSystemExit(3),
                ):
                    getattr(config, prop)

    def test_token_too_open_fails(self) -> None:
        """ConfigHandler.token aborts: too open permissions."""
        for prop, token_filename in (
            ("token", "token"),
            ("activation_token", "activation-token"),
        ):
            with (
                self.subTest(prop=prop),
                self._temporary_config_directory() as temp_directory,
            ):
                token = 'a84b92e3c3379733bf0d2b25700c4c'
                with open(
                    os.path.join(temp_directory, token_filename), 'w'
                ) as token_file:
                    token_file.write(token)

                    Path(token_file.name).chmod(0o640)

                config = ConfigHandler(directories=[temp_directory])

                log_message = (
                    f'Permission too open for {token_file.name}. '
                    'Make sure that the file is not accessible '
                    'by group or others'
                )

                with (
                    self.assertLogsContains(log_message),
                    self.assertRaisesSystemExit(3),
                ):
                    getattr(config, prop)

    def test_token_is_cached(self) -> None:
        """ConfigHandler.token caches the result."""
        token = 'a84b92e3c3379733bf0d2b25700c4c'

        with self._temporary_directory_with_token_file(token) as temp_directory:
            config = ConfigHandler(directories=[temp_directory])
            self.assertEqual(config.token, token)

            with open(os.path.join(temp_directory, 'token'), 'w') as token_file:
                token_file.write('new-token\n')
                Path(token_file.name).chmod(0o600)

            # config.token returns the first token because it is cached
            # (and it was not written via ConfigHandler.write_token which
            # clears the cache).
            self.assertEqual(config.token, token)

    def test_write_token_clean_cache(self) -> None:
        """ConfigHandler.write_token clears ConfigHandler.token cache."""
        token = 'a84b92e3c3379733bf0d2b25700c4c'

        with self._temporary_directory_with_token_file(token) as temp_directory:
            config = ConfigHandler(directories=[temp_directory])
            self.assertEqual(config.token, token)

            config.write_token('new-token')
            # New token is returned
            self.assertEqual(config.token, 'new-token')

    def test_write_token_succeeds(self) -> None:
        """
        ConfigHandler.write_token writes the token.

        Asserts that it overwrites the existing token.
        """
        old_token = 'a84b92e3c3379733bf0d2b25700c4c'

        with self._temporary_directory_with_token_file(
            old_token
        ) as temp_directory:
            config = ConfigHandler(directories=[temp_directory])

            new_token = 'new-token'

            # In order to assert that Config.write_token truncates the file
            # the new_token needs to be shorter than the old_token
            self.assertLess(len(new_token), len(old_token))

            # write_token must overwrite the existing token
            config.write_token(new_token)

            token_file = os.path.join(temp_directory, 'token')

            with open(token_file) as file:
                # Token has been written
                self.assertEqual(file.read(), new_token)

            self.assertEqual(config.token, new_token)

            config._file_permissions_are_restricted_or_fail(token_file)

    def test_write_token_failure(self) -> None:
        """ConfigHandler.write_token cannot write to the token file."""
        with self._temporary_config_directory() as temp_directory:
            config = ConfigHandler(directories=[temp_directory])

        token_file = os.path.join(temp_directory, 'token')

        log_message = (
            f"Cannot open token file: [Errno 2] "
            f"No such file or directory: '{token_file}'"
        )
        with (
            self.assertLogsContains(log_message),
            self.assertRaisesSystemExit(3),
        ):
            # it fails because temp_directory has already been deleted
            config.write_token('some-random-token')

    def _mock_config_handle_property(
        self, property_to_mock: str
    ) -> mock.PropertyMock:
        """Return a PropertyMock attached to ConfigHandler class."""
        # Attach mock to the property as described in https://docs.python.org/3/library/unittest.mock.html#unittest.mock.PropertyMock  # noqa: E501
        old_property = getattr(ConfigHandler, property_to_mock)
        mocked_property = mock.PropertyMock()
        setattr(ConfigHandler, property_to_mock, mocked_property)
        self.addCleanup(setattr, ConfigHandler, property_to_mock, old_property)

        return mocked_property

    def test_validate_config_or_fail(self) -> None:
        """
        validate_config_or_fail() accesses Config properties.

        There are separate tests for the behaviour accessing the properties
        (to call Config._fail). Not re-implemented in this test to avoid
        repetition.
        """
        with self._temporary_config_directory() as temp_directory:
            config = ConfigHandler(directories=[temp_directory])

            mocked_token = self._mock_config_handle_property('token')
            mocked_activation_token = self._mock_config_handle_property(
                'activation_token'
            )
            debusine_url = self._mock_config_handle_property('api_url')

            config.validate_config_or_fail()

            mocked_token.assert_called_once_with()
            mocked_activation_token.assert_called_once_with()
            debusine_url.assert_called_once_with()

    def test_etc_config_lower_priority(self) -> None:
        """/etc/debusine/worker is the last directory to use for config."""
        self.assertEqual(
            ConfigHandler.default_directories[-1], '/etc/debusine/worker'
        )

    def test_config_ini_cannot_be_read(self) -> None:
        """File config.ini in configuration directory cannot be read: fails."""
        with self._temporary_config_directory() as temp_directory:
            config_ini = Path(temp_directory) / 'config.ini'
            os.unlink(config_ini)
            log_message = f'Cannot read {config_ini}'

            with (
                self.assertLogsContains(log_message),
                self.assertRaisesSystemExit(3),
            ):
                ConfigHandler(directories=[temp_directory])

    @staticmethod
    def create_configuration(
        directory: str, configuration: str = '[General]\n'
    ) -> None:
        """
        Create a file named config.ini in directory with configuration_str.

        :param directory: directory to write the config.ini into. The directory
          must exist beforehand.
        :param configuration: contents of config.ini.
        """
        with open(os.path.join(directory, 'config.ini'), 'w') as config_file:
            config_file.write(configuration)

    @classmethod
    @contextlib.contextmanager
    def configuration_temp_directory(
        cls, configuration: dict[str, str]
    ) -> Generator[str, None, None]:
        """
        Create a context to create a configuration file in a new directory.

        Yield the temporary directory name.

        :param configuration: configuration to write inside the section
          'General'.
        """
        with cls._temporary_config_directory() as temp_directory:
            config_file_name = os.path.join(temp_directory, 'config.ini')
            with open(config_file_name, 'w') as config_file:
                config_writer = ConfigParser()
                config_writer['General'] = configuration

                config_writer.write(config_file)

            yield temp_directory
