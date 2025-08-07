# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-client guided setup interface."""

import json
import os
import platform
import re
import secrets
import urllib.parse
from pathlib import Path
from typing import Any, NamedTuple, Self, assert_never

import configobj
import requests
import rich.box
from configobj import ConfigObj
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table
from xkcdpass import xkcd_password

from debusine.client.config import ConfigHandler
from debusine.client.dataentry import DataEntry
from debusine.client.models import (
    EnrollConfirmPayload,
    EnrollOutcome,
    EnrollPayload,
)


class TokenFetchFailed(Exception):
    """Exception raised when fetching a token failed."""


class ServerInfo(NamedTuple):
    """Information about a known server."""

    #: Server name to use in configuration
    name: str
    #: User-visible description
    desc: str
    #: Default API URL
    api_url: str
    #: Default Scope
    scope: str
    #: API token
    api_token: str | None = None

    def lint(self) -> list[str]:
        """
        Run consistency checks on the current draft configuration.

        :returns: a list of messages, or empty if everything looks ordinary
        """
        res: list[str] = []

        api_url_parsed = urllib.parse.urlparse(self.api_url)

        if api_url_parsed.hostname == "localhost":
            expected_scheme = "http"
        else:
            expected_scheme = "https"

        if api_url_parsed.scheme != expected_scheme:
            res.append(
                f"Server url is an [bold]{api_url_parsed.scheme}[/] url,"
                f" should it be [bold]{expected_scheme}[/]?"
            )

        if not self.api_url.endswith("/api") and not self.api_url.endswith(
            "/api/"
        ):
            res.append("API URL does not end in /api or /api/")

        return res

    @classmethod
    def from_config(cls, name: str, section: configobj.Section) -> Self:
        """Infer a ServerInfo from an existing configuration."""
        inferred = cls.from_string(name, desc="temporary serverinfo")
        return cls(
            name=name,
            desc=f"Configured in {os.path.basename(section.main.filename)}",
            api_url=section.get("api-url", inferred.api_url),
            scope=section.get("scope", inferred.scope),
            api_token=section.get("token", None),
        )

    @classmethod
    def from_string(cls, name: str, desc: str) -> Self | "ServerInfo":
        """Infer a ServerInfo from a user-provided name."""
        if ":" not in name:
            # plain hostname
            server_url_parsed = urllib.parse.ParseResult(
                scheme="",
                netloc=name,
                path="",
                params="",
                query="",
                fragment="",
            )
            server_name = name
        elif re.match(r"^[^/]*:\d+(?:/|$)", name):
            # hostname:port, or just :port
            server_url_parsed = urllib.parse.ParseResult(
                scheme="",
                netloc=(f"localhost{name}" if name.startswith(":") else name),
                path="",
                params="",
                query="",
                fragment="",
            )
            assert server_url_parsed.hostname
            server_name = server_url_parsed.hostname
        else:
            # URL
            server_url_parsed = urllib.parse.urlparse(name)
            assert server_url_parsed.hostname
            server_name = server_url_parsed.hostname

        # Lookup known names
        assert server_url_parsed.hostname
        if known := KNOWN_SERVERS.get(server_url_parsed.hostname):
            return known
        # Lookup known hostnames
        for info in KNOWN_SERVERS.values():
            info_parsed = urllib.parse.urlparse(info.api_url)
            if info_parsed.hostname == server_url_parsed.hostname:
                return info

        if not server_url_parsed.scheme:
            server_url_parsed = server_url_parsed._replace(scheme="https")

        if os.path.basename(server_url_parsed.path.rstrip("/")) != "api":
            api_url_parsed = server_url_parsed._replace(
                path=os.path.join(server_url_parsed.path, "api")
            )
        else:
            api_url_parsed = server_url_parsed

        api_url = urllib.parse.urlunparse(api_url_parsed)
        if not api_url.endswith("/"):
            api_url += "/"

        return cls(
            name=server_name, desc=desc, api_url=api_url, scope="debusine"
        )

    def fetch_token(self, console: Console) -> str | None:
        """Get an API token from the server."""
        # Load data for generating challenges
        xkcd_wordfile = xkcd_password.locate_wordfile()
        xkcd_words = xkcd_password.generate_wordlist(
            wordfile=xkcd_wordfile, min_length=5, max_length=8
        )

        payload = EnrollPayload(
            # Nonce used to identify the enrolling session
            nonce=secrets.token_urlsafe(16),
            # Challenge text
            challenge=xkcd_password.generate_xkcdpassword(xkcd_words),
            scope=self.scope,
            hostname=platform.node(),
        )

        api_url_parsed = urllib.parse.urlparse(self.api_url)

        # URL used to get the token
        enroll_url_parsed = api_url_parsed._replace(
            path=os.path.join(api_url_parsed.path, "enroll/")
        )
        enroll_url = urllib.parse.urlunparse(enroll_url_parsed)

        # URL used for the user to confirm enrollment
        confirm_url_parsed = api_url_parsed._replace(
            path=os.path.normpath(
                os.path.join(
                    api_url_parsed.path, f"../-/enroll/confirm/{payload.nonce}/"
                )
            )
        )
        confirm_url = urllib.parse.urlunparse(confirm_url_parsed)

        console.print(
            ":point_right:",
            "Please visit",
            f"[link={confirm_url}]{rich.markup.escape(confirm_url)}[/]",
            "to confirm registration",
        )
        console.print(
            ":point_right:",
            "Make sure the page mentions the passphrase:"
            f' "[bold]{payload.challenge}[/]"',
        )

        payload_dict = payload.dict()
        try:
            response = requests.post(enroll_url, json=payload_dict)
        except Exception as e:
            raise TokenFetchFailed(f"request to server failed: {e}") from e

        if response.status_code != 200:
            lead = f"The server returned status code {response.status_code}: "
            try:
                result = response.json()
            except requests.exceptions.JSONDecodeError:
                raise TokenFetchFailed(lead + response.text)
            else:
                if message := result.get("title"):
                    raise TokenFetchFailed(lead + message)
                else:
                    raise TokenFetchFailed(lead + json.dumps(result))

        try:
            result = response.json()
        except requests.exceptions.JSONDecodeError:
            raise TokenFetchFailed("Invalid JSON in response")

        try:
            confirmation = EnrollConfirmPayload.parse_obj(result)
        except ValueError:
            raise TokenFetchFailed("Invalid response payload")

        match confirmation.outcome:
            case EnrollOutcome.CONFIRM:
                console.print(
                    ":white_check_mark: Confirmation confirmed, token acquired"
                )
                return confirmation.token
            case EnrollOutcome.CANCEL:
                console.print(":exclamation_mark: Confirmation cancelled")
                return None
            case _ as unreachable:
                assert_never(unreachable)


KNOWN_SERVERS: dict[str, ServerInfo] = {
    info.name: info
    for info in (
        ServerInfo(
            name="debian",
            desc="debusine.debian.net",
            api_url="https://debusine.debian.net/api/",
            scope="debian",
        ),
        ServerInfo(
            name="freexian",
            desc="Freexian (E)LTS",
            api_url="https://debusine.freexian.com/api/",
            scope="freexian",
        ),
        ServerInfo(
            name="localhost",
            desc="debusine-admin runserver for development",
            api_url="http://localhost:8000/api/",
            scope="debusine",
        ),
    )
}


class DebusineClientConfig:
    """Editable configuration for debusine client."""

    def __init__(self, path: Path) -> None:
        """
        Initialize from a path.

        :param path: path to the configuration file
        """
        self.path = path
        self.config = ConfigObj(
            self.path.as_posix(),
            interpolation=False,
            list_values=False,
        )
        self.servers: dict[str, ServerInfo] = {
            name[7:]: ServerInfo.from_config(name[7:], section)
            for name, section in self.config.items()
            if name.startswith("server:")
        }
        self.edited: dict[str, ServerInfo] = {}

        # Currently selected server
        self.current_server: ServerInfo | None = None

        # Name of the server set as default
        self.default_server: str | None = None
        if section := self.config.get("General"):
            self.default_server = section.get("default-server")

    def select(self, name: str, desc: str) -> None:
        """
        Select the server to edit.

        :param name: server name
        :param desc: optional description used if creating a new server
        """
        if server := self.edited.get(name):
            # Server is already being edited
            pass
        elif server := self.servers.get(name):
            # Server is in the existing configuration
            self.edited[server.name] = server
        elif server := KNOWN_SERVERS.get(name):
            # Server is known
            self.edited[server.name] = server
        else:
            server = ServerInfo.from_string(name, desc)
            self.edited[server.name] = server
        self.current_server = server

    @property
    def server(self) -> ServerInfo:
        """Require the current server to be set and return it."""
        if self.current_server is None:
            raise RuntimeError("current server has not been selected")
        return self.current_server

    def set_api_url(self, api_url: str) -> None:
        """Set the API url of the current server."""
        edited = self.server._replace(api_url=api_url)
        self.current_server = edited
        self.edited[edited.name] = edited

    def set_scope(self, scope: str) -> None:
        """Set the scope of the current server."""
        edited = self.server._replace(scope=scope)
        self.current_server = edited
        self.edited[edited.name] = edited

    def set_api_token(self, api_token: str | None) -> None:
        """Set the API token of the current server."""
        edited = self.server._replace(api_token=api_token)
        self.current_server = edited
        self.edited[edited.name] = edited

    def set_default(self) -> None:
        """Set the current server as default server."""
        self.default_server = self.server.name

    def fetch_token(self, console: Console) -> bool:
        """Fetch a token and set it in the current configuration."""
        try:
            api_token = self.server.fetch_token(console)
        except TokenFetchFailed as e:
            console.print(":exclamation_mark:", rich.markup.escape(str(e)))
            return False
        self.set_api_token(api_token)
        return True

    def save(self) -> None:
        """Save the configuration."""
        for name, current in self.edited.items():
            orig = self.servers.get(name)

            # Get the server section to save
            key = f"server:{name}"
            if (section := self.config.get(key)) is None:
                self.config[key] = {}
                section = self.config[key]

            # Update with the edited configuration
            if orig is None or orig.api_url != current.api_url:
                section["api-url"] = current.api_url
            if orig is None or orig.scope != current.scope:
                section["scope"] = current.scope
            if orig is None or orig.api_token != current.api_token:
                if current.api_token is None:
                    if "token" in section:
                        del section["token"]
                else:
                    section["token"] = current.api_token

        if self.default_server is not None:
            if section := self.config.get("General"):
                if section.get("default-server") != self.default_server:
                    section["default-server"] = self.default_server
            else:
                self.config["General"] = {"default-server": self.default_server}

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.config.write()


class ServerConfigEditor(DataEntry):
    """Interactive editor for ServerConfig."""

    prompt = "> "

    config: DebusineClientConfig

    def __init__(
        self,
        config: DebusineClientConfig,
        console: Console | None = None,
    ) -> None:
        """Initialize the ServerConfig editor."""
        super().__init__(console=console)
        self.config = config

    def lint_message(self, message: str) -> None:
        """Print a linter message."""
        self.console.print(f":point_right: {message}", highlight=False)

    def menu(self) -> None:
        """Print a menu for the user."""
        server = self.config.server
        is_default = self.config.server.name == self.config.default_server
        title = f"Configuration for {server.name}"
        if is_default:
            title += " [green not bold](default server)[/]"
        else:
            title += (
                " [yellow not bold]"
                f"(default is [i]{self.config.default_server}[/])"
                "[/]"
            )
        table = Table(box=rich.box.SIMPLE)
        table.add_column(title, justify="left", no_wrap=True)
        table.add_column("", justify="left", no_wrap=True)

        table.add_row(fr"\[server:[bold]{server.name}[bold]]", "")
        table.add_row(f"scope = [bold]{server.scope}[bold]", "([bold]sc[/]ope)")
        table.add_row(
            f"api-url = [bold]{server.api_url}[bold]", "([bold]u[/]rl)"
        )
        table.add_row(
            "token = [italic]"
            + ("acquired on save" if server.api_token is None else "present")
            + "[/]",
            "([bold]t[/]oken)",
        )

        self.console.print(table)

        for msg in server.lint():
            self.lint_message(msg)
        self.console.print()
        self.console.print(
            "Commands: [bold]u[/]rl, [bold]t[/]oken, [bold]sc[/]ope, "
            "(make) [bold]d[/]efault, [bold]s[/]ave, [bold]q[/]uit:"
        )

    def do_default(self, arg: str) -> None:  # noqa: U100
        """Set as default server."""
        self.config.set_default()

    def do_scope(self, arg: str) -> None:  # noqa: U100
        """Change the scope."""
        server = self.config.server
        self.config.set_scope(
            self.input_line("Enter scope", initial=server.scope, required=True)
        )

    def do_url(self, arg: str) -> None:  # noqa: U100
        """Change the API URL."""
        server = self.config.server
        self.config.set_api_url(
            self.input_line(
                "Enter API URL", initial=server.api_url, required=True
            )
        )

    def do_token(self, arg: str) -> None:  # noqa: U100
        """Change the token."""
        server = self.config.server
        self.config.set_api_token(
            self.input_line(
                "Enter (or clear) API token", initial=server.api_token
            )
            or None
        )

    def do_quit(self, arg: str) -> bool:  # noqa: U100
        """Quit editor."""
        self.console.print(
            ":hand_with_fingers_splayed: Configuration left unchanged"
        )
        return super().do_quit(arg)

    def do_save(self, arg: str) -> bool:  # noqa: U100
        """Save the new configuration."""
        if not self.config.server.api_token:
            while True:
                if self.config.fetch_token(self.console):
                    break
                retry = Confirm.ask(
                    "Fetch failed: retry?", console=self.console
                )
                if not retry:
                    break

        self.config.save()
        self.console.print(":thumbs_up: Configuration saved")
        return True


class ServerSelector(DataEntry):
    """Interactive server selector."""

    prompt = "> "

    entries: list[ServerInfo]
    selected: ServerInfo | None

    def __init__(
        self,
        entries: list[ServerInfo],
        console: Console | None = None,
        default: str | None = None,
    ) -> None:
        """Initialize the Server selector."""
        super().__init__(console=console)
        self.entries = entries
        self.default_name = default
        self.selected = None

    def menu(self) -> None:
        """Show a list of entries to pick from."""
        table = Table(title="Configured servers", box=rich.box.SIMPLE)
        table.add_column("#", justify="right", no_wrap=True)
        table.add_column("Name", justify="left", no_wrap=True)
        table.add_column("Current", justify="left", no_wrap=True)
        table.add_column("Description", justify="left", no_wrap=True)

        for idx, info in enumerate(self.entries, start=1):
            table.add_row(
                str(idx),
                info.name,
                "yes" if info.name == self.default_name else "",
                info.desc,
            )

        self.console.print(table)

        self.console.print()

        if self.entries:
            if len(self.entries) == 1:
                numbers = "[bold]1[/], "
            else:
                numbers = f"[bold]1…{len(self.entries)}[/], "
        else:
            numbers = ""

        self.console.print(
            f"{numbers}[bold]n[/]ew, [bold]q[/]uit:", highlight=False
        )

    def do_new(self, arg: str) -> bool:  # noqa: U100
        """Create a new entry."""
        entry = self.input_line("New server name or URL")
        if not entry:
            return False
        else:
            self.selected = ServerInfo.from_string(
                entry, desc="Manually entered"
            )
            return True

    # Cmd.default is really expected to return a bool|None, and it looks like
    # badly typed (see how Cmd.onecmd uses it)
    def default(self, line: str) -> bool:  # type: ignore[override]
        """Handle entry IDs."""
        line = line.strip()
        if not re.match("^[0-9]+$", line):
            return super().default(line)

        value = int(line)
        if value < 1 or value > len(self.entries):
            self.console.print(
                "[red]Entry number must be between 1 and"
                f" {len(self.entries)}[/]"
            )
            return False

        self.selected = self.entries[value - 1]
        return True

    def cmdloop(self, intro: Any | None = None) -> None:
        """Run data entry."""
        if not self.entries:
            self.do_new("")
        else:
            super().cmdloop(intro=intro)


def setup_server(
    config_file_path: (
        str | os.PathLike[str]
    ) = ConfigHandler.DEFAULT_CONFIG_FILE_PATH,
    server: str | None = None,
    scope: str | None = None,
    console: Console | None = None,
) -> None:
    """Create or edit a server entry."""
    console = console or Console()
    config = DebusineClientConfig(Path(config_file_path))

    if server is None:
        # Ask the user to pick a server
        servers: list[ServerInfo] = []
        servers.extend(config.servers.values())
        for info in KNOWN_SERVERS.values():
            if info.name in config.servers.keys():
                continue
            servers.append(info)
        server_selector = ServerSelector(
            servers, console=console, default=config.default_server
        )
        server_selector.cmdloop()
        if server_selector.selected is None:
            # Abort if no server was selected
            return
        config.select(server_selector.selected.name, "selected from menu")
    else:
        config.select(server, "provided on command line")

    if scope is not None:
        config.set_scope(scope)

    # Set server as default if no default was set
    if config.default_server is None:
        config.set_default()
        console.print(
            ":star: Default server was not set,"
            f" setting it to [bold]{config.default_server}[/]"
        )

    server_config_editor = ServerConfigEditor(config, console=console)
    server_config_editor.cmdloop()
