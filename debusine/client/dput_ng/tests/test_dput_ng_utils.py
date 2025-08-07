# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for Debusine dput-ng utilities."""

from functools import partial
from textwrap import dedent
from unittest import mock

from debusine.client.config import ConfigHandler
from debusine.client.dput_ng.dput_ng_utils import make_debusine_client
from debusine.test import TestCase


class TestMakeDebusineClient(TestCase):
    """Test :py:func:`make_debusine_client`."""

    def test_properties(self) -> None:
        """The client has reasonable properties."""
        config_file_path = self.create_temporary_file()
        config_file_path.write_text(
            dedent(
                """\
                [server:debusine.example.net]
                api-url = https://debusine.example.net/api
                token = some-token
                scope = default-scope
                """
            )
        )
        profile = {
            "debusine_scope": "debian",
            "debusine_workspace": "base",
            "fqdn": "debusine.example.net",
        }

        with mock.patch(
            "debusine.client.dput_ng.dput_ng_utils.ConfigHandler",
            partial(ConfigHandler, config_file_path=config_file_path),
        ):
            client = make_debusine_client(profile)

        self.assertEqual(
            client.base_api_url, "https://debusine.example.net/api"
        )
        self.assertEqual(client.token, "some-token")
        self.assertEqual(client.scope, "debian")

    def test_searches_for_server(self) -> None:
        """The client searches for a server section with the right api-url."""
        config_file_path = self.create_temporary_file()
        config_file_path.write_text(
            dedent(
                """\
                [General]
                default-server = debusine.incus

                [server:debusine.incus]
                api-url = https://debusine.incus:8000/api
                token = some-token
                scope = debusine

                [server:example]
                api-url = https://debusine.example.net/api
                token = some-token
                scope = debusine

                [server:another-example]
                api-url = https://debusine.another-example.net/api
                token = some-token
                scope = debusine
                """
            )
        )
        profile = {
            "debusine_scope": "debian",
            "debusine_workspace": "base",
            "fqdn": "debusine.example.net",
        }

        with mock.patch(
            "debusine.client.dput_ng.dput_ng_utils.ConfigHandler",
            partial(ConfigHandler, config_file_path=config_file_path),
        ):
            client = make_debusine_client(profile)

        self.assertEqual(
            client.base_api_url, "https://debusine.example.net/api"
        )
        self.assertEqual(client.token, "some-token")
        self.assertEqual(client.scope, "debian")

    def test_missing_server(self) -> None:
        """The client reports an error if there is no suitable configuration."""
        config_file_path = self.create_temporary_file()
        config_file_path.write_text(
            dedent(
                """\
                [server:debusine.incus]
                api-url = https://debusine.incus:8000/api
                token = some-token
                scope = debusine
                """
            )
        )
        profile = {
            "debusine_scope": "debian",
            "debusine_workspace": "base",
            "fqdn": "debusine.example.net",
        }

        with (
            mock.patch(
                "debusine.client.dput_ng.dput_ng_utils.ConfigHandler",
                partial(ConfigHandler, config_file_path=config_file_path),
            ),
            self.assertRaisesRegex(
                ValueError,
                r"No debusine client configuration for debusine\.example\.net; "
                r"run 'debusine setup'",
            ),
        ):
            make_debusine_client(profile)
