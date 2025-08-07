# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the debusine-client setup code."""

import errno
import json
import tempfile
import textwrap
from pathlib import Path
from unittest import mock

import responses
from configobj import ConfigObj

from debusine.client.models import (
    EnrollConfirmPayload,
    EnrollOutcome,
    EnrollPayload,
)
from debusine.client.setup import (
    DebusineClientConfig,
    KNOWN_SERVERS,
    ServerConfigEditor,
    ServerInfo,
    ServerSelector,
    TokenFetchFailed,
    setup_server,
)
from debusine.client.tests.utils import MockTable, TestConsole
from debusine.test import TestCase

FEEDBACK_CONFIG_SAVED = "👍 Configuration saved"
FEEDBACK_TOKEN_ACQUIRED = "✅ Confirmation confirmed, token acquired"


class ServerInfoTests(TestCase):
    """Tests for :py:class:`ServerInfo`."""

    def test_from_config(self) -> None:
        """Test from_config."""
        workdir = self.create_temporary_directory()
        path = workdir / "debusine.ini"
        desc = f"Configured in {path.name}"
        for name, cfg, info in (
            (
                "example",
                {"api-url": "http://example.org/api/", "scope": "debusine"},
                ServerInfo(
                    name="example",
                    api_url="http://example.org/api/",
                    desc=desc,
                    scope="debusine",
                ),
            ),
            (
                "debian",
                {"scope": "debusine"},
                ServerInfo(
                    name="debian",
                    api_url="https://debusine.debian.net/api/",
                    desc=desc,
                    scope="debusine",
                ),
            ),
            (
                "empty",
                {},
                ServerInfo(
                    name="empty",
                    api_url="https://empty/api/",
                    desc=desc,
                    scope="debusine",
                ),
            ),
        ):
            initial_cfg = ConfigObj({f"server:{name}": cfg})
            initial_cfg.filename = path.as_posix()
            initial_cfg.write()
            loaded_cfg = ConfigObj(path.as_posix())
            loaded_info = ServerInfo.from_config(
                name, loaded_cfg[f"server:{name}"]
            )
            self.assertEqual(loaded_info, info)

    def test_from_string_known(self) -> None:
        """Test from_string with a known name or hostname."""
        for arg, name in (
            # Match by name
            ("debian", "debian"),
            ("localhost", "localhost"),
            # Match by hostname
            ("debusine.debian.net", "debian"),
            ("localhost:8000", "localhost"),
            (":8000", "localhost"),
            # Match by URL hostname
            ("https://debusine.debian.net/", "debian"),
            ("http://localhost:8000/api/", "localhost"),
        ):
            with self.subTest(arg=arg):
                self.assertIs(
                    ServerInfo.from_string(arg, desc="test"),
                    KNOWN_SERVERS[name],
                )

    def test_from_string(self) -> None:
        """Test building a ServerInfo from a string argument."""
        for server, server_name, api_url in (
            # Bare name
            ("example", "example", "https://example/api/"),
            # Domain name
            (
                "example.org",
                "example.org",
                "https://example.org/api/",
            ),
            # Domain name and port
            (
                "example.org:8000",
                "example.org",
                "https://example.org:8000/api/",
            ),
            # Base URL
            (
                "https://example.org",
                "example.org",
                "https://example.org/api/",
            ),
            # API URL without trailing slash
            (
                "https://example.org/api",
                "example.org",
                "https://example.org/api/",
            ),
            # API URL with trailing slash
            (
                "https://example.org/api/",
                "example.org",
                "https://example.org/api/",
            ),
            # Subdir URL without trailing slash
            (
                "https://example.org/debusine",
                "example.org",
                "https://example.org/debusine/api/",
            ),
            # Subdir URL with trailing slash
            (
                "https://example.org/debusine/",
                "example.org",
                "https://example.org/debusine/api/",
            ),
        ):
            with self.subTest(server=server):
                self.assertEqual(
                    ServerInfo.from_string(server, "desc"),
                    ServerInfo(
                        name=server_name,
                        api_url=api_url,
                        scope="debusine",
                        desc="desc",
                    ),
                )

    def test_lint_api_url(self) -> None:
        """Test linting of API URLs."""
        for api_url, messages in (
            (
                "https://localhost/api/",
                [
                    "Server url is an [bold]https[/] url,"
                    " should it be [bold]http[/]?"
                ],
            ),
            (
                "http://example.org/api/",
                [
                    "Server url is an [bold]http[/] url,"
                    " should it be [bold]https[/]?"
                ],
            ),
            ("https://example.org/api", []),
            (
                "https://example.org/other/",
                ["API URL does not end in /api or /api/"],
            ),
            (
                "http://example.org/",
                [
                    "Server url is an [bold]http[/] url,"
                    " should it be [bold]https[/]?",
                    "API URL does not end in /api or /api/",
                ],
            ),
        ):
            with self.subTest(api_url=api_url):
                info = ServerInfo(
                    name="test", desc="test", api_url=api_url, scope="test"
                )
                self.assertEqual(info.lint(), messages)

    def _fetch_token(
        self,
        nonce: str = "12345678",
        challenge: str = "correct horse battery staple",
    ) -> tuple[str | None, list[str], TokenFetchFailed | None]:
        exception: TokenFetchFailed | None
        server = KNOWN_SERVERS["debian"]
        console = TestConsole()
        with (
            mock.patch(
                "debusine.client.setup.secrets.token_urlsafe",
                return_value=nonce,
            ),
            mock.patch(
                "debusine.client.setup.xkcd_password.generate_xkcdpassword",
                return_value=challenge,
            ),
            mock.patch(
                "debusine.client.setup.platform.node",
                return_value="hostname",
            ),
        ):
            try:
                result = server.fetch_token(console)
            except TokenFetchFailed as e:
                exception = e
                result = None
            else:
                exception = None

        # A proper payload was sent
        self.assertEqual(len(responses.calls), 1)
        call = responses.calls[0]
        assert call.request.body is not None
        self.assertEqual(
            call.request.headers["Content-Type"], "application/json"
        )
        self.assertEqual(
            json.loads(call.request.body),
            EnrollPayload(
                nonce=nonce,
                challenge=challenge,
                hostname="hostname",
                scope=server.scope,
            ).dict(),
        )

        output_lines = console.output.getvalue().splitlines()

        confirm_url = f"https://debusine.debian.net/-/enroll/confirm/{nonce}"
        self.assertEqual(
            output_lines[0],
            f"👉 Please visit {confirm_url} to confirm registration",
        )

        self.assertEqual(
            output_lines[1],
            f'👉 Make sure the page mentions the passphrase: "{challenge}"',
        )

        return result, output_lines[2:], exception

    @responses.activate
    def test_fetch_token_confirm(self) -> None:
        """Test getting a token from the server."""
        nonce = "12345678"
        challenge = "correct horse battery staple"
        token = "a" * 32
        responses.add(
            responses.POST,
            "https://debusine.debian.net/api/enroll/",
            json=EnrollConfirmPayload(
                outcome=EnrollOutcome.CONFIRM, token=token
            ).dict(),
        )

        # Fetch the token
        result, feedback, exception = self._fetch_token(nonce, challenge)
        self.assertEqual(result, token)
        self.assertIsNone(exception)

        # URL and challenge were printed in console
        self.assertEqual(
            feedback, ["✅ Confirmation confirmed, token acquired"]
        )

    @responses.activate
    def test_fetch_token_cancel(self) -> None:
        """Test getting a token from the server."""
        nonce = "12345678"
        challenge = "correct horse battery staple"
        responses.add(
            responses.POST,
            "https://debusine.debian.net/api/enroll/",
            json=EnrollConfirmPayload(outcome=EnrollOutcome.CANCEL).dict(),
        )

        # Fetch the token
        result, feedback, exception = self._fetch_token(nonce, challenge)
        self.assertIsNone(result)
        self.assertIsNone(exception)

        # URL and challenge were printed in console
        self.assertEqual(feedback, ["❗ Confirmation cancelled"])

    @responses.activate
    def test_fetch_token_server_not_found(self) -> None:
        """Test fetch_token encountering a network error."""
        responses.add(
            responses.POST,
            "https://debusine.debian.net/api/enroll/",
            body=OSError(errno.ESRCH),
        )

        # Fetch the token
        result, feedback, exception = self._fetch_token()
        self.assertIsNone(result)
        self.assertEqual(str(exception), "request to server failed: 3")
        self.assertEqual(feedback, [])

    @responses.activate
    def test_fetch_token_server_problemresponse(self) -> None:
        """Test fetch_token getting a ProblemResponse."""
        responses.add(
            responses.POST,
            "https://debusine.debian.net/api/enroll/",
            status=404,
            json={"title": "fake server error"},
        )

        result, feedback, exception = self._fetch_token()
        self.assertIsNone(result)
        self.assertEqual(
            str(exception),
            "The server returned status code 404: fake server error",
        )
        self.assertEqual(feedback, [])

    @responses.activate
    def test_fetch_token_non_problemresponse_json(self) -> None:
        """Test a JSON server error but not a ProblemResponse."""
        responses.add(
            responses.POST,
            "https://debusine.debian.net/api/enroll/",
            status=500,
            json={"mischief": True},
        )

        result, feedback, exception = self._fetch_token()
        self.assertIsNone(result)
        self.assertEqual(
            str(exception),
            """The server returned status code 500: {"mischief": true}""",
        )
        self.assertEqual(feedback, [])

    @responses.activate
    def test_fetch_token_server_error(self) -> None:
        """Test fetch_token getting a server error."""
        responses.add(
            responses.POST,
            "https://debusine.debian.net/api/enroll/",
            status=500,
            body="fake internal server error",
        )

        result, feedback, exception = self._fetch_token()
        self.assertIsNone(result)
        self.assertEqual(
            str(exception),
            "The server returned status code 500: fake internal server error",
        )
        self.assertEqual(feedback, [])

    @responses.activate
    def test_fetch_token_server_badjson(self) -> None:
        """Test fetch_token getting invalid json."""
        responses.add(
            responses.POST,
            "https://debusine.debian.net/api/enroll/",
            body="{",
            content_type="application/json",
        )

        result, feedback, exception = self._fetch_token()
        self.assertIsNone(result)
        self.assertEqual(str(exception), "Invalid JSON in response")
        self.assertEqual(feedback, [])

    @responses.activate
    def test_fetch_token_server_badpayload(self) -> None:
        """Test fetch_token getting a bad payload."""
        responses.add(
            responses.POST,
            "https://debusine.debian.net/api/enroll/",
            json={"mischief": True},
        )

        result, feedback, exception = self._fetch_token()
        self.assertIsNone(result)
        self.assertEqual(str(exception), "Invalid response payload")
        self.assertEqual(feedback, [])


class DebusineClientConfigTests(TestCase):
    """Tests for :py:class:`DebusineClientConfig`."""

    def make_config(
        self,
        path_or_contents: Path | str = Path("/dev/null"),
        selected: str | None = "debian",
    ) -> DebusineClientConfig:
        """Create a DebusineClientConfig object for testing."""
        if isinstance(path_or_contents, str):
            path = self.create_temporary_file()
            path.write_text(textwrap.dedent(path_or_contents))
        else:
            path = path_or_contents
        config = DebusineClientConfig(path)
        if selected is not None:
            config.select(selected, "test")
        return config

    def test_default_server_unset(self) -> None:
        """Test default_server when not configured."""
        config = DebusineClientConfig(Path("/dev/null"))
        self.assertIsNone(config.default_server)

    def test_default_server_set_existing(self) -> None:
        """Test default_server when set to an existing server."""
        config = self.make_config(
            """
            [General]
            default-server = debian
            [server:debian]
            """,
        )
        self.assertEqual(config.default_server, "debian")

    def test_default_server_set_not_existing(self) -> None:
        """Test default_server when set to a non-existing server."""
        config = self.make_config(
            """
            [General]
            default-server = test
            """,
            selected=None,
        )
        self.assertEqual(config.default_server, "test")

    def test_set_default(self) -> None:
        config = self.make_config(
            """
            [General]
            default-server = debian
            [server:debian]
            """,
            selected="localhost",
        )
        self.assertEqual(config.default_server, "debian")
        self.assertEqual(config.server.name, "localhost")
        config.set_default()
        self.assertEqual(config.default_server, "localhost")
        config.save()
        self.assertEqual(
            config.path.read_text(),
            textwrap.dedent(
                """
                [General]
                default-server = localhost
                [server:debian]
                [server:localhost]
                api-url = http://localhost:8000/api/
                scope = debusine
                """
            ),
        )

    def test_save_default_never_set(self) -> None:
        """Test saving when default server has never been set."""
        config = self.make_config("[server:debian]")
        config.save()
        self.assertEqual(config.path.read_text(), "[server:debian]\n")

    def test_save_default_initially_unset(self) -> None:
        """Test saving when default server is initially unset."""
        config = self.make_config("[server:debian]")
        config.set_default()
        config.save()
        self.assertEqual(
            config.path.read_text(),
            textwrap.dedent(
                """
        [server:debian]
        [General]
        default-server = debian
        """
            ).lstrip(),
        )

    def test_save_default_changed(self) -> None:
        """Test saving when default server is initially unset."""
        config = self.make_config(
            """
            [General]
            default-server=debian
            [server:debian]
            """,
            selected="localhost",
        )
        config.set_default()
        config.save()
        self.assertEqual(
            config.path.read_text(),
            textwrap.dedent(
                """
        [General]
        default-server = localhost
        [server:debian]
        [server:localhost]
        api-url = http://localhost:8000/api/
        scope = debusine
        """
            ),
        )

    def test_save_default_unchanged(self) -> None:
        """Test saving when default server is unchanged."""
        config = self.make_config(
            """
            [General]
            default-server=debian
            [server:debian]
            """,
            selected="localhost",
        )
        config.save()
        self.assertEqual(
            config.path.read_text(),
            textwrap.dedent(
                """
                [General]
                default-server = debian
                [server:debian]
                [server:localhost]
                api-url = http://localhost:8000/api/
                scope = debusine
                """,
            ),
        )

    def test_save(self) -> None:
        """Test saving the configuration."""

        def body(text: str) -> str:
            return textwrap.dedent(text).lstrip()

        for initial, server, changed, expected in (
            # Fill with defaults
            (
                "",
                "localhost",
                {},
                body(
                    """
                    [server:localhost]
                    api-url = http://localhost:8000/api/
                    scope = debusine
                    """
                ),
            ),
            # Add a section from defaults
            (
                body(
                    """
                    # Debian account
                    [server:debian]
                    api-url=http://debusine.debian.net/api/
                    scope=debian
                    # Test comment
                    token=12345678
                    """
                ),
                "localhost",
                {},
                body(
                    """
                    # Debian account
                    [server:debian]
                    api-url = http://debusine.debian.net/api/
                    scope = debian
                    # Test comment
                    token = 12345678
                    [server:localhost]
                    api-url = http://localhost:8000/api/
                    scope = debusine
                    """
                ),
            ),
            # Fetch a token for an existing section
            (
                body(
                    """
                    # Debian account
                    [server:debian]
                    api-url=http://debusine.debian.net/api/
                    scope=debian
                    """
                ),
                "debian",
                {"api_token": "12345678"},
                body(
                    """
                    # Debian account
                    [server:debian]
                    api-url = http://debusine.debian.net/api/
                    scope = debian
                    token = 12345678
                    """
                ),
            ),
            # Change API URL, preserving comments
            (
                body(
                    """
                    # Debian account
                    [server:localhost]
                    # Misses a port?
                    api-url=http://localhost/api/
                    scope=debian
                    """
                ),
                "localhost",
                {"api_url": "http://localhost:8000/api/"},
                body(
                    """
                    # Debian account
                    [server:localhost]
                    # Misses a port?
                    api-url = http://localhost:8000/api/
                    scope = debian
                    """
                ),
            ),
            # Clear API token
            (
                body(
                    """
                    [server:localhost]
                    api-url=http://localhost/api/
                    scope=debian
                    # Token value
                    token=12345678
                    """
                ),
                "localhost",
                {"api_token": None},
                body(
                    """
                    [server:localhost]
                    api-url = http://localhost/api/
                    scope = debian
                    """
                ),
            ),
        ):
            with (
                self.subTest(initial=initial, server=server, changed=changed),
                tempfile.NamedTemporaryFile("w+t") as cfg_file,
            ):
                cfg_file.write(initial)
                cfg_file.flush()
                cfg = DebusineClientConfig(Path(cfg_file.name))
                cfg.select(server, "desc")
                for k, v in changed.items():
                    getattr(cfg, f"set_{k}")(v)
                cfg.save()
                cfg_file.seek(0)
                self.assertEqual(cfg_file.read(), expected)

    def test_save_parent_dir_missing(self) -> None:
        """Test saving the configuration in a missing path."""
        workdir = self.create_temporary_directory()
        configdir = workdir / "client"
        configfile = configdir / "config.ini"
        self.assertFalse(configdir.exists())
        self.assertFalse(configfile.exists())
        cfg = self.make_config(configfile, "localhost")
        cfg.save()
        self.assertTrue(configdir.is_dir())
        self.assertTrue(configfile.is_file())

    def test_fetch_token(self) -> None:
        token = "aaaaaa"
        console = TestConsole()
        cfg = self.make_config()
        with mock.patch(
            "debusine.client.setup.ServerInfo.fetch_token", return_value=token
        ):
            self.assertTrue(cfg.fetch_token(console))
        self.assertEqual(cfg.server.api_token, token)
        self.assertEqual(console.output_lines(), [])

    def test_fetch_cancelled(self) -> None:
        console = TestConsole()
        cfg = self.make_config()
        cfg.set_api_token("aaaaaa")
        with mock.patch(
            "debusine.client.setup.ServerInfo.fetch_token", return_value=None
        ):
            self.assertTrue(cfg.fetch_token(console))
        self.assertIsNone(cfg.server.api_token)
        self.assertEqual(console.output_lines(), [])

    def test_fetch_token_error(self) -> None:
        token = "aaaaaa"
        console = TestConsole()
        cfg = self.make_config()
        cfg.set_api_token(token)
        with mock.patch(
            "debusine.client.setup.ServerInfo.fetch_token",
            side_effect=TokenFetchFailed("message"),
        ):
            self.assertFalse(cfg.fetch_token(console))
        self.assertEqual(console.output_lines(), ["❗ message"])
        self.assertEqual(cfg.server.api_token, token)

    def test_switch(self) -> None:
        """Test switching current server."""
        cfg = self.make_config(selected="debian")
        cfg.set_scope("testdebian")

        cfg.select("localhost", "test")
        self.assertEqual(cfg.server.name, "localhost")
        self.assertEqual(cfg.server.scope, "debusine")

        cfg.select("debian", "test")
        self.assertEqual(cfg.server.name, "debian")
        self.assertEqual(cfg.server.scope, "testdebian")

    def test_save_multiple(self) -> None:
        """Test saving multiple edited servers."""
        with tempfile.NamedTemporaryFile("w+t") as cfg_file:
            path = Path(cfg_file.name)
            path.write_text(
                textwrap.dedent(
                    """
            [server:test]
            api-url=https://test.example.org
            """
                ).lstrip()
            )

            cfg = self.make_config(path, selected="debian")
            cfg.set_scope("testdebian")
            cfg.select("test", "test")
            cfg.set_scope("testtest")
            cfg.save()

            saved = path.read_text()

        self.assertEqual(
            saved.splitlines(),
            [
                "[server:test]",
                "api-url = https://test.example.org",
                "scope = testtest",
                "[server:debian]",
                "api-url = https://debusine.debian.net/api/",
                "scope = testdebian",
            ],
        )

    def test_server_not_selected(self) -> None:
        """Accessing the current server when not selected raises an error."""
        config = DebusineClientConfig(Path("/dev/null"))
        with self.assertRaisesRegex(
            RuntimeError, "current server has not been selected"
        ):
            config.server


class ServerSelectorTests(TestCase):
    """Tests for :py:class:`ServerSelector`."""

    def test_cmdloop_no_entries(self) -> None:
        """Test cmdloop behaviour with no entries."""
        with (
            mock.patch(
                "debusine.client.dataentry.DataEntry.cmdloop"
            ) as cmdloop,
            mock.patch("debusine.client.setup.ServerSelector.do_new") as do_new,
        ):
            console = TestConsole()
            selector = ServerSelector([], console=console)
            selector.cmdloop()
        cmdloop.assert_not_called()
        do_new.assert_called()

    def test_cmdloop_with_entries(self) -> None:
        """Test cmdloop behaviour with entries."""
        with (
            mock.patch(
                "debusine.client.dataentry.DataEntry.cmdloop"
            ) as cmdloop,
            mock.patch("debusine.client.setup.ServerSelector.do_new") as do_new,
        ):
            selector = ServerSelector(
                list(KNOWN_SERVERS.values()), console=TestConsole()
            )
            selector.cmdloop()
        cmdloop.assert_called()
        do_new.assert_not_called()

    def test_do_new(self) -> None:
        """Test do_new."""
        for entered, stored in (
            ("", None),
            ("localhost", KNOWN_SERVERS["localhost"]),
            (
                "https://example.org",
                ServerInfo(
                    "example.org",
                    desc="Manually entered",
                    api_url="https://example.org/api/",
                    scope="debusine",
                ),
            ),
        ):
            with self.subTest(entered=entered):
                selector = ServerSelector(
                    [KNOWN_SERVERS["localhost"]], console=TestConsole()
                )
                with mock.patch(
                    "debusine.client.dataentry.DataEntry.input_line",
                    return_value=entered,
                ):
                    selector.onecmd("new")
                self.assertEqual(selector.selected, stored)

    def test_table(self) -> None:
        """Test table display."""
        console = TestConsole()
        selector = ServerSelector(
            list(KNOWN_SERVERS.values()),
            console=console,
            default="localhost",
        )
        with mock.patch("debusine.client.setup.Table", new=MockTable):
            selector.menu()

        table = console.tables[0]
        self.assertEqual(
            [c.args for c in table.rows],
            [
                ("1", "debian", "", "debusine.debian.net"),
                ("2", "freexian", "", "Freexian (E)LTS"),
                (
                    "3",
                    "localhost",
                    "yes",
                    "debusine-admin runserver for development",
                ),
            ],
        )

    def test_menu_prompt(self) -> None:
        """Test menu prompt."""
        for entries, expected in (
            ([], "new, quit:"),
            ([ServerInfo.from_string("a", "a")], "1, new, quit:"),
            (
                [ServerInfo.from_string(x, x) for x in ("a", "b", "c")],
                "1…3, new, quit:",
            ),
        ):
            with self.subTest(entries=entries):
                console = TestConsole()
                selector = ServerSelector(entries, console=console)
                selector.menu()
                self.assertEqual(
                    console.output.getvalue().splitlines()[-1], expected
                )

    def test_enter_number(self) -> None:
        """Test entering a number to pick an entry."""
        selector = ServerSelector(
            list(KNOWN_SERVERS.values()), console=TestConsole()
        )
        self.assertTrue(selector.onecmd("2"))
        self.assertIs(selector.selected, KNOWN_SERVERS["freexian"])

    def test_enter_number_out_of_range(self) -> None:
        """Test entering a number out of range."""
        console = TestConsole()
        selector = ServerSelector(list(KNOWN_SERVERS.values()), console=console)
        for num in 0, len(KNOWN_SERVERS) + 1, len(KNOWN_SERVERS) + 10:
            with self.subTest(num=num):
                console.reset_output()
                self.assertFalse(selector.onecmd(str(num)))
                self.assertEqual(
                    console.output.getvalue(),
                    "Entry number must be between"
                    f" 1 and {len(KNOWN_SERVERS)}\n",
                )
                self.assertIsNone(selector.selected)

    def test_enter_invalid_command(self) -> None:
        """Test entering an invalid command."""
        console = TestConsole()
        selector = ServerSelector(list(KNOWN_SERVERS.values()), console=console)
        for cmd in "-1", "14.3", "mischief":
            with self.subTest(cmd=cmd):
                console.reset_output()
                self.assertFalse(selector.onecmd(cmd))
                self.assertEqual(
                    console.output.getvalue(),
                    f"command not recognized: {cmd!r}\n",
                )
                self.assertIsNone(selector.selected)


class ServerConfigEditorTests(TestCase):
    """Tests for :py:class:`ServerConfigEditor`."""

    def _config(self) -> DebusineClientConfig:
        res = DebusineClientConfig(Path("/dev/null"))
        res.select("debian", "tests")
        return res

    def test_menu(self) -> None:
        """Test formatting the menu."""
        cfg = self._config()
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with mock.patch("debusine.client.setup.Table", new=MockTable):
            editor.menu()
        table = console.tables[0]
        self.assertEqual(
            table.columns[0].args[0],
            "Configuration for debian"
            " [yellow not bold](default is [i]None[/])[/]",
        )
        self.assertEqual(
            [c.args for c in table.rows],
            [
                ("\\[server:[bold]debian[bold]]", ""),
                ("scope = [bold]debian[bold]", "([bold]sc[/]ope)"),
                (
                    "api-url = [bold]https://debusine.debian.net/api/[bold]",
                    "([bold]u[/]rl)",
                ),
                ("token = [italic]acquired on save[/]", "([bold]t[/]oken)"),
            ],
        )

        output = console.output.getvalue()
        self.assertEqual(
            output.splitlines()[-1],
            "Commands: url, token, scope, (make) default, save, quit:",
        )

    def test_menu_default_server(self) -> None:
        """Test menu showing the default server."""
        cfg = self._config()
        cfg.set_default()
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with mock.patch("debusine.client.setup.Table", new=MockTable):
            editor.menu()
        table = console.tables[0]
        self.assertEqual(
            table.columns[0].args[0],
            "Configuration for debian" " [green not bold](default server)[/]",
        )

    def test_menu_with_token(self) -> None:
        """Test formatting the menu when the token is set."""
        cfg = self._config()
        cfg.set_api_token("12345678")
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        editor.menu()
        self.assertIn("token = present", console.output.getvalue())

    def test_menu_linter(self) -> None:
        """Ensure linter messages appear in the menu."""
        cfg = self._config()
        cfg.set_api_url("http://debusine.debian.net/api/")
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        editor.menu()
        self.assertIn(
            "👉 Server url is an http url, should it be https?",
            console.output_lines(),
        )

    def test_edit_scope(self) -> None:
        """Edit the scope."""
        cfg = self._config()
        self.assertEqual(cfg.server.scope, "debian")
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with mock.patch(
            "debusine.client.dataentry.DataEntry.input_line",
            return_value="debusine",
        ) as input_line:
            editor.onecmd("scope")
        self.assertEqual(cfg.server.scope, "debusine")
        input_line.assert_called_with(
            "Enter scope", initial="debian", required=True
        )

    def test_edit_url(self) -> None:
        """Edit the api url."""
        old_url = "https://debusine.debian.net/api/"
        new_url = "https://debusine.debian.org/api/"
        cfg = self._config()
        self.assertEqual(cfg.server.api_url, old_url)
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with mock.patch(
            "debusine.client.dataentry.DataEntry.input_line",
            return_value=new_url,
        ) as input_line:
            editor.onecmd("url")
        self.assertEqual(cfg.server.api_url, new_url)
        input_line.assert_called_with(
            "Enter API URL", initial=old_url, required=True
        )

    def test_edit_token(self) -> None:
        """Edit the token."""
        new_token = "12345678"
        cfg = self._config()
        self.assertIsNone(cfg.server.api_token)
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with mock.patch(
            "debusine.client.dataentry.DataEntry.input_line",
            return_value=new_token,
        ) as input_line:
            editor.onecmd("token")
        self.assertEqual(cfg.server.api_token, new_token)
        input_line.assert_called_with(
            "Enter (or clear) API token", initial=None
        )

    def test_set_default(self) -> None:
        """Set the current server as default."""
        cfg = self._config()
        self.assertIsNone(cfg.default_server)
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        editor.onecmd("default")
        self.assertEqual(cfg.default_server, "debian")

    def test_clear_token(self) -> None:
        """Clear the token."""
        old_token = "12345678"
        cfg = self._config()
        cfg.set_api_token(old_token)
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with mock.patch(
            "debusine.client.dataentry.DataEntry.input_line",
            return_value="",
        ) as input_line:
            editor.onecmd("token")
        self.assertIsNone(cfg.server.api_token)
        input_line.assert_called_with(
            "Enter (or clear) API token", initial=old_token
        )

    def test_quit(self) -> None:
        """Test quitting the editor."""
        cfg = self._config()
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        self.assertTrue(editor.onecmd("quit"))
        output = console.output.getvalue()
        self.assertEqual(
            output.splitlines()[-1], "🖐 Configuration left unchanged"
        )

    def test_save_with_token(self) -> None:
        """Test the save command with a token already present."""
        cfg = self._config()
        cfg.set_api_token("12345678")
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with mock.patch(
            "debusine.client.setup.DebusineClientConfig.save"
        ) as save:
            self.assertTrue(editor.onecmd("save"))
        save.assert_called()
        self.assertEqual(
            console.output.getvalue().splitlines(), [FEEDBACK_CONFIG_SAVED]
        )

    def test_save_fetch_token_confirm(self) -> None:
        """Test the save command with a token, user confirmed."""
        cfg = self._config()

        def _mock_fetch_token(console: TestConsole) -> bool:  # noqa: U100
            cfg.set_api_token("12345678")
            return True

        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with (
            mock.patch.object(cfg, "save") as save,
            mock.patch.object(
                cfg, "fetch_token", side_effect=_mock_fetch_token
            ) as fetch_token,
        ):
            self.assertTrue(editor.onecmd("save"))
        fetch_token.assert_called_with(console)
        save.assert_called()
        self.assertEqual(cfg.server.api_token, "12345678")
        self.assertEqual(
            console.output.getvalue().splitlines(), [FEEDBACK_CONFIG_SAVED]
        )

    def test_save_fetch_token_cancel(self) -> None:
        """Test the save command with a token, user cancelled."""
        cfg = self._config()

        def _mock_fetch_token(console: TestConsole) -> bool:  # noqa: U100
            cfg.set_api_token(None)
            return True

        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with (
            mock.patch.object(cfg, "save") as save,
            mock.patch.object(
                cfg, "fetch_token", side_effect=_mock_fetch_token
            ) as fetch_token,
        ):
            self.assertTrue(editor.onecmd("save"))
        fetch_token.assert_called_with(console)
        save.assert_called()
        self.assertIsNone(cfg.server.api_token)
        self.assertEqual(
            console.output.getvalue().splitlines(), [FEEDBACK_CONFIG_SAVED]
        )

    def test_save_retry(self) -> None:
        """Test the save command with a token."""
        cfg = self._config()

        call_count = 0

        def _mock_fetch_token(console: TestConsole) -> bool:  # noqa: U100
            nonlocal call_count
            try:
                match call_count:
                    case 0:
                        return False
                    case _:
                        cfg.set_api_token("12345678")
                        return True
            finally:
                call_count += 1

        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with (
            mock.patch.object(cfg, "save") as save,
            mock.patch.object(
                cfg, "fetch_token", side_effect=_mock_fetch_token
            ) as fetch_token,
            mock.patch(
                "debusine.client.setup.Confirm.ask", return_value=True
            ) as ask,
        ):
            self.assertTrue(editor.onecmd("save"))
        fetch_token.assert_called_with(console)
        ask.assert_called_with("Fetch failed: retry?", console=console)
        save.assert_called()
        self.assertEqual(cfg.server.api_token, "12345678")
        self.assertEqual(
            console.output.getvalue().splitlines(), [FEEDBACK_CONFIG_SAVED]
        )

    def test_save_abort(self) -> None:
        """Test the save command with a token."""
        cfg = self._config()
        console = TestConsole()
        editor = ServerConfigEditor(cfg, console=console)
        with (
            mock.patch.object(cfg, "save") as save,
            mock.patch.object(
                cfg, "fetch_token", return_value=False
            ) as fetch_token,
            mock.patch(
                "debusine.client.setup.Confirm.ask", return_value=False
            ) as ask,
        ):
            self.assertTrue(editor.onecmd("save"))
        fetch_token.assert_called_with(console)
        ask.assert_called_with("Fetch failed: retry?", console=console)
        save.assert_called()
        self.assertIsNone(cfg.server.api_token)
        self.assertEqual(
            console.output.getvalue().splitlines(), [FEEDBACK_CONFIG_SAVED]
        )


class SetupServerTests(TestCase):
    """Tests for :py:func:`setup_server`."""

    def test_no_args(self) -> None:
        """Invoke with no arguments."""
        workdir = self.create_temporary_directory()
        console = TestConsole()

        def _select_server(self: ServerSelector) -> None:
            self.selected = ServerInfo.from_string("name", desc="desc")

        with (
            mock.patch(
                "debusine.client.setup.ServerSelector.cmdloop",
                autospec=True,
                side_effect=_select_server,
            ) as selector_cmdloop,
            mock.patch(
                "debusine.client.setup.ServerConfigEditor.cmdloop",
                autospec=True,
            ) as editor_cmdloop,
        ):
            setup_server(
                config_file_path=workdir / "config.ini", console=console
            )

        selector_cmdloop.assert_called()
        selector = selector_cmdloop.call_args.args[0]
        self.assertEqual(selector.entries, list(KNOWN_SERVERS.values()))
        editor_cmdloop.assert_called()
        editor = editor_cmdloop.call_args.args[0]
        server = editor.config.server
        self.assertEqual(server.name, "name")
        self.assertEqual(server.scope, "debusine")

    def test_no_server_selected(self) -> None:
        """Invoke with no arguments."""
        workdir = self.create_temporary_directory()
        console = TestConsole()

        with (
            mock.patch(
                "debusine.client.setup.ServerSelector.cmdloop",
            ) as selector_cmdloop,
            mock.patch(
                "debusine.client.setup.ServerConfigEditor.cmdloop",
                autospec=True,
            ) as editor_cmdloop,
        ):
            setup_server(
                config_file_path=workdir / "config.ini", console=console
            )

        selector_cmdloop.assert_called()
        editor_cmdloop.assert_not_called()

    def test_server_provided(self) -> None:
        """Invoke with server set."""
        workdir = self.create_temporary_directory()
        console = TestConsole()

        with (
            mock.patch(
                "debusine.client.setup.ServerSelector.cmdloop",
            ) as selector_cmdloop,
            mock.patch(
                "debusine.client.setup.ServerConfigEditor.cmdloop",
                autospec=True,
            ) as editor_cmdloop,
        ):
            setup_server(
                config_file_path=workdir / "config.ini",
                server="name",
                console=console,
            )

        selector_cmdloop.assert_not_called()
        editor_cmdloop.assert_called()
        editor = editor_cmdloop.call_args.args[0]
        server = editor.config.server
        self.assertEqual(server.name, "name")
        self.assertEqual(server.scope, "debusine")
        self.assertEqual(editor.config.default_server, "name")

    def test_scope_provided(self) -> None:
        """Invoke with scope set."""
        workdir = self.create_temporary_directory()
        console = TestConsole()

        def _select_server(self: ServerSelector) -> None:
            self.selected = ServerInfo.from_string("name", desc="desc")

        with (
            mock.patch(
                "debusine.client.setup.ServerSelector.cmdloop",
                autospec=True,
                side_effect=_select_server,
            ) as selector_cmdloop,
            mock.patch(
                "debusine.client.setup.ServerConfigEditor.cmdloop",
                autospec=True,
            ) as editor_cmdloop,
        ):
            setup_server(
                config_file_path=workdir / "config.ini",
                scope="scope",
                console=console,
            )

        selector_cmdloop.assert_called()
        selector = selector_cmdloop.call_args.args[0]
        self.assertEqual(selector.entries, list(KNOWN_SERVERS.values()))
        editor_cmdloop.assert_called()
        editor = editor_cmdloop.call_args.args[0]
        server = editor.config.server
        self.assertEqual(server.name, "name")
        self.assertEqual(server.scope, "scope")

    def test_existing_config(self) -> None:
        """Invoke with an existing configuration."""
        workdir = self.create_temporary_directory()
        config_path = workdir / "config.ini"
        config_path.write_text(
            textwrap.dedent(
                """
                [General]
                default-server=localhost
                [server:localhost]
                scope = debusine
                api-url = http://localhost:8000/api/
                [server:debian]
                scope = debian
                api-url = http://debusine.debian.net/api/
                [server:example]
                """
            )
        )
        console = TestConsole()

        def _select_server(self: ServerSelector) -> None:
            self.selected = KNOWN_SERVERS["debian"]

        with (
            mock.patch(
                "debusine.client.setup.ServerSelector.cmdloop",
                autospec=True,
                side_effect=_select_server,
            ) as selector_cmdloop,
            mock.patch(
                "debusine.client.setup.ServerConfigEditor.cmdloop",
                autospec=True,
            ) as editor_cmdloop,
        ):
            setup_server(
                config_file_path=config_path,
                console=console,
            )

        selector_cmdloop.assert_called()
        selector = selector_cmdloop.call_args.args[0]
        self.assertEqual(
            [x.name for x in selector.entries],
            ["localhost", "debian", "example", "freexian"],
        )
        editor_cmdloop.assert_called()
        editor = editor_cmdloop.call_args.args[0]
        server = editor.config.server
        self.assertEqual(server.name, "debian")
        self.assertEqual(server.scope, "debian")
        self.assertEqual(editor.config.default_server, "localhost")

    def test_existing_config_override_scope(self) -> None:
        """Invoke with an existing configuration, setting a scope."""
        workdir = self.create_temporary_directory()
        config_path = workdir / "config.ini"
        config_path.write_text(
            textwrap.dedent(
                """
                [server:localhost]
                scope = debusine
                api-url = http://localhost:8000/api/
                """
            )
        )
        console = TestConsole()

        with (
            mock.patch(
                "debusine.client.setup.ServerConfigEditor.cmdloop",
                autospec=True,
            ) as editor_cmdloop,
        ):
            setup_server(
                config_file_path=config_path,
                server="localhost",
                scope="debian",
                console=console,
            )

        editor_cmdloop.assert_called()
        editor = editor_cmdloop.call_args.args[0]
        server = editor.config.server
        self.assertEqual(server.name, "localhost")
        self.assertEqual(server.scope, "debian")
