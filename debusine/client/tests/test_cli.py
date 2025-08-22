# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the debusine Cli class."""

import contextlib
import http
import io
import json
import logging
import math
import os
import re
import signal
import stat
import textwrap
from collections.abc import Callable, Generator
from datetime import datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any, ClassVar, Literal, NoReturn
from unittest import mock
from unittest.mock import MagicMock, Mock
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import responses
import yaml

from debusine.artifacts import LocalArtifact
from debusine.artifacts.models import (
    ArtifactCategory,
    DebusineTaskConfiguration,
    TaskTypes,
)
from debusine.assets import AssetCategory, KeyPurpose, SigningKeyData
from debusine.client import exceptions
from debusine.client.cli import Cli
from debusine.client.config import ConfigHandler
from debusine.client.debusine import Debusine
from debusine.client.exceptions import DebusineError, NotFoundError
from debusine.client.models import (
    ArtifactResponse,
    AssetResponse,
    AssetsResponse,
    CreateWorkflowRequest,
    RelationResponse,
    RelationsResponse,
    RemoteArtifact,
    TaskConfigurationCollection,
    TaskConfigurationCollectionUpdateResults,
    WorkRequestExternalDebsignRequest,
    WorkRequestRequest,
    WorkRequestResponse,
    WorkflowTemplateRequest,
    WorkspaceInheritanceChain,
    WorkspaceInheritanceChainElement,
    model_to_json_serializable_dict,
)
from debusine.client.task_configuration import RemoteTaskConfigurationRepository
from debusine.test import TestCase
from debusine.test.test_utils import (
    create_artifact_response,
    create_file_response,
    create_remote_artifact,
    create_work_request_response,
    create_workflow_template_response,
)
from debusine.utils import calculate_hash
from debusine.utils.input import YamlEditor


class BaseCliTests(TestCase):
    """Basic functionality to implement tests for the Cli class."""

    def setUp(self) -> None:
        """Configure test object."""
        super().setUp()

        debian_server = {
            'api-url': 'https://debusine.debian.org/api',
            'scope': 'debian',
            'token': 'token-for-debian',
        }
        kali_server = {
            'api-url': 'https://debusine.kali.org/api',
            'scope': 'kali',
            'token': 'token-for-kali',
        }

        self.servers = {
            "debian": debian_server,
            "kali": kali_server,
        }
        self.default_server = "debian"

        self.default_sigint_handler = signal.getsignal(signal.SIGINT)
        self.default_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def tearDown(self) -> None:
        """Cleanup after executing a test."""
        # Restore signal handlers. Cli.execute() changes them
        signal.signal(signal.SIGINT, self.default_sigint_handler)
        signal.signal(signal.SIGTERM, self.default_sigterm_handler)

        # Cli._build_debusine_object and Cli._setup_http_logging reconfigure
        # the logging system.  Reset it.
        for name, logger in logging.Logger.manager.loggerDict.items():
            if not isinstance(logger, logging.PlaceHolder):
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()
                logger.propagate = True

        super().tearDown()

    def create_cli(self, argv: list[str], create_config: bool = True) -> Cli:
        """
        Return a Cli object using argv, self.stdout and self.stderr.

        :param argv: arguments passed to the Cli class.
        :param create_config: True for creating a config file and adding
          --config config_file_path into CLI's argv.
        """
        if create_config:
            if '--config' in argv:  # pragma: no cover
                raise ValueError(
                    'Incompatible options: create_config cannot be True if '
                    '--config is in argv'
                )
            config = self.create_config_file()

            argv = ['--config', config] + argv

        return Cli(argv)

    def create_config_file(self) -> str:
        """Write a config file and returns the path."""
        config_directory = self.create_temp_config_directory(
            {
                'General': {'default-server': self.default_server},
                'server:debian': self.servers["debian"],
                'server:kali': self.servers["kali"],
            }
        )

        return os.path.join(config_directory, 'config.ini')

    def capture_output(
        self,
        func: Callable[..., Any],
        args: list[Any] | None = None,
        assert_system_exit_code: int | None = None,
    ) -> tuple[str, str]:
        """
        Execute func() and return stderr and stdout output.

        :param func: functor to be executed
        :param args: list of arguments to be passed to func() (or None if
          no arguments are passed when calling func()
        :param assert_system_exit_code: if not None assert that SystemExit
          is raised
        """
        if args is None:
            args = []

        stderr = io.StringIO()
        stdout = io.StringIO()

        with (
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(stdout),
        ):
            if assert_system_exit_code is not None:
                with self.assertRaisesSystemExit(assert_system_exit_code):
                    func(*args)
            else:
                func(*args)

        return stderr.getvalue(), stdout.getvalue()

    def patch_sys_stdin_read(self) -> MagicMock:
        """Patch sys.stdin.read to return what a user might write / input."""
        patcher_sys_stdin = mock.patch('sys.stdin.read', autospec=True)
        mocked_sys_stdin = patcher_sys_stdin.start()
        self.addCleanup(patcher_sys_stdin.stop)

        return mocked_sys_stdin

    def patch_sys_stderr_write(self) -> MagicMock:
        """Patch sys.stderr.write to check what was written."""
        patcher_sys_stderr = mock.patch("sys.stderr.write")
        mocked_sys_stderr = patcher_sys_stderr.start()
        self.addCleanup(patcher_sys_stderr.stop)

        return mocked_sys_stderr

    def patch_build_debusine_object(self) -> MagicMock:
        """
        Patch _build_debusine_object. Return mock.

        The mocked object return_value is a MagicMock(spec=Debusine).

        Keyword arguments correspond to methods of the resulting Debusine
        object to mock.
        """
        patcher = mock.patch.object(
            Cli, "_build_debusine_object", autospec=True
        )
        mocked = patcher.start()
        mock_debusine = mock.create_autospec(spec=Debusine)
        mock_debusine._logger = logging.getLogger("debusine.client.tests")
        mocked.return_value = mock_debusine
        self.addCleanup(patcher.stop)

        return mocked

    def get_base_url(self, server_name: str) -> str:
        """Return the base web URL for a given server."""
        split_server = urlsplit(self.servers[server_name]["api-url"])
        return urlunsplit(
            (split_server.scheme, split_server.netloc, "", "", "")
        )


class CliTests(BaseCliTests):
    """Tests for the debusine command line interface generic functionality."""

    def test_client_without_parameters(self) -> None:
        """
        Executing the client without any parameter returns an error.

        At least one subcommands is required. argparse prints help
        and exit.
        """
        cli = self.create_cli([], create_config=False)
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            with self.assertRaisesSystemExit(2):
                # Argparse prints help and exits with exit_code=2
                cli.execute()

        self.assertGreater(len(stderr.getvalue()), 80)

    def test_client_help_include_default_setting(self) -> None:
        """Cli.execute() help include ConfigHandler.DEFAULT_CONFIG_FILE_PATH."""
        stdout = io.StringIO()

        cli = self.create_cli(['--help'], create_config=False)

        with contextlib.redirect_stdout(stdout):
            with self.assertRaisesSystemExit(0):
                # Argparse prints help and exits with exit_code=0
                cli.execute()

        # argparse might add \n and spaces (for indentation) in the
        # output to align the text. In this case
        # ConfigHandler.DEFAULT_CONFIG_FILE_PATH could not be found
        output = stdout.getvalue().replace('\n', '').replace(' ', '')

        self.assertIn(
            str(ConfigHandler.DEFAULT_CONFIG_FILE_PATH).replace(' ', ''), output
        )

    def assert_client_object_use_specific_server(
        self, args: list[str], server_config: dict[str, str]
    ) -> None:
        """Assert that Cli uses Debusine with the correct endpoint."""
        cli = self.create_cli(args)

        cli._parse_args()

        debusine = cli._build_debusine_object()

        self.assertEqual(debusine.base_api_url, server_config['api-url'])
        self.assertEqual(debusine.token, server_config['token'])

    def test_use_default_server(self) -> None:
        """Ensure debusine object uses the default server."""
        self.assert_client_object_use_specific_server(
            ['show-work-request', '10'], self.servers[self.default_server]
        )

    def test_use_explicit_server(self) -> None:
        """Ensure debusine object uses the Kali server when requested."""
        self.assert_client_object_use_specific_server(
            ['--server', 'kali', 'show-work-request', '10'],
            self.servers["kali"],
        )

    def test_use_scope(self) -> None:
        """Ensure debusine object uses the default or specific scope."""
        cli = self.create_cli(["--server", "kali", "show-work-request", "10"])
        cli._parse_args()
        debusine = cli._build_debusine_object()
        self.assertEqual(
            debusine.scope,
            self.servers["kali"]["scope"],
        )

        cli = self.create_cli(
            [
                "--server",
                "kali",
                "--scope",
                "altscope",
                "show-work-request",
                "10",
            ]
        )
        cli._parse_args()
        debusine = cli._build_debusine_object()
        self.assertEqual(debusine.scope, "altscope")

    def test_no_server_found_by_fqdn_and_scope(self) -> None:
        """Cli fails if no matching server is found by FQDN/scope."""
        cli = self.create_cli(
            [
                "--server",
                "nonexistent.example.org/scope",
                "show-work-request",
                "10",
            ]
        )
        cli._parse_args()

        stderr, stdout = self.capture_output(
            cli._build_debusine_object, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr,
            "No Debusine client configuration for "
            "'nonexistent.example.org/scope'; "
            "run 'debusine setup' to configure it\n",
        )

    def test_no_server_found_by_name(self) -> None:
        """Cli fails if no matching server is found by name."""
        cli = self.create_cli(
            ["--server", "nonexistent", "show-work-request", "10"]
        )
        cli._parse_args()

        stderr, stdout = self.capture_output(
            cli._build_debusine_object, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr,
            "No Debusine client configuration for 'nonexistent'; "
            "run 'debusine setup' to configure it\n",
        )

    def test_build_debusine_object_logging_warning(self) -> None:
        """Cli with --silent create and pass logger level WARNING."""
        cli = self.create_cli(["--silent", "show-work-request", "10"])

        cli._parse_args()

        mocked_sys_stderr = self.patch_sys_stderr_write()

        debusine = cli._build_debusine_object()

        self.assertEqual(debusine._logger.level, logging.WARNING)
        self.assertFalse(debusine._logger.propagate)

        msg = "This is a test"

        # Test the logger
        debusine._logger.warning(msg)
        mocked_sys_stderr.assert_called_with(msg + "\n")

    def test_build_debusine_object_logging_info(self) -> None:
        """Cli without --silent create and pass logger level INFO."""
        cli = self.create_cli(["show-work-request", "10"])

        cli._parse_args()

        debusine = cli._build_debusine_object()

        self.assertEqual(debusine._logger.level, logging.INFO)

    def test_debug(self) -> None:
        """Cli with --debug."""
        cli = self.create_cli(["--debug", "show-work-request", "10"])

        with mock.patch.object(cli, "_show_work_request", autospec=True):
            cli.execute()
        self.assertEqual(http.client.HTTPConnection.debuglevel, 1)

        # Test the logger
        msg = "This is a test"
        mocked_sys_stderr = self.patch_sys_stderr_write()
        # http.client uses the print builtin, and debusine.client.cli
        # monkey-patches that, but it doesn't have an official type
        # annotation.
        http.client.print(msg)  # type: ignore[attr-defined]
        mocked_sys_stderr.assert_called_with("DEBUG:requests:" + msg + "\n")

    def test_api_call_or_fail_not_found(self) -> None:
        """_api_call_or_fail print error message for not found and exit."""
        cli = self.create_cli([], create_config=False)

        def raiseNotFound() -> NoReturn:
            with cli._api_call_or_fail():
                raise NotFoundError("Not found")

        stderr, stdout = self.capture_output(
            raiseNotFound, assert_system_exit_code=3
        )

        self.assertEqual(stderr, "Not found\n")

    def test_api_call_or_fail_unexpected_error(self) -> None:
        """_api_call_or_fail print error message for not found and exit."""
        cli = self.create_cli([], create_config=False)

        def raiseUnexpectedResponseError() -> NoReturn:
            with cli._api_call_or_fail():
                raise exceptions.UnexpectedResponseError("Not available")

        stderr, stdout = self.capture_output(
            raiseUnexpectedResponseError, assert_system_exit_code=3
        )

        self.assertEqual(stderr, "Not available\n")

    def test_api_call_or_fail_client_forbidden_error(self) -> None:
        """
        _api_call_or_fail print error message and exit.

        ClientForbiddenError was raised.
        """
        cli = self.create_cli([], create_config=False)

        def raiseClientForbiddenError() -> NoReturn:
            with cli._api_call_or_fail():
                raise exceptions.ClientForbiddenError("Invalid token")

        stderr, stdout = self.capture_output(
            raiseClientForbiddenError, assert_system_exit_code=3
        )

        self.assertEqual(stderr, "Server rejected connection: Invalid token\n")

    def test_api_call_or_fail_client_connection_error(self) -> None:
        """
        _api_call_or_fail print error message and exit.

        ClientConnectionError was raised.
        """
        cli = self.create_cli([], create_config=False)

        def raiseClientConnectionError() -> None:
            with cli._api_call_or_fail():
                raise exceptions.ClientConnectionError("Connection refused")

        stderr, stdout = self.capture_output(
            raiseClientConnectionError, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr,
            'Error connecting to debusine: Connection refused\n',
        )

    def test_api_call_or_fail_success(self) -> None:
        """
        _api_call_or_fail print error message.

        No exception was raised.
        """
        cli = self.create_cli([], create_config=False)

        with cli._api_call_or_fail():
            data = "some data from the server"

        self.assertEqual(data, 'some data from the server')

    def test_signal_handlers_set_on_exec(self) -> None:
        """Test that the signal handlers are set on exec()."""
        cli = self.create_cli(["show-work-request", "1"])

        # Cli.__init__() is not changing the signal handlers for SIG{INT,TERM}
        self.assertEqual(
            signal.getsignal(signal.SIGINT), self.default_sigint_handler
        )
        self.assertEqual(
            signal.getsignal(signal.SIGTERM), self.default_sigterm_handler
        )

        patcher = mock.patch.object(cli, "_show_work_request", autospec=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        cli.execute()

        # cli.execute() changed the signal handlers for SIG{INT,TERM}
        self.assertEqual(signal.getsignal(signal.SIGINT), cli._exit)
        self.assertEqual(signal.getsignal(signal.SIGTERM), cli._exit)

    def test_exit_raise_system_exit_0(self) -> None:
        """Cli._exit raise SystemExit(0)."""
        with self.assertRaisesSystemExit(0):
            Cli._exit(signal.SIGINT, None)

    def test_print_yaml(self) -> None:
        """Cli._print_yaml prints yaml to stdout."""
        cli = self.create_cli([])
        data = {"message": "x" * 100}

        stderr, stdout = self.capture_output(cli._print_yaml, args=[data])

        self.assertEqual(stdout.count("\n"), 1)


class CliCreateArtifactTests(BaseCliTests):
    """Tests for the Cli functionality related to create artifacts."""

    def create_files_to_upload(self) -> list[Path]:
        """Create three files to upload in the temp directory."""
        return [
            self.create_temporary_file(prefix="pkg_1.0.dsc", contents=b"test"),
            self.create_temporary_file(
                prefix="pkg_1.0.tar.gz", contents=b"test2"
            ),
            self.create_temporary_file(prefix="empty.txt", contents=b""),
        ]

    def patch_create_artifact(self) -> MagicMock:
        """Patch Debusine.create_artifact."""
        patcher_create_artifact = mock.patch.object(
            Debusine, "artifact_create", autospec=True
        )
        mocked_create_artifact = patcher_create_artifact.start()

        self.addCleanup(patcher_create_artifact.stop)
        return mocked_create_artifact

    def assert_artifact_is_created(
        self,
        mocked_create_artifact: MagicMock,
        expected_workspace: str,
        expected_artifact_id: int,
        expected_files_to_upload_count: int,
        stdout: str,
        stderr: str,
        expected_expire_at: datetime | None = None,
        data_from_stdin: bool = False,
    ) -> LocalArtifact[Any]:
        """
        Assert that mocked_create_artifact was called only once and stdout.

        In stdout expects that CLI printed into stdout the information
        about the recently created artifact.

        Return LocalArtifact that would be sent to the server.

        :param mocked_create_artifact: mock to assert that was called only once.
        :param expected_workspace: expected created workspace.
        :param expected_artifact_id: expected created artifact id.
        :param expected_files_to_upload_count: expected files to upload.
        :param stdout: contains Cli.execute() output to verify expected output.
        :param stderr: contains Cli.execute() output to verify expected output.
        :param expected_expire_at: expected artifact expiry time.
        :param data_from_stdin: if True, input data came from stdin.
        """
        mocked_create_artifact.assert_called_once()

        base_url = self.get_base_url(self.default_server)
        scope = self.servers[self.default_server]["scope"]
        artifact_url = urljoin(
            base_url,
            f"{quote(scope)}/{quote(expected_workspace)}/"
            f"artifact/{expected_artifact_id}/",
        )

        expected = yaml.safe_dump(
            {
                "result": "success",
                "message": f"New artifact created: {artifact_url}",
                "artifact_id": expected_artifact_id,
                "files_to_upload": expected_files_to_upload_count,
                "expire_at": expected_expire_at,
            },
            sort_keys=False,
            width=math.inf,
        )
        if data_from_stdin:
            expected = f"---\n{expected}"

        self.assertEqual(stdout, expected)
        self.assertEqual(stderr, "")

        local_artifact = mocked_create_artifact.call_args[0][1]
        assert isinstance(local_artifact, LocalArtifact)

        return local_artifact

    def create_artifact_via_cli(
        self,
        category: str,
        *,
        workspace: str | None = None,
        files_to_upload: list[str] | None = None,
        upload_base_directory: Path | None = None,
        expire_at: datetime | None = None,
        data: bytes | None = None,
        data_file: Path | Literal["-"] | None = None,
        stdin: dict[str, Any] | None = None,
        create_artifact_return_value: ArtifactResponse | None = None,
        create_artifact_side_effect: Any = None,
        assert_system_exit_code: int | None = None,
    ) -> tuple[str, str, MagicMock]:
        """
        Call CLI create-artifact with the correct parameters.

        Patch Debusine.create_artifact before executing the command.

        :param category: category of the artifact.
        :param workspace: add ["--workspace", workspace] options.
        :param files_to_upload: list of file paths to upload.
        :param expire_at: datetime (for --expire-at option).
        :param data: data passed to the artifact (creates a temporary file
          and passes the temporary file path via --data).
        :param data_file: data file (for --data options).
        :param stdin: dump stdin into YAML and writes it into stdin.
        :param create_artifact_return_value: return value for the
          Debusine.create_artifact mock.
        :param create_artifact_side_effect: side effect for the
          Debusine.create_artifact mock.
        :param assert_system_exit_code: assert that the create-artifact
          command raises SystemExit(X).

        Return tuple with stderr, stdout and the patched
        Debusine.create_artifact
        """
        mocked_create_artifact = self.patch_create_artifact()

        if create_artifact_return_value is not None:
            mocked_create_artifact.return_value = create_artifact_return_value

        if create_artifact_side_effect is not None:
            mocked_create_artifact.side_effect = create_artifact_side_effect

        create_cli_options = [category]

        if workspace is not None:
            create_cli_options.extend(["--workspace", workspace])

        if expire_at is not None:
            create_cli_options.extend([f"--expire-at={expire_at}"])

        if data is not None:
            data_file_temp = self.create_temporary_file(
                prefix="create-artifact", contents=data
            )
            create_cli_options.extend(["--data", str(data_file_temp)])

        if data_file is not None:
            if data is not None:
                raise ValueError(
                    "'data_file' parameter cannot be used with 'data'"
                )  # pragma: no cover
            create_cli_options.extend(["--data", str(data_file)])

        if files_to_upload is not None:
            create_cli_options.extend(["--upload", *files_to_upload])

        if upload_base_directory is not None:
            create_cli_options.extend(
                ["--upload-base-directory", upload_base_directory.as_posix()]
            )

        if stdin is not None:
            mocked_sys_stdin_read = self.patch_sys_stdin_read()
            mocked_sys_stdin_read.return_value = yaml.safe_dump(stdin)

        cli = self.create_cli(["create-artifact", *create_cli_options])

        if assert_system_exit_code is None:
            stderr, stdout = self.capture_output(cli.execute)
        else:
            stderr, stdout = self.capture_output(
                cli.execute, assert_system_exit_code=assert_system_exit_code
            )

        return stderr, stdout, mocked_create_artifact

    def assert_create_artifact_success(
        self,
        files_to_upload: list[Path],
        upload_base_directory: Path | None = None,
        expire_at: datetime | None = None,
    ) -> None:
        """
        Cli parse the command line and calls Debusine.create_artifact.

        Verify that the correct files_to_upload has been used.
        """
        artifact_category = ArtifactCategory.WORK_REQUEST_DEBUG_LOGS
        artifact_id = 2
        workspace = "test"

        # Mock Debusine.upload_files: will be used to verify the arguments
        # that debusine client used to try to upload the files
        upload_files_patcher = mock.patch.object(
            Debusine, "upload_files", autospec=True
        )
        upload_files_patcher.start()
        self.addCleanup(upload_files_patcher.stop)

        # Prepare dictionary with the paths in the artifact and the
        # FileModelRequest objects
        paths_in_artifact_to_file_models = {}
        for file_to_upload in files_to_upload:
            if file_to_upload.is_absolute():
                local_file_path = file_to_upload
                artifact_path = file_to_upload.name
            else:
                local_file_path = Path(os.getcwd()).joinpath(file_to_upload)
                artifact_path = str(file_to_upload.name)

            paths_in_artifact_to_file_models[artifact_path] = local_file_path

        create_artifact_return_value = create_artifact_response(
            base_url=self.get_base_url(self.default_server),
            scope=self.servers[self.default_server]["scope"],
            id=artifact_id,
            workspace=workspace,
            files_to_upload=list(paths_in_artifact_to_file_models.keys()),
        )

        (
            stderr_actual,
            stdout_actual,
            mocked_create_artifact,
        ) = self.create_artifact_via_cli(
            artifact_category,
            workspace=workspace,
            create_artifact_return_value=create_artifact_return_value,
            files_to_upload=list(map(str, files_to_upload)),
            upload_base_directory=upload_base_directory,
            expire_at=expire_at,
        )

        # Assert the call happened as expected
        artifact_request = self.assert_artifact_is_created(
            mocked_create_artifact,
            workspace,
            artifact_id,
            len(paths_in_artifact_to_file_models),
            stdout_actual,
            stderr_actual,
            expire_at,
        )
        self.assertEqual(artifact_request.category, artifact_category)
        self.assertEqual(
            artifact_request.files, paths_in_artifact_to_file_models
        )

    def test_create_artifact_absolute_file_paths(self) -> None:
        """Test create an artifact uploading files by absolute path."""
        self.assert_create_artifact_success(self.create_files_to_upload())

    def test_create_artifact_relative_file_path(self) -> None:
        """Test create an artifact uploading a file by relative to cwd."""
        file_path = self.create_temporary_file(directory=os.getcwd())

        file_name = file_path.name

        self.assert_create_artifact_success([Path(file_name)])

    def test_create_artifact_relative_file_path_in_subdirectory(self) -> None:
        """Test create an artifact uploading a file from a subdir of cwd."""
        temp_directory = self.create_temporary_directory(directory=os.getcwd())
        file_path = temp_directory / "file1.data"
        file_path.write_bytes(b"test")

        self.assert_create_artifact_success(
            [file_path.relative_to(os.getcwd())]
        )

    def test_create_artifact_upload_base_directory(self) -> None:
        """Create artifact with an abs file path in a abs path directory."""
        temp_directory = self.create_temporary_directory()

        (file := temp_directory / "file1.data").write_bytes(b"")

        self.assert_create_artifact_success([file], temp_directory)

    def test_create_artifact_expire_at(self) -> None:
        """Test create an artifact uploading files by absolute path."""
        expire_at = datetime.now() + timedelta(days=1)
        self.assert_create_artifact_success(
            self.create_files_to_upload(), expire_at=expire_at
        )

    def test_create_artifact_debusine_upload_files_fail(self) -> None:
        """Debusine.create_artifact fails: cannot connect to the server."""
        files_to_upload = self.create_files_to_upload()

        upload_files_patcher = mock.patch.object(
            Debusine, "upload_files", autospec=True
        )
        mocked_upload_files = upload_files_patcher.start()

        # Debusine.upload_files cannot connect to the server
        mocked_upload_files.side_effect = exceptions.ClientConnectionError
        self.addCleanup(upload_files_patcher.stop)

        workspace = "workspace"
        create_artifact_return_value = create_artifact_response(
            base_url=self.get_base_url(self.default_server),
            scope=self.servers[self.default_server]["scope"],
            id=2,
            workspace=workspace,
            files_to_upload=list(map(lambda p: p.name, files_to_upload)),
        )

        stderr, stdout, _ = self.create_artifact_via_cli(
            ArtifactCategory.WORK_REQUEST_DEBUG_LOGS,
            workspace=workspace,
            create_artifact_return_value=create_artifact_return_value,
            files_to_upload=list(map(str, files_to_upload)),
            assert_system_exit_code=3,
        )
        self.assertTrue(stderr.startswith("Error connecting to debusine:"))

    def test_create_artifact_read_stdin(self) -> None:
        """Cli create an artifact reading the data from stdin."""
        artifact_id = 2
        artifact_category = ArtifactCategory.WORK_REQUEST_DEBUG_LOGS
        workspace = "default"
        data: dict[str, Any] = {}

        create_artifact_return_value = create_artifact_response(
            base_url=self.get_base_url(self.default_server),
            scope=self.servers[self.default_server]["scope"],
            id=artifact_id,
            workspace=workspace,
        )

        stderr, stdout, mocked_create_artifact = self.create_artifact_via_cli(
            artifact_category,
            workspace=workspace,
            create_artifact_return_value=create_artifact_return_value,
            stdin=data,
            data_file="-",
        )

        artifact_request = self.assert_artifact_is_created(
            mocked_create_artifact,
            workspace,
            artifact_id,
            0,
            stdout,
            stderr,
            data_from_stdin=True,
        )
        self.assertEqual(artifact_request.data, data)

    def test_create_artifact_invalid_data_file_type(self) -> None:
        """Cli try to create an artifact, data file does not contain a dict."""
        stderr, stdout, mocked_create_artifact = self.create_artifact_via_cli(
            ArtifactCategory.SOURCE_PACKAGE,
            data=b"some-text",
            assert_system_exit_code=3,
        )

        self.assertEqual(
            stderr, "Error: data must be a dictionary. It is: str\n"
        )

    def test_create_artifact_invalid_yaml_in_data_file(self) -> None:
        """Cli try to create an artifact, data file contains invalid YAML."""
        stderr, stdout, mocked_create_artifact = self.create_artifact_via_cli(
            ArtifactCategory.SOURCE_PACKAGE,
            data=b'"',
            assert_system_exit_code=3,
        )

        self.assertRegex(stderr, "^Error parsing YAML:")
        self.assertRegex(stderr, r"Fix the YAML data\n$")

    def test_create_artifact_cannot_read_data(self) -> None:
        """Cli try to create an artifact with a data file cannot be read."""
        data_file = "/tmp/debusine-test-file-does-not-exist"
        stderr, stdout, mocked_create_artifact = self.create_artifact_via_cli(
            ArtifactCategory.SOURCE_PACKAGE,
            data_file=Path(data_file),
            assert_system_exit_code=2,
        )

        # argparse will show an error message containing the file path
        # (the rest of the message is up to argparse)
        self.assertRegex(stderr, data_file)

    def test_create_artifact_without_workspace_success(self) -> None:
        """Cli try to create an artifact with workspace=None."""
        artifact_id = 2
        expected_workspace = "default"
        data = {
            "name": "name",
            "version": "1.0",
            "type": "dpkg",
            "dsc_fields": {},
        }
        files_to_upload: list[str] = []

        create_artifact_return_value = create_artifact_response(
            base_url=self.get_base_url(self.default_server),
            scope=self.servers[self.default_server]["scope"],
            id=artifact_id,
            workspace=expected_workspace,
            files_to_upload=files_to_upload,
        )
        artifact_category = ArtifactCategory.SOURCE_PACKAGE
        (
            stderr_actual,
            stdout_actual,
            mocked_create_artifact,
        ) = self.create_artifact_via_cli(
            artifact_category,
            data=yaml.safe_dump(data).encode("utf-8"),
            create_artifact_return_value=create_artifact_return_value,
        )

        self.assert_artifact_is_created(
            mocked_create_artifact,
            expected_workspace,
            artifact_id,
            len(files_to_upload),
            stdout_actual,
            stderr_actual,
        )

    def test_create_artifact_workspace_not_found(self) -> None:
        """Cli try to create an artifact for a non-existing workspace."""
        workspace_name = "does-not-exist"
        title_error = f'Workspace "{workspace_name}" cannot be found'

        create_artifact_side_effect = DebusineError({"title": title_error})

        (
            stderr_actual,
            stdout_actual,
            mocked_create_artifact,
        ) = self.create_artifact_via_cli(
            ArtifactCategory.WORK_REQUEST_DEBUG_LOGS,
            workspace=workspace_name,
            create_artifact_side_effect=create_artifact_side_effect,
        )

        stdout_expected = yaml.safe_dump(
            {"result": "failure", "error": {"title": title_error}},
            sort_keys=False,
        )

        self.assertEqual(stderr_actual, "")
        self.assertEqual(stdout_actual, stdout_expected)

    def test_create_artifact_with_two_files_of_same_name(self) -> None:
        """Cannot create an artifact with two files having the same path."""
        file_name = "file1"
        # Create a file to be uploaded in a directory
        directory_to_upload1 = self.create_temporary_directory()
        file_in_dir1 = Path(directory_to_upload1) / Path(file_name)
        file_in_dir1.write_bytes(b"test")

        # And create another file, same name, in another directory
        directory_to_upload2 = self.create_temporary_directory()
        file_in_dir2 = Path(directory_to_upload2) / Path(file_name)
        file_in_dir2.write_bytes(b"test")

        paths_to_upload = [str(file_in_dir1), str(file_in_dir2)]

        stderr, stdout, _ = self.create_artifact_via_cli(
            ArtifactCategory.WORK_REQUEST_DEBUG_LOGS,
            workspace="some-workspace",
            files_to_upload=paths_to_upload,
            assert_system_exit_code=3,
        )

        self.assertEqual(
            stderr,
            f"Cannot create artifact: File with the same path ({file_name}) "
            f"is already in the artifact "
            f'("{file_in_dir1}" and "{file_in_dir2}")\n',
        )

    def test_create_artifact_invalid_category(self) -> None:
        """Cli try to create an artifact with a category that does not exist."""
        stderr, stdout, mocked_create_artifact = self.create_artifact_via_cli(
            "missing:does-not-exist",
            assert_system_exit_code=2,
        )


class CliImportDebianArtifactTests(BaseCliTests):
    """
    Tests for the debian artifact import CLI.

    Mocking at the upload_artifact level.

    Remote sources are tested through the DGet test suite, so not repeated here.
    """

    def create_dsc(self, directory: Path | None = None) -> list[Path]:
        """
        Create a simple Debian source package in a temporary directory.

        Return a list of the created Paths. The first item is the dsc.
        """
        if not directory:
            directory = self.create_temporary_directory()
        orig = directory / "pkg_1.0.orig.tar.gz"
        orig.write_bytes(b"I'm a tarball")
        dsc = directory / "pkg_1.0.dsc"
        self.write_dsc_file(dsc, [orig], version="1.0")
        return [dsc, orig]

    def create_upload(
        self,
        source: bool,
        binary: bool,
        extra_dsc: bool = False,
        binnmu: bool = False,
    ) -> list[Path]:
        """
        Create a simple Debian upload in a temporary directory.

        Return a list of the created Paths. The first item is the changes.
        """
        directory = self.create_temporary_directory()
        files = []
        if source:
            files += self.create_dsc(directory)
        if extra_dsc:
            extra = directory / "foo_1.0.dsc"
            self.write_dsc_file(extra, [], version="1.0")
            files.append(extra)
        if binary:
            deb_version = "1.0"
            if binnmu:
                deb_version += "+b1"
            deb = directory / f"pkg_{deb_version}_all.deb"
            self.write_deb_file(deb, source_version="1.0")
            files.append(deb)
        changes = directory / "pkg.changes"
        self.write_changes_file(changes, files, version="1.0", binnmu=binnmu)
        return [changes] + files

    def patch_upload_artifact(self) -> MagicMock:
        """
        Patch Debusine.upload_artifact.

        Return different RemoteArtifacts for each request.
        """
        patcher_upload_artifact = mock.patch.object(
            Debusine, "upload_artifact", autospec=True
        )
        mocked_upload_artifact = patcher_upload_artifact.start()

        id_counter = count(10)

        def upload_artifact(
            _self: Debusine,
            local_artifact: LocalArtifact[Any],  # noqa: U100
            *,
            workspace: str | None,  # noqa: U100
            expire_at: datetime | None,  # noqa: U100
        ) -> RemoteArtifact:
            if workspace is None:  # pragma: no cover
                workspace = "System"
            artifact_id = next(id_counter)
            return create_remote_artifact(
                base_url=self.get_base_url(self.default_server),
                scope=self.servers[self.default_server]["scope"],
                id=artifact_id,
                workspace=workspace,
            )

        mocked_upload_artifact.side_effect = upload_artifact

        self.addCleanup(patcher_upload_artifact.stop)
        return mocked_upload_artifact

    def patch_relation_create(self) -> MagicMock:
        """Patch Debusine.relation_create."""
        patcher_relation_create = mock.patch.object(
            Debusine, "relation_create", autospec=True
        )
        mocked_relation_create = patcher_relation_create.start()
        self.addCleanup(patcher_relation_create.stop)
        return mocked_relation_create

    def assert_artifacts_are_uploaded(
        self,
        mocked_upload_artifact: MagicMock,
        expected_workspace: str,
        expected_artifacts: int,
        expected_files: int,
        stdout: str,
        stderr: str,
        extended_artifacts: list[int] | None = None,
        related_artifacts: list[int] | None = None,
        expected_expire_at: datetime | None = None,
    ) -> list[LocalArtifact[Any]]:
        """
        Assert that mocked_upload_artifact uploaded expected_artifacts only.

        In stdout we expect that CLI printed the uploaded artifacts.

        Return LocalArtifacts that would have been uploaded.

        :param mocked_upload_artifact: mock of upload_artifact.
        :param expected_workspace: expected created workspace.
        :param expected_artifacts: expected number of artifacts created.
        :param expected_files: expected number of files uploaded.
        :param extended_artifacts: expected artifact IDs extending the main
            artifact.
        :param related_artifacts: expected artifact IDs relating to the main
            artifact.
        :param stdout: contains Cli.execute() output to verify expected output.
        :param stderr: contains Cli.execute() output to verify expected output.
        """
        self.assertEqual(mocked_upload_artifact.call_count, expected_artifacts)

        base_url = self.get_base_url(self.default_server)
        scope = self.servers[self.default_server]["scope"]
        artifact_url = urljoin(
            base_url, f"{quote(scope)}/{quote(expected_workspace)}/artifact/10/"
        )

        expected = {
            "result": "success",
            "message": f"New artifact created: {artifact_url}",
            "artifact_id": 10,
        }
        if extended_artifacts:
            expected["extends"] = [
                {"artifact_id": id_} for id_ in extended_artifacts
            ]
        if related_artifacts:
            expected["relates_to"] = [
                {"artifact_id": id_} for id_ in related_artifacts
            ]
        expected_str = yaml.safe_dump(
            expected,
            sort_keys=False,
            width=math.inf,
        )

        self.assertEqual(stdout, expected_str)
        self.assertEqual(stderr, "")

        local_artifacts = []
        uploaded_files = set()
        for args, kwargs in mocked_upload_artifact.call_args_list:
            self.assertEqual(len(args), 2)
            debusine, local_artifact = args
            self.assertEqual(kwargs["workspace"], expected_workspace)
            self.assertEqual(kwargs["expire_at"], expected_expire_at)
            self.assertIsInstance(local_artifact, LocalArtifact)
            for file in local_artifact.files:
                uploaded_files.add(file)
            local_artifacts.append(local_artifact)

        self.assertEqual(len(uploaded_files), expected_files)

        return local_artifacts

    def import_debian_artifact(
        self,
        *,
        workspace: str | None = None,
        upload: str | os.PathLike[str] | None = None,
        expire_at: datetime | None = None,
        upload_artifact_side_effect: (
            BaseException | type[BaseException] | None
        ) = None,
        assert_system_exit_code: int | None = None,
    ) -> tuple[str, str, MagicMock]:
        """
        Call CLI import-debian-artifact with the correct parameters.

        Patch Debusine.upload_artifact before executing the command.

        :param workspace: add ["--workspace", workspace] options.
        :param upload: file path / URL to upload.
        :param expire_at: datetime (for --expire-at option).
        :param upload_artifact_side_effect: side effect for the
          Debusine.create_artifact mock.
        :param assert_system_exit_code: assert that the import-debian-artifact
          command raises SystemExit(X).

        Return tuple with stderr, stdout and the patched
        Debusine.upload_artifact
        """
        mocked_upload_artifact = self.patch_upload_artifact()

        if upload_artifact_side_effect is not None:
            mocked_upload_artifact.side_effect = upload_artifact_side_effect

        args = []

        if workspace is not None:
            args += ["--workspace", workspace]

        if expire_at is not None:
            args += ["--expire-at", str(expire_at)]

        args.append(str(upload))

        cli = self.create_cli(["import-debian-artifact", *args])

        if assert_system_exit_code is None:
            stderr, stdout = self.capture_output(cli.execute)
        else:
            stderr, stdout = self.capture_output(
                cli.execute, assert_system_exit_code=assert_system_exit_code
            )

        return stderr, stdout, mocked_upload_artifact

    def assert_import_dsc(
        self,
        dsc: Path,
        files: list[Path],
        workspace: str = "test",
        expire_at: datetime | None = None,
    ) -> None:
        """
        Call import-debian-artifact to import a source package.

        Verify that the right artifacts are created and files uploaded.

        Return the uploaded artifacts.
        """
        # Make the request
        (
            stderr_actual,
            stdout_actual,
            mocked_upload_artifact,
        ) = self.import_debian_artifact(
            workspace=workspace,
            upload=str(dsc),
            expire_at=expire_at,
        )

        # Assert the call happened as expected
        artifacts = self.assert_artifacts_are_uploaded(
            mocked_upload_artifact,
            expected_workspace=workspace,
            expected_artifacts=1,
            expected_files=len(files),
            stdout=stdout_actual,
            stderr=stderr_actual,
            expected_expire_at=expire_at,
        )
        self.assertEqual(len(artifacts), 1)
        self.assertSourcePackage(artifacts[0])

    def assertSourcePackage(self, artifact: LocalArtifact[Any]) -> None:
        """Assert that artifact is a .dsc with expected metadata."""
        self.assertEqual(artifact.category, ArtifactCategory.SOURCE_PACKAGE)
        self.assertEqual(artifact.data.name, "hello-traditional")
        self.assertEqual(artifact.data.type, "dpkg")
        self.assertEqual(artifact.data.version, "1.0")
        self.assertEqual(
            artifact.data.dsc_fields["Source"], "hello-traditional"
        )
        self.assertEqual(
            set(artifact.files), {"pkg_1.0.dsc", "pkg_1.0.orig.tar.gz"}
        )

    def assertBinaryPackage(
        self, artifact: LocalArtifact[Any], binnmu: bool = False
    ) -> None:
        """Assert that artifact is a .deb with expected metadata."""
        self.assertEqual(artifact.category, ArtifactCategory.BINARY_PACKAGE)
        version = "1.0"
        if binnmu:
            version += "+b1"
        expected_deb_fields = {
            "Package": "pkg",
            "Version": version,
            "Architecture": "all",
            "Maintainer": "Example Maintainer <example@example.org>",
            "Description": "Example description",
        }
        if binnmu:
            expected_deb_fields["Source"] = "pkg (1.0)"
        expected = {
            "srcpkg_name": "pkg",
            "srcpkg_version": "1.0",
            "deb_fields": expected_deb_fields,
            "deb_control_files": ["control"],
        }
        self.assertEqual(artifact.data, expected)
        self.assertEqual(set(artifact.files), {f"pkg_{version}_all.deb"})

    def assertBinaryPackages(
        self, artifact: LocalArtifact[Any], binnmu: bool = False
    ) -> None:
        """Assert that artifact is a deb set with expected metadata."""
        self.assertEqual(artifact.category, ArtifactCategory.BINARY_PACKAGES)
        deb_version = "1.0"
        if binnmu:
            deb_version += "+b1"
        expected = {
            "srcpkg_name": "hello-traditional",
            "srcpkg_version": "1.0",
            "version": deb_version,
            "architecture": "all",
            "packages": ["pkg"],
        }
        self.assertEqual(artifact.data, expected)
        self.assertEqual(set(artifact.files), {f"pkg_{deb_version}_all.deb"})

    def assertUpload(
        self,
        artifact: LocalArtifact[Any],
        file_names: set[str],
        binnmu: bool = False,
    ) -> None:
        """Assert that artifact is an Upload with expected metadata."""
        self.assertEqual(artifact.category, ArtifactCategory.UPLOAD)
        self.assertEqual(artifact.data.type, "dpkg")
        expected = "hello-traditional"
        if binnmu:
            expected += " (1.0)"
        self.assertEqual(artifact.data.changes_fields["Source"], expected)
        self.assertEqual(set(artifact.files), file_names)

    def test_import_debian_artifact_dsc(self) -> None:
        """Test import a .dsc from a local path."""
        files = self.create_dsc()
        self.assert_import_dsc(files[0], files)

    def test_import_debian_artifact_expire_at(self) -> None:
        """Test import a .dsc from a local path, with expiry."""
        expire_at = datetime.now() + timedelta(days=1)
        files = self.create_dsc()
        self.assert_import_dsc(files[0], files, expire_at=expire_at)

    def test_import_debian_artifact_workspace(self) -> None:
        """Test import a .dsc from a local path, with a workspace."""
        files = self.create_dsc()
        self.assert_import_dsc(files[0], files, workspace="different")

    def test_import_debian_artifact_source_changes(self) -> None:
        """Test import a source upload from a local path."""
        files = self.create_upload(source=True, binary=False)
        workspace = "test"
        mocked_relation_create = self.patch_relation_create()
        stderr, stdout, mocked_upload_artifact = self.import_debian_artifact(
            upload=str(files[0]),
            workspace=workspace,
        )

        # Assert the call happened as expected
        artifacts = self.assert_artifacts_are_uploaded(
            mocked_upload_artifact,
            expected_workspace=workspace,
            expected_artifacts=2,
            expected_files=len(files),
            stdout=stdout,
            stderr=stderr,
            extended_artifacts=[11],
            related_artifacts=[11],
        )
        self.assertUpload(artifacts[0], {file.name for file in files})
        self.assertSourcePackage(artifacts[1])
        mocked_relation_create.assert_has_calls(
            [
                mock.call(
                    mock.ANY,
                    artifact_id=10,
                    target_id=11,
                    relation_type="extends",
                ),
                mock.call(
                    mock.ANY,
                    artifact_id=10,
                    target_id=11,
                    relation_type="relates-to",
                ),
            ]
        )

    def test_import_debian_artifact_binary_changes(self) -> None:
        """Test import a binary upload from a local path."""
        files = self.create_upload(source=False, binary=True)
        workspace = "test"
        mocked_relation_create = self.patch_relation_create()
        stderr, stdout, mocked_upload_artifact = self.import_debian_artifact(
            upload=str(files[0]),
            workspace=workspace,
        )

        # Assert the call happened as expected
        artifacts = self.assert_artifacts_are_uploaded(
            mocked_upload_artifact,
            expected_workspace=workspace,
            expected_artifacts=3,
            expected_files=len(files),
            stdout=stdout,
            stderr=stderr,
            extended_artifacts=[11, 12],
            related_artifacts=[11, 12],
        )
        self.assertUpload(artifacts[0], {file.name for file in files})
        self.assertBinaryPackage(artifacts[1])
        self.assertBinaryPackages(artifacts[2])
        mocked_relation_create.assert_has_calls(
            [
                mock.call(
                    mock.ANY,
                    artifact_id=10,
                    target_id=11,
                    relation_type="extends",
                ),
                mock.call(
                    mock.ANY,
                    artifact_id=10,
                    target_id=11,
                    relation_type="relates-to",
                ),
                mock.call(
                    mock.ANY,
                    artifact_id=10,
                    target_id=12,
                    relation_type="extends",
                ),
                mock.call(
                    mock.ANY,
                    artifact_id=10,
                    target_id=12,
                    relation_type="relates-to",
                ),
            ]
        )

    def test_import_debian_artifact_binnmu_changes(self) -> None:
        """Test import a binnmu binary upload from a local path."""
        files = self.create_upload(source=False, binary=True, binnmu=True)
        workspace = "test"
        self.patch_relation_create()
        stderr, stdout, mocked_upload_artifact = self.import_debian_artifact(
            upload=str(files[0]),
            workspace=workspace,
        )

        # Assert the call happened as expected
        artifacts = self.assert_artifacts_are_uploaded(
            mocked_upload_artifact,
            expected_workspace=workspace,
            expected_artifacts=3,
            expected_files=len(files),
            stdout=stdout,
            stderr=stderr,
            extended_artifacts=[11, 12],
            related_artifacts=[11, 12],
        )
        self.assertUpload(
            artifacts[0], {file.name for file in files}, binnmu=True
        )
        self.assertBinaryPackage(artifacts[1], binnmu=True)
        self.assertBinaryPackages(artifacts[2], binnmu=True)

    def test_import_debian_artifact_mixed_changes(self) -> None:
        """Test import a source+binary upload from a local path."""
        files = self.create_upload(source=True, binary=True)
        workspace = "test"
        self.patch_relation_create()
        stderr, stdout, mocked_upload_artifact = self.import_debian_artifact(
            upload=str(files[0]),
            workspace=workspace,
        )

        # Assert the call happened as expected
        artifacts = self.assert_artifacts_are_uploaded(
            mocked_upload_artifact,
            expected_workspace=workspace,
            expected_artifacts=4,
            expected_files=len(files),
            stdout=stdout,
            stderr=stderr,
            extended_artifacts=[11, 12, 13],
            related_artifacts=[11, 12, 13],
        )
        self.assertUpload(artifacts[0], {file.name for file in files})
        self.assertBinaryPackage(artifacts[1])
        self.assertBinaryPackages(artifacts[2])
        self.assertSourcePackage(artifacts[3])

    def test_import_debian_artifact_deb(self) -> None:
        """import-debian-artifact imports a .deb directly."""
        directory = self.create_temporary_directory()
        workspace = "test"
        deb = directory / "pkg_1.0_all.deb"
        self.write_deb_file(deb)
        stderr, stdout, mocked_create_artifact = self.import_debian_artifact(
            upload=deb,
            workspace=workspace,
        )
        # Assert the call happened as expected
        artifacts = self.assert_artifacts_are_uploaded(
            mocked_create_artifact,
            expected_workspace=workspace,
            expected_artifacts=1,
            expected_files=1,
            stdout=stdout,
            stderr=stderr,
        )
        self.assertBinaryPackage(artifacts[0])

    def test_import_debian_artifact_fails_to_connect(self) -> None:
        """Debusine.upload_artifact fails: cannot connect to the server."""
        files = self.create_dsc()

        stderr, stdout, mocked_upload_artifact = self.import_debian_artifact(
            workspace="test",
            upload=str(files[0]),
            upload_artifact_side_effect=exceptions.ClientConnectionError,
            assert_system_exit_code=3,
        )
        self.assertTrue(stderr.startswith("Error connecting to debusine:"))

    def test_import_debian_artifact_multiple_dsc(self) -> None:
        """import-debian-artifact fails when given two .dscs."""
        files = self.create_upload(source=True, binary=False, extra_dsc=True)
        stderr, stdout, mocked_create_artifact = self.import_debian_artifact(
            upload=files[0], assert_system_exit_code=3
        )

        self.assertRegex(
            stderr, r"^Expecting exactly 1 \.dsc in source package, found 2$"
        )

    def test_import_debian_artifact_binary_changes_without_debs(self) -> None:
        """import-debian-artifact fails with a binary upload without debs."""
        changes = self.create_temporary_file(suffix=".changes")
        changes.write_bytes(
            textwrap.dedent(
                """\
                Format: 3.0 (quilt)
                Source: hello-traditional
                Binary: hello-traditional
                Version: 2.10-5
                Maintainer: Santiago Vila <sanvila@debian.org>
                Homepage: http://www.gnu.org/software/hello/
                Standards-Version: 4.3.0
                Architecture: all
                Package-List:
                 hello-traditional deb devel optional arch=any
                Files:
                """
            ).encode("utf-8")
        )
        stderr, stdout, mocked_create_artifact = self.import_debian_artifact(
            upload=changes, assert_system_exit_code=3
        )

        self.assertRegex(
            stderr,
            r"^Expecting at least one \.deb per arch in binary packages$",
        )

    def test_import_debian_artifact_unknown_extension(self) -> None:
        """import-debian-artifact fails when given a .foo file."""
        file = self.create_temporary_file(suffix=".foo")
        stderr, stdout, mocked_create_artifact = self.import_debian_artifact(
            upload=file, assert_system_exit_code=3
        )

        self.assertEqual(
            stdout,
            (
                "result: failure\n"
                "error: Only source packages (.dsc), binary packages (.deb), "
                "and source/binary uploads (.changes) can be directly "
                f"imported with this command. {file} is not supported.\n"
            ),
        )

    def test_import_debian_artifact_missing_file(self) -> None:
        """import-debian-artifact fails when a named file is missing."""
        file = "/tmp/nonexistent.dsc"
        stderr, stdout, mocked_create_artifact = self.import_debian_artifact(
            upload=file, assert_system_exit_code=3
        )
        self.assertEqual(
            stdout,
            (
                "result: failure\n"
                "error: '[Errno 2] No such file or directory: "
                "''/tmp/nonexistent.dsc'''\n"
            ),
        )

    def test_import_debian_artifact_missing_referenced_file(self) -> None:
        """import-debian-artifact fails when a referenced file is missing."""
        files = self.create_dsc()
        files[1].unlink()
        stderr, stdout, mocked_create_artifact = self.import_debian_artifact(
            upload=files[0], assert_system_exit_code=3
        )
        stdout_expected = yaml.safe_dump(
            {
                "result": "failure",
                "error": f'"{files[1]}" does not exist',
            },
            sort_keys=False,
        )
        self.assertEqual(stdout, stdout_expected)

    def test_import_debian_artifact_workspace_not_found(self) -> None:
        """import-debian-artifact fails when the named workspace is missing."""
        workspace_name = "does-not-exist"
        title_error = f'Workspace "{workspace_name}" cannot be found'
        files = self.create_dsc()

        stderr, stdout, mocked_upload_artifact = self.import_debian_artifact(
            workspace="test",
            upload=str(files[0]),
            upload_artifact_side_effect=DebusineError({"title": title_error}),
            assert_system_exit_code=3,
        )

        stdout_expected = yaml.safe_dump(
            {"result": "failure", "error": {"title": title_error}},
            sort_keys=False,
        )

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, stdout_expected)


class CliCreateAssetTests(BaseCliTests):
    """Tests for the Cli functionality related to creating assets."""

    def patch_asset_create(self) -> MagicMock:
        """Patch Debusine.asset_create."""
        patcher_asset_create = mock.patch.object(
            Debusine, "asset_create", autospec=True
        )
        mocked_asset_create = patcher_asset_create.start()

        self.addCleanup(patcher_asset_create.stop)
        return mocked_asset_create

    def assert_create_signing_asset(
        self,
        use_stdin: bool = True,
        simulate_failure: bool = False,
    ) -> None:
        """Create a debusine:signing-key asset using the CLI."""
        category = AssetCategory.SIGNING_KEY
        asset_id = 2
        workspace = "test"
        data = SigningKeyData(
            purpose=KeyPurpose.OPENPGP,
            fingerprint="ABC123",
            public_key=(
                "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n"
                "-----END PGP PUBLIC KEY BLOCK-----\n"
            ),
            description="A Description",
        )

        mocked_asset_create = self.patch_asset_create()

        assert_system_exit_code: int | None = None
        if simulate_failure:
            mocked_asset_create.side_effect = DebusineError(
                {
                    "title": "Workspace not found",
                    "detail": "Workspace test not found in scope debusine",
                }
            )
            assert_system_exit_code = 3
        else:
            mocked_asset_create.return_value = AssetResponse(
                id=asset_id,
                data=data,
                category=category,
                workspace=workspace,
            )

        create_cli_options = [category, "--workspace", workspace]

        yaml_data = yaml.safe_dump(data.dict()).encode("UTF-8")
        if use_stdin:
            mocked_sys_stdin_read = self.patch_sys_stdin_read()
            mocked_sys_stdin_read.return_value = yaml_data
            create_cli_options.extend(["--data", "-"])
        else:
            data_file_temp = self.create_temporary_file(
                prefix="create-asset",
                contents=yaml_data,
            )
            create_cli_options.extend(["--data", str(data_file_temp)])

        cli = self.create_cli(["create-asset", *create_cli_options])

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=assert_system_exit_code
        )

        mocked_asset_create.assert_called_once_with(
            mock.ANY, category=category, data=data, workspace=workspace
        )

        server_url = self.servers[self.default_server]["api-url"]

        if simulate_failure:
            expected_data = {
                "result": "failure",
                "error": {
                    "title": "Workspace not found",
                    "detail": "Workspace test not found in scope debusine",
                },
            }
        else:
            expected_data = {
                "result": "success",
                "message": (
                    f"New asset created in {server_url} in workspace "
                    f"{workspace} with id {asset_id}."
                ),
            }
        expected = yaml.safe_dump(
            expected_data, sort_keys=False, width=math.inf
        )
        if use_stdin:
            expected = f"---\n{expected}"

        self.assertEqual(stdout, expected)
        self.assertEqual(stderr, "")

    def test_create_signing_asset_stdin(self) -> None:
        """Test create an asset with stdin YAML data."""
        self.assert_create_signing_asset(use_stdin=True)

    def test_create_signing_asset_file(self) -> None:
        """Test create an asset with YAML data in a file."""
        self.assert_create_signing_asset(use_stdin=False)

    def test_create_signing_asset_existing(self) -> None:
        """Test create an asset with YAML data in a file."""
        self.assert_create_signing_asset(simulate_failure=True)


class CliListAssetTests(BaseCliTests):
    """Tests for the cli functionality related to listing assets."""

    def patch_asset_list(self) -> MagicMock:
        """Patch Debusine.asset_list."""
        patcher_asset_list = mock.patch.object(
            Debusine, "asset_list", autospec=True
        )
        mocked_asset_list = patcher_asset_list.start()

        self.addCleanup(patcher_asset_list.stop)
        return mocked_asset_list

    def assert_list_signing_assets(
        self,
        args: list[str],
        expected_call: mock._Call,
        simulate_failure: bool = False,
    ) -> None:
        """List assets using the CLI."""
        mocked_asset_list = self.patch_asset_list()
        asset_response = AssetResponse(
            id=2,
            category=AssetCategory.SIGNING_KEY,
            workspace="test",
            data=SigningKeyData(
                purpose=KeyPurpose.OPENPGP,
                fingerprint="ABC123",
                public_key=(
                    "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n"
                    "-----END PGP PUBLIC KEY BLOCK-----\n"
                ),
                description="A Description",
            ),
            work_request=12,
        ).dict()
        assert_system_exit_code: int | None = None
        if simulate_failure:
            mocked_asset_list.side_effect = DebusineError(
                {
                    "title": "Workspace not found",
                    "detail": "Workspace test not found in scope debusine",
                }
            )
            assert_system_exit_code = 3
        else:
            mocked_asset_list.return_value = AssetsResponse.parse_obj(
                [asset_response]
            )

        cli = self.create_cli(["list-assets", *args])

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=assert_system_exit_code
        )

        mocked_asset_list.assert_has_calls([expected_call])
        self.assertEqual(len(mocked_asset_list.mock_calls), 1)

        expected_data: list[dict[str, Any]] | dict[str, Any]
        if simulate_failure:
            expected_data = {
                "result": "failure",
                "error": {
                    "title": "Workspace not found",
                    "detail": "Workspace test not found in scope debusine",
                },
            }
        else:
            expected_data = [asset_response]
        expected = yaml.safe_dump(
            expected_data, sort_keys=False, width=math.inf
        )

        self.assertEqual(stdout, expected)
        self.assertEqual(stderr, "")

    def test_list_assets_by_id(self) -> None:
        """Test list an asset by ID."""
        self.assert_list_signing_assets(
            args=["--id", "2"],
            expected_call=mock.call(
                mock.ANY, asset_id=2, work_request=None, workspace=None
            ),
        )

    def test_list_assets_by_workspace(self) -> None:
        """Test list an asset by workspace."""
        self.assert_list_signing_assets(
            args=["--workspace", "test"],
            expected_call=mock.call(
                mock.ANY, asset_id=None, work_request=None, workspace="test"
            ),
        )

    def test_list_assets_by_work_request(self) -> None:
        """Test list an asset by work_request."""
        self.assert_list_signing_assets(
            args=["--work-request", "12"],
            expected_call=mock.call(
                mock.ANY, asset_id=None, work_request=12, workspace=None
            ),
        )

    def test_list_assets_failure(self) -> None:
        """Test list an asset by work_request."""
        self.assert_list_signing_assets(
            args=["--work-request", "12"],
            expected_call=mock.call(
                mock.ANY, asset_id=None, work_request=12, workspace=None
            ),
            simulate_failure=True,
        )

    def test_list_assets_unfiltered(self) -> None:
        cli = self.create_cli(["list-assets"])

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )
        self.assertEqual(
            stderr,
            (
                "At least one of --id, --workspace, and --work-request must "
                "be specified.\n"
            ),
        )


class CliCreateWorkRequestTests(BaseCliTests):
    """Tests for the Cli functionality related to create work requests."""

    def patch_work_request_create(self) -> MagicMock:
        """
        Patch Debusine.work_request_create.

        Does not set return_value / side_effect.
        """
        patcher_work_request_create = mock.patch.object(
            Debusine, 'work_request_create', autospec=True
        )
        mocked_work_request_create = patcher_work_request_create.start()
        self.addCleanup(patcher_work_request_create.stop)

        return mocked_work_request_create

    def test_create_work_request_invalid_task_name(self) -> None:
        """Cli parse the CLI and stdin to create a WorkRequest bad task_name."""
        mocked_work_request_create = self.patch_work_request_create()
        task_name = "task-name"
        mocked_work_request_create.side_effect = DebusineError(
            {"title": f"invalid {task_name}"}
        )

        mocked_sys_stdin_read = self.patch_sys_stdin_read()

        mocked_sys_stdin_read.return_value = (
            'distribution: jessie\npackage: http://..../package_1.2-3.dsc\n'
        )

        cli = self.create_cli(['create-work-request', task_name])

        stderr, stdout = self.capture_output(cli.execute)

        expected = yaml.safe_dump(
            {
                "result": "failure",
                "error": {"title": f"invalid {task_name}"},
            },
            sort_keys=False,
        )
        self.assertEqual(stdout, f"---\n{expected}")

    def assert_create_work_request(
        self,
        cli: Cli,
        mocked_post_work_request: MagicMock,
        work_request_request: WorkRequestRequest,
        data_from_stdin: bool = False,
    ) -> None:
        """
        Call cli.execute() and assert the work request is created.

        Assert expected stderr/stdout and network call.

        :param data_from_stdin: if True, input data came from stdin.
        """
        stderr, stdout = self.capture_output(cli.execute)

        base_url = self.get_base_url(self.default_server)
        scope = self.servers[self.default_server]["scope"]
        workspace = work_request_request.workspace or "Testing"
        work_request_id = mocked_post_work_request.return_value.id
        work_request_url = urljoin(
            base_url,
            f"{quote(scope)}/{quote(workspace)}/"
            f"work-request/{work_request_id}/",
        )

        expected = yaml.safe_dump(
            {
                'result': 'success',
                'message': f'Work request registered: {work_request_url}',
                'work_request_id': work_request_id,
            },
            sort_keys=False,
        )
        if data_from_stdin:
            expected = f"---\n{expected}"
        self.assertEqual(stdout, expected)
        self.assertEqual(stderr, "")

        actual_work_request_request = mocked_post_work_request.mock_calls[0][1][
            1
        ]

        # Verify WorkRequestRequest is as expected
        self.assertEqual(actual_work_request_request, work_request_request)

    def verify_create_work_request_scenario(
        self,
        cli_args: list[str],
        workspace: str | None = None,
        event_reactions: dict[str, Any] = {},
        use_stdin: bool = False,
    ) -> None:
        """
        Test create-work-request using workspace/stdin/stdin-data.

        :param cli_args: first parameter is task_name. create-work-request
          is prepended to them.
        :param workspace: workspace to use or None if not specified in the
          cli_args
        :param use_stdin: to pass the generated data: use_stdin or a file.
        """
        task_name = cli_args[0]

        mocked_post_work_request = self.patch_work_request_create()
        work_request_id = 11
        data = {
            "distribution": "jessie",
            "package": "http://..../package_1.2-3.dsc",
        }
        mocked_post_work_request.return_value = create_work_request_response(
            base_url=self.get_base_url(self.default_server),
            scope=self.servers[self.default_server]["scope"],
            id=work_request_id,
        )

        serialized_data = yaml.safe_dump(data)

        if use_stdin:
            mocked_sys_stdin_read = self.patch_sys_stdin_read()
            mocked_sys_stdin_read.return_value = serialized_data
            data_option = "-"
        else:
            data_file = self.create_temporary_file(
                contents=serialized_data.encode("utf-8")
            )
            data_option = str(data_file)

        if event_reactions:
            event_reactions_file = self.create_temporary_file(
                contents=yaml.safe_dump(event_reactions).encode("utf-8")
            )
            cli_args += ["--event-reactions", str(event_reactions_file)]

        cli = self.create_cli(
            ["create-work-request"] + cli_args + (["--data", data_option])
        )

        expected_work_request_request = WorkRequestRequest(
            task_name=task_name,
            workspace=workspace,
            task_data=data,
            event_reactions=event_reactions,
        )
        self.assert_create_work_request(
            cli,
            mocked_post_work_request,
            expected_work_request_request,
            data_from_stdin=use_stdin,
        )

    def test_create_work_request_specific_workspace(self) -> None:
        """Cli parse the command line and use workspace."""
        workspace_name = "Testing"
        args = ["sbuild", "--workspace", workspace_name]
        self.verify_create_work_request_scenario(args, workspace=workspace_name)

    def test_create_work_request_event_reactions(self) -> None:
        """Cli parse the command line and provide even_reactions."""
        args = ["sbuild"]
        self.verify_create_work_request_scenario(
            args, event_reactions={'on_success': [{'send-notifications': {}}]}
        )

    def test_create_work_request_success_data_from_file(self) -> None:
        """Cli parse the command line and read file to create a work request."""
        args = ["sbuild"]
        self.verify_create_work_request_scenario(args)

    def test_create_work_request_success_data_from_stdin(self) -> None:
        """Cli parse the command line and stdin to create a work request."""
        args = ["sbuild"]
        self.verify_create_work_request_scenario(args, use_stdin=True)

    def test_create_work_request_data_is_empty(self) -> None:
        """Cli parse command line to create a work request with empty data."""
        empty_file = self.create_temporary_file()
        cli = self.create_cli(
            ["create-work-request", "sbuild", "--data", str(empty_file)]
        )

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr, "Error: data must be a dictionary. It is empty\n"
        )
        self.assertEqual(stdout, "")

    def test_create_work_request_yaml_errors_failed(self) -> None:
        """cli.execute() deal with different invalid task_data."""
        work_requests = [
            {
                "task_data": "test:\n  name: a-name\n"
                "    first-name: some first name",
                "comment": "yaml.safe_load raises ScannerError",
            },
            {
                "task_data": "input:\n  source_url: https://example.com\n )",
                "comment": "yaml.safe_load raises ParserError",
            },
        ]

        mocked_sys_stdin = self.patch_sys_stdin_read()

        for work_request in work_requests:
            task_data = work_request["task_data"]
            with self.subTest(task_data):
                mocked_sys_stdin.return_value = task_data

                cli = self.create_cli(["create-work-request", "task-name"])
                stderr, stdout = self.capture_output(
                    cli.execute, assert_system_exit_code=3
                )

                self.assertRegex(stderr, "^Error parsing YAML:")
                self.assertRegex(
                    stderr,
                    "Fix the YAML data\n$",
                )


class CliManageWorkRequestTests(BaseCliTests):
    """Tests for CLI manage-work-request subcommand."""

    def test_no_changes(self) -> None:
        """manage-work-request fails if no changes are specified."""
        cli = self.create_cli(["manage-work-request", "1"])

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )

        self.assertEqual(stderr, "Error: no changes specified\n")
        self.assertEqual(stdout, "")

    def test_set_priority_adjustment(self) -> None:
        """manage-work-request sets the priority adjustment."""
        debusine = self.patch_build_debusine_object()
        cli = self.create_cli(
            ["manage-work-request", "--set-priority-adjustment", "100", "1"]
        )

        stderr, stdout = self.capture_output(cli.execute)

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")
        debusine.return_value.work_request_update.assert_called_once_with(
            1, priority_adjustment=100
        )


class CliRetryWorkRequestTests(BaseCliTests):
    """Tests for CLI retry-work-request subcommand."""

    def test_retry(self) -> None:
        """retry-work-request calls the retry method."""
        debusine = self.patch_build_debusine_object()
        cli = self.create_cli(["retry-work-request", "1"])

        stderr, stdout = self.capture_output(cli.execute)

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")
        debusine.return_value.work_request_retry.assert_called_once_with(
            1,
        )


class CliAbortWorkRequestTests(BaseCliTests):
    """Tests for CLI abort-work-request subcommand."""

    def test_abort(self) -> None:
        """abort-work-request calls the abort method."""
        debusine = self.patch_build_debusine_object()
        cli = self.create_cli(["abort-work-request", "1"])

        stderr, stdout = self.capture_output(cli.execute)

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")
        debusine.return_value.work_request_abort.assert_called_once_with(
            1,
        )


class CliWorkRequestListTests(BaseCliTests):
    """Tests for CLI list-work-requests method."""

    def setUp(self) -> None:
        """Set up test."""
        self.artifact = create_artifact_response(
            id=1,
        )
        super().setUp()

    def test_list_work_requests_success(self) -> None:
        """CLI lists work requests and prints them."""
        work_request_response_1 = create_work_request_response(id=1)
        work_request_response_2 = create_work_request_response(id=2)

        def work_request_iter(
            self: Debusine,
        ) -> Generator[WorkRequestResponse, None, None]:
            yield work_request_response_1
            yield work_request_response_2

        patcher = mock.patch.object(
            Debusine, "work_request_iter", autospec=True
        )
        mocked_work_request_iter = patcher.start()
        mocked_work_request_iter.side_effect = work_request_iter
        self.addCleanup(patcher.stop)

        cli = self.create_cli(["list-work-requests"])

        stderr, stdout = self.capture_output(cli.execute)

        expected = yaml.safe_dump(
            {
                "results": [
                    {
                        "id": work_request_response.id,
                        "url": (
                            f"https://example.com/debusine/"
                            f"{quote(work_request_response.workspace)}/"
                            f"work-request/{work_request_response.id}/"
                        ),
                        "created_at": work_request_response.created_at,
                        "started_at": work_request_response.started_at,
                        "completed_at": work_request_response.completed_at,
                        "duration": work_request_response.duration,
                        "status": work_request_response.status,
                        "result": work_request_response.result,
                        "worker": work_request_response.worker,
                        "task_type": work_request_response.task_type,
                        "task_name": work_request_response.task_name,
                        "task_data": work_request_response.task_data,
                        "dynamic_task_data": None,
                        "priority_base": 0,
                        "priority_adjustment": 0,
                        "artifacts": [],
                        "workspace": work_request_response.workspace,
                    }
                    for work_request_response in (
                        work_request_response_1,
                        work_request_response_2,
                    )
                ]
            },
            sort_keys=False,
        )
        self.assertEqual(stdout, expected)


class CliWorkRequestStatusTests(BaseCliTests):
    """Tests for the Cli functionality related to work request status."""

    def setUp(self) -> None:
        """Set up test."""
        self.artifact = create_artifact_response(
            id=1,
        )
        super().setUp()

    def artifact_get(
        self, debusine: None, artifact_id: int  # noqa: U100
    ) -> ArtifactResponse:
        """
        Return copy of self.artifact changing its id to artifact_id.

        :param debusine: unused, only to have the same function signature
          as Debusine.artifact_get() (so it can be used in a mock)
        :param artifact_id: if of the returned artifact.
        """
        artifact = self.artifact.copy()
        artifact.id = artifact_id
        return artifact

    def test_show_work_request_success(self) -> None:
        """Cli use Debusine to fetch a WorkRequest and prints it to stdout."""
        patcher = mock.patch.object(Debusine, 'work_request_get', autospec=True)
        created_at = datetime.now()
        mocked_task_status = patcher.start()
        work_request_id = 10
        artifact_ids = [1, 2]
        work_request_response = create_work_request_response(
            id=work_request_id,
            created_at=created_at,
            artifacts=artifact_ids,
            task_data={"architecture": "amd64"},
            status="running",
        )
        mocked_task_status.return_value = work_request_response
        self.addCleanup(patcher.stop)

        # Patch artifact_get
        artifact_get_patcher = mock.patch.object(
            Debusine, "artifact_get", autospec=True
        )
        artifact_get_mocked = artifact_get_patcher.start()
        artifact_get_mocked.side_effect = self.artifact_get
        self.addCleanup(artifact_get_patcher.stop)

        cli = self.create_cli(['show-work-request', str(work_request_id)])

        stderr, stdout = self.capture_output(cli.execute)

        expected_artifacts = []

        for artifact_id in artifact_ids:
            expected_artifacts.append(
                model_to_json_serializable_dict(
                    self.artifact_get(None, artifact_id)
                )
            )

        expected = yaml.safe_dump(
            {
                'id': work_request_response.id,
                "url": (
                    f"https://example.com/debusine/"
                    f"{quote(work_request_response.workspace)}/"
                    f"work-request/{work_request_response.id}/"
                ),
                'created_at': work_request_response.created_at,
                'started_at': work_request_response.started_at,
                'completed_at': work_request_response.completed_at,
                'duration': work_request_response.duration,
                'status': work_request_response.status,
                'result': work_request_response.result,
                'worker': work_request_response.worker,
                "task_type": work_request_response.task_type,
                'task_name': work_request_response.task_name,
                'task_data': work_request_response.task_data,
                "dynamic_task_data": None,
                "priority_base": work_request_response.priority_base,
                "priority_adjustment": (
                    work_request_response.priority_adjustment
                ),
                "artifacts": expected_artifacts,
                "workspace": work_request_response.workspace,
            },
            sort_keys=False,
        )
        self.assertEqual(stdout, expected)


class CliProvideSignatureTests(BaseCliTests):
    """Tests for the CLI `provide-signature` command."""

    @contextlib.contextmanager
    def patch_debusine_method(
        self, name: str, **kwargs: Any
    ) -> Generator[mock.MagicMock, None, None]:
        """Patch a method on :py:class:`Debusine`."""
        with mock.patch.object(
            Debusine, name, autospec=True, **kwargs
        ) as patcher:
            yield patcher

    @responses.activate
    def verify_provide_signature_scenario(
        self, local_changes: bool = False, extra_args: list[str] | None = None
    ) -> None:
        """Test a provide-signature scenario."""
        if extra_args is None:
            extra_args = []
        directory = self.create_temporary_directory()
        (tar := directory / "foo_1.0.tar.xz").write_text("tar")
        (dsc := directory / "foo_1.0.dsc").write_text("dsc")
        (buildinfo := directory / "foo_1.0_source.buildinfo").write_text(
            "buildinfo"
        )
        changes = directory / "foo_1.0_source.changes"
        self.write_changes_file(changes, [tar, dsc, buildinfo])
        bare_work_request_response = create_work_request_response(
            id=1, task_type="Wait", task_name="externaldebsign"
        )
        full_work_request_response = create_work_request_response(
            id=1,
            task_type="Wait",
            task_name="externaldebsign",
            dynamic_task_data={"unsigned_id": 2},
        )
        unsigned_artifact_response = create_artifact_response(
            id=2,
            files={
                path.name: create_file_response(
                    size=path.stat().st_size,
                    checksums={"sha256": calculate_hash(path, "sha256").hex()},
                    url=f"https://example.com/{path.name}",
                )
                for path in (tar, dsc, buildinfo, changes)
            },
        )
        remote_signed_artifact = create_remote_artifact(
            id=3, workspace="Testing"
        )
        args = ["provide-signature", "1"]
        if local_changes:
            args.extend(["--local-file", str(changes)])
        if extra_args:
            args.extend(["--", *extra_args])
        cli = self.create_cli(args)

        for path in (tar, dsc, buildinfo, changes):
            responses.add(
                responses.GET,
                f"https://example.com/{path.name}",
                body=path.read_bytes(),
            )

        with (
            self.patch_debusine_method(
                "work_request_get", return_value=bare_work_request_response
            ) as mock_work_request_get,
            self.patch_debusine_method(
                "work_request_external_debsign_get",
                return_value=full_work_request_response,
            ) as mock_work_request_external_debsign_get,
            self.patch_debusine_method(
                "artifact_get", return_value=unsigned_artifact_response
            ) as mock_artifact_get,
            mock.patch("subprocess.run") as mock_run,
            self.patch_debusine_method(
                "upload_artifact", return_value=remote_signed_artifact
            ) as mock_upload_artifact,
            self.patch_debusine_method(
                "work_request_external_debsign_complete"
            ) as mock_work_request_external_debsign_complete,
        ):
            stderr, stdout = self.capture_output(cli.execute)

            if local_changes:
                self.assertEqual(stderr, "")
            else:
                self.assertRegex(
                    stderr,
                    r"\A"
                    + "\n".join(
                        [
                            (
                                fr"Artifact file downloaded: "
                                fr".*/{re.escape(path.name)}"
                            )
                            for path in (dsc, buildinfo, changes)
                        ]
                    )
                    + r"\n\Z",
                )
            self.assertEqual(stdout, "")

            # Ensure that the CLI called debusine in the right sequence.
            # The mocks are arranged a bit awkwardly since we want to allow
            # the unmocked Debusine.download_artifact_file to be called.
            debusine_instance = mock_work_request_get.call_args[0][0]
            self.assertIsInstance(debusine_instance, Debusine)
            mock_work_request_get.assert_called_once_with(debusine_instance, 1)
            mock_work_request_external_debsign_get.assert_called_once_with(
                debusine_instance, 1
            )
            mock_artifact_get.assert_called_once_with(debusine_instance, 2)
            mock_run.assert_called_once_with(
                ["debsign", "--re-sign", changes.name, *extra_args],
                cwd=mock.ANY,
                check=True,
            )
            mock_upload_artifact.assert_called_once_with(
                debusine_instance, mock.ANY, workspace="Testing"
            )
            signed_local = mock_upload_artifact.call_args[0][1]
            self.assertEqual(signed_local.category, ArtifactCategory.UPLOAD)
            self.assertCountEqual(
                signed_local.files.keys(),
                [changes.name, dsc.name, buildinfo.name],
            )
            self.assertCountEqual(signed_local.remote_files.keys(), [tar.name])
            mock_work_request_external_debsign_complete.assert_called_once_with(
                debusine_instance,
                1,
                WorkRequestExternalDebsignRequest(signed_artifact=3),
            )
        if local_changes:
            for path in (tar, dsc, buildinfo, changes):
                url = f"https://example.com/{path.name}"
                responses.assert_call_count(url, 0)

    def test_debsign(self) -> None:
        """provide-signature calls debsign and posts the output."""
        self.verify_provide_signature_scenario()

    def test_debsign_local_changes(self) -> None:
        """provide-signature uses local .changes."""
        self.verify_provide_signature_scenario(local_changes=True)

    def test_debsign_with_args(self) -> None:
        """provide-signature calls debsign with args and posts the output."""
        self.verify_provide_signature_scenario(extra_args=["-kKEYID"])

    def test_wrong_work_request(self) -> None:
        """provide-signature only works for Wait/externaldebsign requests."""
        debusine = self.patch_build_debusine_object()
        debusine.return_value.work_request_get.return_value = (
            create_work_request_response(task_type="Wait", task_name="delay")
        )
        cli = self.create_cli(["provide-signature", "1"])

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr,
            "Don't know how to provide signature for Wait/delay work request\n",
        )

    def verify_provide_signature_error_scenario(
        self,
        error_regex: str,
        local_file: str | None = None,
        expect_size: dict[str, int] | None = None,
        expect_sha256: dict[str, str] | None = None,
        delete_dsc: bool = False,
    ) -> None:
        """Test a provide-signature failure scenario with local_file."""
        if expect_size is None:
            expect_size = {}
        if expect_sha256 is None:
            expect_sha256 = {}
        directory = self.create_temporary_directory()
        (tar := directory / "foo_1.0.tar.xz").write_text("tar")
        (dsc := directory / "foo_1.0.dsc").write_text("dsc")
        (buildinfo := directory / "foo_1.0_source.buildinfo").write_text(
            "buildinfo"
        )
        changes = directory / "foo_1.0_source.changes"
        self.write_changes_file(changes, [tar, dsc, buildinfo])
        bare_work_request_response = create_work_request_response(
            id=1, task_type="Wait", task_name="externaldebsign"
        )
        full_work_request_response = create_work_request_response(
            id=1,
            task_type="Wait",
            task_name="externaldebsign",
            dynamic_task_data={"unsigned_id": 2},
        )
        unsigned_artifact_response = create_artifact_response(
            id=2,
            files={
                path.name: create_file_response(
                    size=expect_size.get(path.name, path.stat().st_size),
                    checksums={
                        "sha256": expect_sha256.get(
                            path.name, calculate_hash(path, "sha256").hex()
                        )
                    },
                    url=f"https://example.com/{path.name}",
                )
                for path in (tar, dsc, buildinfo, changes)
            },
        )
        if delete_dsc:
            dsc.unlink()
        if local_file is None:
            local_file = str(changes)
        args = ["provide-signature", "1", "--local-file", local_file]
        cli = self.create_cli(args)

        with (
            self.patch_debusine_method(
                "work_request_get", return_value=bare_work_request_response
            ),
            self.patch_debusine_method(
                "work_request_external_debsign_get",
                return_value=full_work_request_response,
            ),
            self.patch_debusine_method(
                "artifact_get", return_value=unsigned_artifact_response
            ),
            mock.patch("subprocess.run"),
        ):
            stderr, stdout = self.capture_output(
                cli.execute, assert_system_exit_code=3
            )

        self.assertRegex(stderr, error_regex)

    def test_missing_local_changes(self) -> None:
        """provide-signature raises an error for missing --local-file."""
        self.verify_provide_signature_error_scenario(
            r"^--local-file 'nonexistent\.changes' does not exist\.$",
            local_file="nonexistent.changes",
        )

    def test_wrong_local_changes(self) -> None:
        """provide-signature raises an error for the wrong --local-file."""
        directory = self.create_temporary_directory()
        (local_file := directory / "different.changes").write_text("")
        self.verify_provide_signature_error_scenario(
            (
                r"^'[^']+\/different\.changes' is not part of artifact \d+\. "
                r"Expecting 'foo_1.0_source\.changes'$"
            ),
            local_file=str(local_file),
        )

    def test_missing_local_dsc(self) -> None:
        """provide-signature raises an error for missing indirect local-file."""
        self.verify_provide_signature_error_scenario(
            r"^'[^']+\/foo_1.0\.dsc' does not exist\.$",
            delete_dsc=True,
        )

    def test_local_dsc(self) -> None:
        """provide-signature raises an error for the wrong kind of file."""
        self.verify_provide_signature_error_scenario(
            r"^--local-file 'foo\.dsc' is not a \.changes file.$",
            local_file="foo.dsc",
        )

    def test_size_mismatch(self) -> None:
        """provide-signature verifies the size of local files."""
        self.verify_provide_signature_error_scenario(
            r"^'[^']+/foo_1\.0\.dsc' size mismatch \(expected 999 bytes\)$",
            expect_size={"foo_1.0.dsc": 999},
        )

    def test_hash_mismatch(self) -> None:
        """provide-signature verifies the hashes of local files."""
        self.verify_provide_signature_error_scenario(
            (
                r"^'[^']+/foo_1\.0\.dsc' hash mismatch "
                r"\(expected sha256 = abc123\)$"
            ),
            expect_sha256={"foo_1.0.dsc": "abc123"},
        )


class CliCreateWorkflowTemplateTests(BaseCliTests):
    """Tests for the CLI `create-workflow-template` command."""

    def test_invalid_task_name(self) -> None:
        """CLI fails if the task name is bad."""
        task_name = "task-name"

        mocked_sys_stdin_read = self.patch_sys_stdin_read()
        mocked_sys_stdin_read.return_value = "{}"

        cli = self.create_cli(["create-workflow-template", "test", task_name])

        with mock.patch.object(
            Debusine,
            "workflow_template_create",
            autospec=True,
            side_effect=DebusineError({"title": f"invalid {task_name}"}),
        ):
            stderr, stdout = self.capture_output(cli.execute)

        expected = yaml.safe_dump(
            {
                "result": "failure",
                "error": {"title": f"invalid {task_name}"},
            },
            sort_keys=False,
        )
        self.assertEqual(stdout, f"---\n{expected}")

    def assert_create_workflow_template(
        self,
        cli: Cli,
        mocked_workflow_template_create: MagicMock,
        workflow_template_request: WorkflowTemplateRequest,
        data_from_stdin: bool = False,
    ) -> None:
        """
        Call cli.execute() and assert the workflow template is created.

        Assert expected stderr/stdout and network call.

        :param data_from_stdin: if True, input data came from stdin.
        """
        stderr, stdout = self.capture_output(cli.execute)

        base_url = self.get_base_url(self.default_server)
        scope = self.servers[self.default_server]["scope"]
        workspace = workflow_template_request.workspace or "Testing"
        workflow_template_id = mocked_workflow_template_create.return_value.id
        workflow_template_name = (
            mocked_workflow_template_create.return_value.name
        )
        workflow_template_url = urljoin(
            base_url,
            f"{quote(scope)}/{quote(workspace)}/"
            f"workflow-template/{quote(workflow_template_name)}/",
        )

        expected = yaml.safe_dump(
            {
                "result": "success",
                "message": (
                    f"Workflow template registered: {workflow_template_url}"
                ),
                "workflow_template_id": workflow_template_id,
            },
            sort_keys=False,
        )
        if data_from_stdin:
            expected = f"---\n{expected}"
        self.assertEqual(stdout, expected)
        self.assertEqual(stderr, "")
        mocked_workflow_template_create.assert_called_once_with(
            mock.ANY, workflow_template_request
        )

    def verify_create_workflow_template_scenario(
        self,
        cli_args: list[str],
        workspace: str | None = None,
        priority: int = 0,
        use_stdin: bool = False,
    ) -> None:
        """
        Test create-workflow-template using workspace/stdin/stdin-data.

        :param cli_args: first and second parameters are template_name and
          task_name.  create-workflow-template is prepended to them.
        :param workspace: workspace to use, or None if not specified in the
          cli_args.
        :param priority: priority to use, or 0 if not specified in the
          cli_args.
        :param use_stdin: to pass the generated data: use_stdin or a file.
        """
        template_name = cli_args[0]
        task_name = cli_args[1]

        workflow_template_id = 11
        data = {"target_distribution": "bookworm"}

        serialized_data = yaml.safe_dump(data)

        if use_stdin:
            mocked_sys_stdin_read = self.patch_sys_stdin_read()
            mocked_sys_stdin_read.return_value = serialized_data
            data_option = "-"
        else:
            data_file = self.create_temporary_file(
                contents=serialized_data.encode("utf-8")
            )
            data_option = str(data_file)

        cli = self.create_cli(
            ["create-workflow-template"] + cli_args + (["--data", data_option])
        )

        expected_workflow_template_request = WorkflowTemplateRequest(
            name=template_name,
            task_name=task_name,
            workspace=workspace,
            task_data=data,
            priority=priority,
        )
        with mock.patch.object(
            Debusine,
            "workflow_template_create",
            autospec=True,
            return_value=create_workflow_template_response(
                base_url=self.get_base_url(self.default_server),
                scope=self.servers[self.default_server]["scope"],
                id=workflow_template_id,
            ),
        ) as mocked_workflow_template_create:
            self.assert_create_workflow_template(
                cli,
                mocked_workflow_template_create,
                expected_workflow_template_request,
                data_from_stdin=use_stdin,
            )

    def test_specific_workspace(self) -> None:
        """CLI parses the command line and uses --workspace."""
        workspace_name = "Testing"
        args = ["sbuild-test", "sbuild", "--workspace", workspace_name]
        self.verify_create_workflow_template_scenario(
            args, workspace=workspace_name
        )

    def test_success_data_from_file(self) -> None:
        """CLI creates a workflow template with data from a file."""
        args = ["sbuild-test", "sbuild"]
        self.verify_create_workflow_template_scenario(args)

    def test_success_data_from_stdin(self) -> None:
        """CLI creates a workflow template with data from stdin."""
        args = ["sbuild-test", "sbuild"]
        self.verify_create_workflow_template_scenario(args, use_stdin=True)

    def test_data_is_empty(self) -> None:
        """CLI rejects a workflow template with empty data."""
        empty_file = self.create_temporary_file()
        cli = self.create_cli(
            [
                "create-workflow-template",
                "sbuild-test",
                "sbuild",
                "--data",
                str(empty_file),
            ]
        )

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr, "Error: data must be a dictionary. It is empty\n"
        )
        self.assertEqual(stdout, "")

    def test_yaml_errors_failed(self) -> None:
        """cli.execute() deals with different invalid task_data."""
        workflow_templates = [
            {
                "task_data": (
                    "test:\n"
                    "  name: a-name\n"
                    "    first-name: some first name"
                ),
                "comment": "yaml.safe_load raises ScannerError",
            },
            {
                "task_data": "input:\n  source_url: https://example.com\n )",
                "comment": "yaml.safe_load raises ParserError",
            },
        ]

        mocked_sys_stdin = self.patch_sys_stdin_read()

        for workflow_template in workflow_templates:
            task_data = workflow_template["task_data"]
            with self.subTest(task_data):
                mocked_sys_stdin.return_value = task_data

                cli = self.create_cli(
                    ["create-workflow-template", "template-name", "task-name"]
                )
                stderr, stdout = self.capture_output(
                    cli.execute, assert_system_exit_code=3
                )

                self.assertRegex(stderr, "^Error parsing YAML:")
                self.assertRegex(stderr, "Fix the YAML data\n$")


class CliCreateWorkflowTests(BaseCliTests):
    """Tests for the CLI `create-workflow` command."""

    def test_invalid_template_name(self) -> None:
        """CLI fails if the template name is bad."""
        template_name = "template-name"

        mocked_sys_stdin_read = self.patch_sys_stdin_read()
        mocked_sys_stdin_read.return_value = "{}"

        cli = self.create_cli(["create-workflow", template_name])

        with mock.patch.object(
            Debusine,
            "workflow_create",
            autospec=True,
            side_effect=DebusineError({"title": f"invalid {template_name}"}),
        ):
            stderr, stdout = self.capture_output(cli.execute)

        expected = yaml.safe_dump(
            {
                "result": "failure",
                "error": {"title": f"invalid {template_name}"},
            },
            sort_keys=False,
        )
        self.assertEqual(stdout, f"---\n{expected}")

    def assert_create_workflow(
        self,
        cli: Cli,
        mocked_workflow_create: MagicMock,
        workflow_request: CreateWorkflowRequest,
        data_from_stdin: bool = False,
    ) -> None:
        """
        Call cli.execute() and assert the workflow template is created.

        Assert expected stderr/stdout and network call.

        :param data_from_stdin: if True, input data came from stdin.
        """
        stderr, stdout = self.capture_output(cli.execute)

        base_url = self.get_base_url(self.default_server)
        scope = self.servers[self.default_server]["scope"]
        workspace = workflow_request.workspace or "Testing"
        workflow_id = mocked_workflow_create.return_value.id
        workflow_url = urljoin(
            base_url,
            f"{quote(scope)}/{quote(workspace)}/work-request/{workflow_id}/",
        )

        expected = yaml.safe_dump(
            {
                "result": "success",
                "message": f"Workflow created: {workflow_url}",
                "workflow_id": workflow_id,
            },
            sort_keys=False,
        )
        if data_from_stdin:
            expected = f"---\n{expected}"
        self.assertEqual(stdout, expected)
        self.assertEqual(stderr, "")

        actual_workflow_request = mocked_workflow_create.mock_calls[0][1][1]

        # Verify CreateWorkflowRequest is as expected
        self.assertEqual(actual_workflow_request, workflow_request)

    def verify_create_workflow_scenario(
        self,
        cli_args: list[str],
        workspace: str | None = None,
        use_stdin: bool = False,
    ) -> None:
        """
        Test create-workflow using workspace/stdin/stdin-data.

        :param cli_args: first parameter is template_name.  create-workflow
          is prepended to them.
        :param workspace: workspace to use, or None if not specified in the
          cli_args.
        :param use_stdin: to pass the generated data: use_stdin or a file.
        """
        template_name = cli_args[0]

        workflow_id = 11
        data = {"input": {"source_artifact_id": 1}}

        serialized_data = yaml.safe_dump(data)

        if use_stdin:
            mocked_sys_stdin_read = self.patch_sys_stdin_read()
            mocked_sys_stdin_read.return_value = serialized_data
            data_option = "-"
        else:
            data_file = self.create_temporary_file(
                contents=serialized_data.encode("utf-8")
            )
            data_option = str(data_file)

        cli = self.create_cli(
            ["create-workflow"] + cli_args + (["--data", data_option])
        )

        expected_workflow_request = CreateWorkflowRequest(
            template_name=template_name, workspace=workspace, task_data=data
        )
        with mock.patch.object(
            Debusine,
            "workflow_create",
            autospec=True,
            return_value=create_work_request_response(
                base_url=self.get_base_url(self.default_server),
                scope=self.servers[self.default_server]["scope"],
                id=workflow_id,
            ),
        ) as mocked_workflow_create:
            self.assert_create_workflow(
                cli,
                mocked_workflow_create,
                expected_workflow_request,
                data_from_stdin=use_stdin,
            )

    def test_specific_workspace(self) -> None:
        """CLI parses the command line and uses --workspace."""
        workspace_name = "Testing"
        args = ["sbuild-amd64-arm64", "--workspace", workspace_name]
        self.verify_create_workflow_scenario(args, workspace=workspace_name)

    def test_success_data_from_file(self) -> None:
        """CLI creates a workflow with data from a file."""
        args = ["sbuild-amd64-arm64"]
        self.verify_create_workflow_scenario(args)

    def test_success_data_from_stdin(self) -> None:
        """CLI creates a workflow with data from stdin."""
        args = ["sbuild-amd64-arm64"]
        self.verify_create_workflow_scenario(args, use_stdin=True)

    def test_data_is_empty(self) -> None:
        """CLI creates a workflow with empty data."""
        empty_file = self.create_temporary_file()
        cli = self.create_cli(
            ["create-workflow", "sbuild-amd64-arm64", "--data", str(empty_file)]
        )

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr, "Error: data must be a dictionary. It is empty\n"
        )
        self.assertEqual(stdout, "")

    def test_yaml_errors_failed(self) -> None:
        """cli.execute() deals with different invalid task_data."""
        workflows = [
            {
                "task_data": (
                    "test:\n"
                    "  name: a-name\n"
                    "    first-name: some first name"
                ),
                "comment": "yaml.safe_load raises ScannerError",
            },
            {
                "task_data": "input:\n  source_url: https://example.com\n )",
                "comment": "yaml.safe_load raises ParserError",
            },
        ]

        mocked_sys_stdin = self.patch_sys_stdin_read()

        for workflow in workflows:
            task_data = workflow["task_data"]
            with self.subTest(task_data):
                mocked_sys_stdin.return_value = task_data

                cli = self.create_cli(["create-workflow", "workflow-name"])
                stderr, stdout = self.capture_output(
                    cli.execute, assert_system_exit_code=3
                )

                self.assertRegex(stderr, "^Error parsing YAML:")
                self.assertRegex(stderr, "Fix the YAML data\n$")


class CliDownloadArtifact(BaseCliTests):
    """Tests for the Cli functionality related to downloading an artifact."""

    def test_download_artifact(self) -> None:
        """download-artifact call debusine.download_artifact()."""
        artifact_id = str(10)
        destination = Path.cwd()

        debusine = self.patch_build_debusine_object()

        for args, tarball in (
            (["download-artifact", artifact_id, "--tarball"], True),
            (["download-artifact", artifact_id], False),
            (["-s", "download-artifact", artifact_id, "--tarball"], True),
            (["-s", "download-artifact", artifact_id], False),
        ):
            with self.subTest(args=args):
                cli = self.create_cli(args)

                cli.execute()

                debusine.return_value.download_artifact.assert_called_with(
                    int(artifact_id), destination=destination, tarball=tarball
                )

    def test_download_artifact_target_directory_no_write_access(self) -> None:
        """_download_artifact target_directory no write access."""
        # Similar as other tests: avoid building the debusine object
        # (not relevant for this test)
        self.patch_build_debusine_object()

        target_directory = self.create_temporary_directory()
        os.chmod(target_directory, 0)

        cli = self.create_cli(
            [
                "download-artifact",
                "10",
                "--target-directory",
                str(target_directory),
            ],
            create_config=False,
        )

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )

        self.assertEqual(stderr, f"Error: Cannot write to {target_directory}\n")
        self.assertEqual(stdout, "")


class CliShowArtifactTests(BaseCliTests):
    """Tests for Cli show-artifact."""

    def test_show_artifact(self) -> None:
        """Test show-artifact shows the information."""
        artifact_id = 11
        debusine = self.patch_build_debusine_object()
        debusine.return_value.artifact_get.return_value = (
            create_artifact_response(id=artifact_id)
        )
        cli = self.create_cli(["show-artifact", str(artifact_id)])

        with mock.patch.object(
            cli, "_api_call_or_fail", side_effect=cli._api_call_or_fail
        ) as api_call_or_fail_mocked:
            stderr, stdout = self.capture_output(cli.execute)

        api_call_or_fail_mocked.assert_called_once_with()
        debusine.return_value.artifact_get.assert_called_once_with(
            int(artifact_id)
        )

        self.assertEqual(stderr, "")

        _, expected_output = self.capture_output(
            Cli._print_yaml,
            [
                model_to_json_serializable_dict(
                    debusine.return_value.artifact_get.return_value
                )
            ],
        )

        self.assertEqual(stdout, expected_output)


class CliShowArtifactRelationsTests(BaseCliTests):
    """Tests for Cli show-artifact-relations."""

    def create_relation_response(self, **kwargs: Any) -> RelationResponse:
        """Return a RelationResponse. Use defaults for certain fields."""
        defaults: dict[str, Any] = {
            "id": 1,
            "artifact": 10,
            "target": 11,
            "type": "relates-to",
        }
        defaults.update(kwargs)

        return RelationResponse(**defaults)

    def create_relations_response(
        self, *args: RelationResponse
    ) -> RelationsResponse:
        """Return a RelationsResponse containing args."""
        return RelationsResponse(__root__=args)

    def test_show_artifact_relations(self) -> None:
        """Test show-artifact-relations shows all relations."""
        artifact_id = 11
        debusine = self.patch_build_debusine_object()
        debusine.return_value.relation_list.return_value = (
            self.create_relations_response(
                self.create_relation_response(
                    id=1, artifact=artifact_id, target=15
                ),
                self.create_relation_response(
                    id=2, artifact=artifact_id, target=16
                ),
            )
        )
        cli = self.create_cli(["show-artifact-relations", str(artifact_id)])

        with mock.patch.object(
            cli, "_api_call_or_fail", side_effect=cli._api_call_or_fail
        ) as api_call_or_fail_mocked:
            stderr, stdout = self.capture_output(cli.execute)

        api_call_or_fail_mocked.assert_called_once_with()
        debusine.return_value.relation_list.assert_called_once_with(
            artifact_id=int(artifact_id)
        )

        self.assertEqual(stderr, "")

        _, expected_output = self.capture_output(
            Cli._print_yaml,
            [
                [
                    {
                        "artifact": 11,
                        "target": 15,
                        "type": "relates-to",
                        "id": 1,
                    },
                    {
                        "artifact": 11,
                        "target": 16,
                        "type": "relates-to",
                        "id": 2,
                    },
                ]
            ],
        )

        self.assertEqual(stdout, expected_output)

    def test_show_artifact_relations_reverse(self) -> None:
        """Test show-artifact-relations shows reverse relations."""
        artifact_id = 11
        debusine = self.patch_build_debusine_object()
        debusine.return_value.relation_list.return_value = (
            self.create_relations_response(
                self.create_relation_response(
                    id=3, artifact=8, target=artifact_id
                ),
                self.create_relation_response(
                    id=4, artifact=9, target=artifact_id
                ),
            )
        )
        cli = self.create_cli(
            ["show-artifact-relations", str(artifact_id), "--reverse"]
        )

        with mock.patch.object(
            cli, "_api_call_or_fail", side_effect=cli._api_call_or_fail
        ) as api_call_or_fail_mocked:
            stderr, stdout = self.capture_output(cli.execute)

        api_call_or_fail_mocked.assert_called_once_with()
        debusine.return_value.relation_list.assert_called_once_with(
            target_id=int(artifact_id)
        )

        self.assertEqual(stderr, "")

        _, expected_output = self.capture_output(
            Cli._print_yaml,
            [
                [
                    {
                        "artifact": 8,
                        "target": 11,
                        "type": "relates-to",
                        "id": 3,
                    },
                    {
                        "artifact": 9,
                        "target": 11,
                        "type": "relates-to",
                        "id": 4,
                    },
                ]
            ],
        )

        self.assertEqual(stdout, expected_output)


class CliOnWorkRequestTests(BaseCliTests):
    """Tests for Cli on-work-request-completed."""

    def test_command_does_not_exist(self) -> None:
        """Cli report that command does not exist."""
        command = "/some/file/does/not/exist"
        cli = self.create_cli(["on-work-request-completed", command])

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr, f'Error: "{command}" does not exist or is not executable\n'
        )
        self.assertEqual(stdout, "")

    def test_command_is_not_executable(self) -> None:
        """Cli report that command is not executable."""
        command = self.create_temporary_file()
        cli = self.create_cli(["on-work-request-completed", str(command)])

        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr, f'Error: "{command}" does not exist or is not executable\n'
        )
        self.assertEqual(stdout, "")

    def debusine_on_work_request_completed_is_called(
        self,
        *,
        workspaces: list[str] | None = None,
        last_completed_at: Path | None = None,
        silent: bool | None = False,
        extra_args: list[str] | None = None,
    ) -> None:
        """Cli call debusine.on_work_request_completed."""
        command = self.create_temporary_file()
        command.chmod(stat.S_IXUSR | stat.S_IRUSR)

        debusine = self.patch_build_debusine_object()

        cli_args = []

        if silent:
            cli_args.extend(["--silent"])

        cli_args.extend(
            ["on-work-request-completed", str(command), *(extra_args or [])]
        )

        if workspaces:
            cli_args.extend(["--workspace", *workspaces])

        if last_completed_at:
            cli_args.extend(["--last-completed-at", str(last_completed_at)])

        cli = self.create_cli(cli_args)

        stderr, stdout = self.capture_output(cli.execute)

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")

        debusine.return_value.on_work_request_completed.assert_called_with(
            workspaces=workspaces,
            last_completed_at=last_completed_at,
            command=[str(command), *(extra_args or [])],
            working_directory=Path.cwd(),
        )

    def test_debusine_on_work_request_completed(self) -> None:
        """Cli call debusine.on_work_request_completed."""
        self.debusine_on_work_request_completed_is_called(silent=True)

    def test_debusine_on_work_request_completed_with_extra_args(self) -> None:
        self.debusine_on_work_request_completed_is_called(
            silent=True, extra_args=["foo", "bar"]
        )

    def test_debusine_on_work_request_completed_with_workspace(self) -> None:
        """Cli call debusine.on_work_request_completed."""
        self.debusine_on_work_request_completed_is_called(workspaces=["System"])

    def test_debusine_on_work_request_completed_create_last_completed(
        self,
    ) -> None:
        """
        Cli call debusine.on_work_request_completed.

        File last_completed_at is created.
        """
        last_completed_at = self.create_temporary_file()
        last_completed_at.unlink()

        self.debusine_on_work_request_completed_is_called(
            last_completed_at=last_completed_at
        )

        expected = json.dumps({"last_completed_at": None}, indent=2) + "\n"
        self.assertEqual(last_completed_at.read_text(), expected)

    def test_debusine_on_work_request_completed_last_completed_existed(
        self,
    ) -> None:
        """Cli call debusine.on_work_request_completed."""
        contents = json.dumps({"last_completed_at": None}).encode("utf-8")
        last_completed_at = self.create_temporary_file(contents=contents)

        self.debusine_on_work_request_completed_is_called(
            last_completed_at=last_completed_at
        )

    def test_debusine_on_work_request_last_completed_no_write_permission(
        self,
    ) -> None:
        """Cli cannot write to last-completed-at file."""
        cannot_write = self.create_temporary_file()
        cannot_write.chmod(0o444)
        cli = self.create_cli(
            [
                "on-work-request-completed",
                "/bin/echo",
                "--last-completed-at",
                str(cannot_write),
            ]
        )

        stderr, stdout = self.capture_output(
            cli.execute,
            assert_system_exit_code=3,
        )
        self.assertEqual(
            stderr,
            'Error: write or read access '
            f'denied for "{str(cannot_write)}"\n',
        )

    def test_debusine_on_work_request_last_completed_no_read_permission(
        self,
    ) -> None:
        """Cli cannot write to last-completed-at file."""
        cannot_write = self.create_temporary_file()
        cannot_write.chmod(0o333)
        cli = self.create_cli(
            [
                "on-work-request-completed",
                "/bin/echo",
                "--last-completed-at",
                str(cannot_write),
            ]
        )

        stderr, stdout = self.capture_output(
            cli.execute,
            assert_system_exit_code=3,
        )
        self.assertEqual(
            stderr,
            'Error: write or read access '
            f'denied for "{str(cannot_write)}"\n',
        )

    def test_debusine_on_work_request_dir_last_completed_no_permission(
        self,
    ) -> None:
        """Cli cannot write to last-completed-at directory."""
        directory = self.create_temporary_directory()
        directory.chmod(0o000)
        cli = self.create_cli(
            [
                "on-work-request-completed",
                "/bin/echo",
                "--last-completed-at",
                str(directory / "file.json"),
            ]
        )

        stderr, stdout = self.capture_output(
            cli.execute,
            assert_system_exit_code=3,
        )
        self.assertEqual(
            stderr,
            f'Error: write access denied for directory "{str(directory)}"\n',
        )


class CliSetupTests(BaseCliTests):
    """Tests for the setup command."""

    def assertRunsSuccessfully(self, cli: Cli) -> None:
        """Ensure cli runs successfully."""
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            cli.execute()
        self.assertEqual(stderr.getvalue(), "")

    def test_without_args(self) -> None:
        """Call setup without arguments."""
        with mock.patch("debusine.client.setup.setup_server") as setup_server:
            cli = self.create_cli(["setup"], create_config=False)
            self.assertRunsSuccessfully(cli)
        setup_server.assert_called_with(
            config_file_path=ConfigHandler.DEFAULT_CONFIG_FILE_PATH,
            server=None,
            scope=None,
        )

    def test_with_config(self) -> None:
        """Call setup with --config-file."""
        with mock.patch("debusine.client.setup.setup_server") as setup_server:
            cli = self.create_cli(
                ["--config-file=debusine.cfg", "setup"], create_config=False
            )
            self.assertRunsSuccessfully(cli)
        setup_server.assert_called_with(
            config_file_path="debusine.cfg", server=None, scope=None
        )

    def test_with_server(self) -> None:
        """Call setup with --server."""
        with mock.patch("debusine.client.setup.setup_server") as setup_server:
            cli = self.create_cli(
                ["--server=debian", "setup"], create_config=False
            )
            self.assertRunsSuccessfully(cli)
        setup_server.assert_called_with(
            config_file_path=ConfigHandler.DEFAULT_CONFIG_FILE_PATH,
            server="debian",
            scope=None,
        )

    def test_with_scope(self) -> None:
        """Call setup with --scope."""
        with mock.patch("debusine.client.setup.setup_server") as setup_server:
            cli = self.create_cli(
                ["--scope=debian", "setup"], create_config=False
            )
            self.assertRunsSuccessfully(cli)
        setup_server.assert_called_with(
            config_file_path=ConfigHandler.DEFAULT_CONFIG_FILE_PATH,
            server=None,
            scope="debian",
        )


class CliTaskConfigPullPushTests(BaseCliTests):
    """Tests for Cli task-config-pull and -push."""

    config1: ClassVar[DebusineTaskConfiguration]
    config2: ClassVar[DebusineTaskConfiguration]
    template: ClassVar[DebusineTaskConfiguration]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.config1 = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        cls.config2 = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            context="context",
            subject="subject",
        )
        cls.template = DebusineTaskConfiguration(
            template="template", comment="this is a template"
        )

    def setUp(self) -> None:
        super().setUp()
        # Working directory for the local collection
        self.workdir = self.create_temporary_directory()
        # Mocked debusine object
        build_debusine = self.patch_build_debusine_object()
        self.debusine = build_debusine.return_value

    def manifest(
        self,
        workspace: str = "System",
        pk: int = 42,
        name: str = "default",
        data: dict[str, Any] | None = None,
    ) -> RemoteTaskConfigurationRepository.Manifest:
        """Create a manifest."""
        return RemoteTaskConfigurationRepository.Manifest(
            workspace=workspace,
            collection=TaskConfigurationCollection(
                id=pk, name=name, data=data or {}
            ),
        )

    def make_remote_repo(
        self, workspace: str = "System", pk: int = 42, name: str = "default"
    ) -> RemoteTaskConfigurationRepository:
        """Create a TaskConfigurationRepository with manifest."""
        return RemoteTaskConfigurationRepository(
            self.manifest(workspace=workspace, pk=pk, name=name)
        )

    def mock_fetch(
        self, repo: RemoteTaskConfigurationRepository | None
    ) -> Mock:
        """Mock push_task_configuration_collection with a plausible return."""
        if repo is None:
            fetch = Mock(
                side_effect=AssertionError("fetch is not supposed to be called")
            )
        else:
            fetch = Mock(return_value=repo)
        self.debusine.fetch_task_configuration_collection = fetch
        return fetch

    def mock_push(
        self,
        added: int = 1,
        updated: int = 2,
        removed: int = 3,
        unchanged: int = 4,
    ) -> Mock:
        """Mock push_task_configuration_collection with a plausible return."""
        results = TaskConfigurationCollectionUpdateResults(
            added=added, updated=updated, removed=removed, unchanged=unchanged
        )
        push = Mock(return_value=results)
        self.debusine.push_task_configuration_collection = push
        return push

    def write_file(self, path: Path, data: dict[str, Any]) -> None:
        """Write out a yaml file in workdir."""
        with (self.workdir / path).open("wt") as fd:
            yaml.safe_dump(data, fd)

    def workdir_files(self) -> list[Path]:
        """List files present in workdir."""
        res: list[Path] = []
        for cur_root, dirs, files in os.walk(self.workdir):
            for name in files:
                res.append(
                    Path(os.path.join(cur_root, name)).relative_to(self.workdir)
                )
        return res

    def assertFileContents(self, path: Path, expected: dict[str, Any]) -> None:
        """Check that path points to a YAML file with the given contents."""
        with (self.workdir / path).open() as fd:
            actual = yaml.safe_load(fd)
        self.assertEqual(actual, expected)

    def test_pull_default_to_current_dir(self) -> None:
        with mock.patch("debusine.client.cli.Cli._task_config_pull") as pull:
            cli = self.create_cli(["task-config-pull"])
            cli.execute()
        pull.assert_called_with(
            self.debusine, Path.cwd(), workspace=None, collection="default"
        )

    def test_pull_workdir(self) -> None:
        with mock.patch("debusine.client.cli.Cli._task_config_pull") as pull:
            cli = self.create_cli(
                ["task-config-pull", "--workdir", self.workdir.as_posix()]
            )
            cli.execute()
        pull.assert_called_with(
            self.debusine, self.workdir, workspace=None, collection="default"
        )

    def test_pull_workspace(self) -> None:
        with mock.patch("debusine.client.cli.Cli._task_config_pull") as pull:
            cli = self.create_cli(
                [
                    "task-config-pull",
                    "--workdir",
                    self.workdir.as_posix(),
                    "--workspace=workspace",
                ]
            )
            cli.execute()
        pull.assert_called_with(
            self.debusine,
            self.workdir,
            workspace="workspace",
            collection="default",
        )

    def test_pull_collection_name(self) -> None:
        with mock.patch("debusine.client.cli.Cli._task_config_pull") as pull:
            cli = self.create_cli(
                [
                    "task-config-pull",
                    "--workdir",
                    self.workdir.as_posix(),
                    "--workspace=workspace",
                    "collection",
                ]
            )
            cli.execute()
        pull.assert_called_with(
            self.debusine,
            self.workdir,
            workspace="workspace",
            collection="collection",
        )

    def test_pull_no_workspace(self) -> None:
        fetch = self.mock_fetch(repo=None)
        cli = self.create_cli(
            ["task-config-pull", "--workdir", self.workdir.as_posix()]
        )
        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )
        fetch.assert_not_called()
        self.assertEqual(stdout, "")
        self.assertEqual(
            stderr,
            f"{self.workdir} is not a repository,"
            " and no workspace/collection names"
            " were provided for a new checkout\n",
        )

    def test_pull_new_repo(self) -> None:
        server_repo = self.make_remote_repo()
        server_repo.add(self.config1)
        server_repo.add(self.template)
        self.mock_fetch(repo=server_repo)
        cli = self.create_cli(
            [
                "task-config-pull",
                "--workspace=System",
                "--workdir",
                self.workdir.as_posix(),
            ]
        )
        with self.assertLogs("debusine.client.tests") as log:
            stderr, stdout = self.capture_output(cli.execute)

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            [
                "new/templates/template.yaml: new item",
                "new/worker_noop/any_any.yaml: new item",
                "2 added, 0 updated, 0 deleted, 0 unchanged",
            ],
        )
        self.assertCountEqual(
            self.workdir_files(),
            [
                path_manifest := Path("MANIFEST"),
                path_template := Path("new/templates/template.yaml"),
                path_config1 := Path("new/worker_noop/any_any.yaml"),
            ],
        )

        self.assertFileContents(path_manifest, server_repo.manifest.dict())
        self.assertFileContents(path_template, self.template.dict())
        self.assertFileContents(path_config1, self.config1.dict())

    def test_pull_existing_repo(self) -> None:
        server_repo = self.make_remote_repo()
        server_repo.add(self.config1)
        server_repo.add(self.template)
        self.mock_fetch(repo=server_repo)
        self.write_file(
            path_manifest := Path("MANIFEST"), server_repo.manifest.dict()
        )
        self.write_file(
            path_config1 := Path("config.yaml"), self.config1.dict()
        )
        self.write_file(
            path_template := Path("template.yaml"), self.template.dict()
        )
        cli = self.create_cli(
            ["task-config-pull", "--workdir", self.workdir.as_posix()]
        )
        with self.assertLogs("debusine.client.tests") as log:
            stderr, stdout = self.capture_output(cli.execute)

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            [
                "0 added, 0 updated, 0 deleted, 2 unchanged",
            ],
        )
        self.assertCountEqual(
            self.workdir_files(),
            [
                path_manifest,
                path_template,
                path_config1,
            ],
        )

        self.assertFileContents(path_manifest, server_repo.manifest.dict())
        self.assertFileContents(path_template, self.template.dict())
        self.assertFileContents(path_config1, self.config1.dict())

    def test_pull_repo_manifest_mismatch(self) -> None:
        server_repo = self.make_remote_repo()
        server_repo.add(self.config2)
        self.mock_fetch(repo=server_repo)
        local_manifest = server_repo.manifest.dict()
        local_manifest["workspace"] = "other"
        self.write_file(path_manifest := Path("MANIFEST"), local_manifest)
        self.write_file(
            path_config1 := Path("config.yaml"), self.config1.dict()
        )
        cli = self.create_cli(
            ["task-config-pull", "System", "--workdir", self.workdir.as_posix()]
        )
        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )

        self.assertEqual(
            stderr,
            "repo to pull refers to collection System/default (42)"
            " while the checkout has collection other/default (42)\n",
        )
        self.assertEqual(stdout, "")
        self.assertCountEqual(
            self.workdir_files(),
            [
                path_manifest,
                path_config1,
            ],
        )

    def test_push_default_to_current_dir(self) -> None:
        with mock.patch("debusine.client.cli.Cli._task_config_push") as push:
            cli = self.create_cli(["task-config-push"])
            cli.execute()
        push.assert_called_with(
            self.debusine, Path.cwd(), dry_run=False, force=False
        )

    def test_push_workdir(self) -> None:
        with mock.patch("debusine.client.cli.Cli._task_config_push") as push:
            cli = self.create_cli(
                ["task-config-push", "--workdir", self.workdir.as_posix()]
            )
            cli.execute()
        push.assert_called_with(
            self.debusine,
            self.workdir,
            dry_run=False,
            force=False,
        )

    def test_push_dry_run(self) -> None:
        with mock.patch("debusine.client.cli.Cli._task_config_push") as push:
            cli = self.create_cli(
                [
                    "task-config-push",
                    "--workdir",
                    self.workdir.as_posix(),
                    "--dry-run",
                ]
            )
            cli.execute()
        push.assert_called_with(
            self.debusine, self.workdir, dry_run=True, force=False
        )

    def test_push_force(self) -> None:
        with mock.patch("debusine.client.cli.Cli._task_config_push") as push:
            cli = self.create_cli(
                [
                    "task-config-push",
                    "--workdir",
                    self.workdir.as_posix(),
                    "--force",
                ]
            )
            cli.execute()
        push.assert_called_with(
            self.debusine, self.workdir, dry_run=False, force=True
        )

    def test_push_no_workdir(self) -> None:
        path = self.workdir / "does-not-exist"
        cli = self.create_cli(
            ["task-config-push", "--workdir", path.as_posix()]
        )
        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )
        self.assertEqual(stderr, f"{path} is not a repository\n")
        self.assertEqual(stdout, "")

    def test_push_no_manifest(self) -> None:
        cli = self.create_cli(
            ["task-config-push", "--workdir", self.workdir.as_posix()]
        )
        stderr, stdout = self.capture_output(
            cli.execute, assert_system_exit_code=3
        )
        self.assertEqual(stderr, f"{self.workdir} is not a repository\n")
        self.assertEqual(stdout, "")

    def test_push_repo(self) -> None:
        server_repo = self.make_remote_repo()
        self.mock_fetch(repo=server_repo)
        push_method = self.mock_push()
        self.write_file(Path("MANIFEST"), self.manifest().dict())
        self.write_file(Path("config.yaml"), self.config1.dict())
        cli = self.create_cli(
            ["task-config-push", "--workdir", self.workdir.as_posix()]
        )
        with self.assertLogs("debusine.client.tests") as log:
            stderr, stdout = self.capture_output(cli.execute)

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            [
                "Pushing data to server...",
                "1 added, 2 updated, 3 removed, 4 unchanged",
            ],
        )

        repo = push_method.call_args.kwargs["repo"]
        self.assertEqual(repo.manifest, self.manifest())
        self.assertEqual(
            [x.item for x in repo.entries.values()], [self.config1]
        )
        self.assertFalse(push_method.call_args.kwargs["dry_run"])

    def test_push_repo_dry_run(self) -> None:
        server_repo = self.make_remote_repo()
        self.mock_fetch(repo=server_repo)
        push_method = self.mock_push()
        self.write_file(Path("MANIFEST"), self.manifest().dict())
        self.write_file(Path("config.yaml"), self.config1.dict())
        cli = self.create_cli(
            [
                "task-config-push",
                "--workdir",
                self.workdir.as_posix(),
                "--dry-run",
            ]
        )
        with self.assertLogs("debusine.client.tests") as log:
            stderr, stdout = self.capture_output(cli.execute)

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")
        self.assertCountEqual(
            [x.removeprefix("INFO:debusine.client.tests:") for x in log.output],
            [
                "Pushing data to server (dry run)...",
                "1 added, 2 updated, 3 removed, 4 unchanged",
            ],
        )

        repo = push_method.call_args.kwargs["repo"]
        self.assertEqual(repo.manifest, self.manifest())
        self.assertEqual(
            [x.item for x in repo.entries.values()], [self.config1]
        )
        self.assertTrue(push_method.call_args.kwargs["dry_run"])

    def test_push_repo_dirty(self) -> None:
        self.write_file(Path("MANIFEST"), self.manifest().dict())
        cli = self.create_cli(
            ["task-config-push", "--workdir", self.workdir.as_posix()]
        )
        with mock.patch(
            "debusine.client.task_configuration"
            ".LocalTaskConfigurationRepository.is_dirty",
            return_value=True,
        ):
            stderr, stdout = self.capture_output(
                cli.execute, assert_system_exit_code=3
            )
        self.assertEqual(
            stderr,
            f"{self.workdir} has uncommitted changes:"
            " please commit them before pushing\n",
        )
        self.assertEqual(stdout, "")

    def test_push_repo_dirty_force(self) -> None:
        self.write_file(Path("MANIFEST"), self.manifest().dict())
        fetch_method = self.mock_fetch(repo=self.make_remote_repo())
        push_method = self.mock_push()
        cli = self.create_cli(
            [
                "task-config-push",
                "--workdir",
                self.workdir.as_posix(),
                "--force",
            ]
        )
        with (
            mock.patch(
                "debusine.client.task_configuration"
                ".LocalTaskConfigurationRepository.is_dirty",
                return_value=True,
            ),
            self.assertLogs("debusine.client.tests") as log,
        ):
            stderr, stdout = self.capture_output(cli.execute)
        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")
        self.assertCountEqual(
            [
                x.removeprefix("WARNING:debusine.client.tests:").removeprefix(
                    "INFO:debusine.client.tests:"
                )
                for x in log.output
            ],
            [
                f"{self.workdir} has uncommitted changes:"
                " please commit them before pushing",
                "Pushing data to server...",
                "1 added, 2 updated, 3 removed, 4 unchanged",
            ],
        )
        fetch_method.assert_called()
        push_method.assert_called()

    def test_push_git_no_commit_on_server(self) -> None:
        local_commit = "0" * 40
        server_repo = self.make_remote_repo()
        self.mock_fetch(repo=server_repo)
        push_method = self.mock_push()
        self.write_file(Path("MANIFEST"), self.manifest().dict())
        with mock.patch(
            "debusine.client.task_configuration"
            ".LocalTaskConfigurationRepository.git_commit",
            return_value=local_commit,
        ):
            cli = self.create_cli(
                [
                    "task-config-push",
                    "--workdir",
                    self.workdir.as_posix(),
                ]
            )
            cli.execute()

        repo = push_method.call_args.kwargs["repo"]
        self.assertEqual(
            repo.manifest, self.manifest(data={"git_commit": local_commit})
        )

    def test_push_git_does_not_have_previous_commit(self) -> None:
        local_commit = "1" * 40
        server_commit = "0" * 40
        server_repo = self.make_remote_repo()
        server_repo.manifest.collection.data["git_commit"] = server_commit
        fetch_method = self.mock_fetch(repo=server_repo)
        push_method = self.mock_push()

        self.write_file(Path("MANIFEST"), self.manifest().dict())
        with (
            mock.patch(
                "debusine.client.task_configuration"
                ".LocalTaskConfigurationRepository.git_commit",
                return_value=local_commit,
            ),
            mock.patch(
                "debusine.client.task_configuration"
                ".LocalTaskConfigurationRepository.has_commit",
                return_value=False,
            ),
        ):
            cli = self.create_cli(
                [
                    "task-config-push",
                    "--workdir",
                    self.workdir.as_posix(),
                ]
            )
            stderr, stdout = self.capture_output(
                cli.execute, assert_system_exit_code=3
            )

        self.assertEqual(
            stderr,
            "server collection was pushed from commit"
            f" {server_commit} which is not known to {self.workdir}\n",
        )
        self.assertEqual(stdout, "")

        fetch_method.assert_called()
        push_method.assert_not_called()

    def test_push_git_does_not_have_previous_commit_force(self) -> None:
        local_commit = "1" * 40
        server_commit = "0" * 40
        server_repo = self.make_remote_repo()
        server_repo.manifest.collection.data["git_commit"] = server_commit
        fetch_method = self.mock_fetch(repo=server_repo)
        push_method = self.mock_push()

        self.write_file(Path("MANIFEST"), self.manifest().dict())
        with (
            mock.patch(
                "debusine.client.task_configuration"
                ".LocalTaskConfigurationRepository.git_commit",
                return_value=local_commit,
            ),
            mock.patch(
                "debusine.client.task_configuration"
                ".LocalTaskConfigurationRepository.has_commit",
                return_value=False,
            ),
            self.assertLogs("debusine.client.tests") as log,
        ):
            cli = self.create_cli(
                [
                    "task-config-push",
                    "--workdir",
                    self.workdir.as_posix(),
                    "--force",
                ]
            )
            stderr, stdout = self.capture_output(cli.execute)

        self.assertCountEqual(
            [
                x.removeprefix("WARNING:debusine.client.tests:").removeprefix(
                    "INFO:debusine.client.tests:"
                )
                for x in log.output
            ],
            [
                "server collection was pushed from commit"
                f" {server_commit} which is not known to {self.workdir}",
                "Pushing data to server...",
                "1 added, 2 updated, 3 removed, 4 unchanged",
            ],
        )

        self.assertEqual(stderr, "")
        self.assertEqual(stdout, "")

        fetch_method.assert_called()
        push_method.assert_called()

    def test_push_git_has_previous_commit(self) -> None:
        local_commit = "0" * 40
        server_commit = "1" * 40
        server_repo = self.make_remote_repo()
        server_repo.manifest.collection.data["git_commit"] = server_commit
        self.mock_fetch(repo=server_repo)
        push_method = self.mock_push()

        self.write_file(Path("MANIFEST"), self.manifest().dict())
        with (
            mock.patch(
                "debusine.client.task_configuration"
                ".LocalTaskConfigurationRepository.git_commit",
                return_value=local_commit,
            ),
            mock.patch(
                "debusine.client.task_configuration"
                ".LocalTaskConfigurationRepository.has_commit",
                return_value=True,
            ),
        ):
            cli = self.create_cli(
                [
                    "task-config-push",
                    "--workdir",
                    self.workdir.as_posix(),
                ]
            )
            cli.execute()

        repo = push_method.call_args.kwargs["repo"]
        self.assertEqual(
            repo.manifest, self.manifest(data={"git_commit": local_commit})
        )


class CliWorkspaceInheritanceTests(BaseCliTests):
    """Tests for Cli workspace-inheritance."""

    def setUp(self) -> None:
        super().setUp()
        build_debusine = self.patch_build_debusine_object()
        self.debusine = build_debusine.return_value

    def cli(
        self, args: list[str], current: WorkspaceInheritanceChain
    ) -> WorkspaceInheritanceChain | None:
        """
        Run the command with the given arguments.

        :returns: the inheritance chain submitted to the server, or None if
            nothing was submitted
        """
        with (
            mock.patch.object(
                self.debusine,
                "get_workspace_inheritance",
                return_value=current or WorkspaceInheritanceChain(),
            ),
            mock.patch.object(
                self.debusine,
                "set_workspace_inheritance",
                side_effect=lambda self, chain: chain,
            ) as set_inheritance,
        ):
            cli = self.create_cli(["workspace-inheritance", "workspace"] + args)
            stderr, stdout = self.capture_output(cli.execute)
            self.assertEqual(stderr, "")
            if set_inheritance.called:
                submitted = set_inheritance.call_args.kwargs["chain"]
                assert isinstance(submitted, WorkspaceInheritanceChain)
                self.assertEqual(
                    stdout,
                    yaml.safe_dump(
                        submitted.dict(),
                        sort_keys=False,
                        width=math.inf,
                    ),
                )
                return submitted
            else:
                self.assertEqual(
                    stdout,
                    yaml.safe_dump(
                        current.dict(),
                        sort_keys=False,
                        width=math.inf,
                    ),
                )
                return None

    def chain(
        self,
        *args: WorkspaceInheritanceChainElement,
    ) -> WorkspaceInheritanceChain:
        """Shortcut to build an inheritance chain."""
        return WorkspaceInheritanceChain(chain=list(args))

    def test_get(self) -> None:
        El = WorkspaceInheritanceChainElement
        self.assertIsNone(self.cli([], current=self.chain(El(id=1), El(id=2))))

    def test_change(self) -> None:
        El = WorkspaceInheritanceChainElement
        el1, el2, el3 = El(id=1), El(id=2), El(id=3)
        for current, args, expected in (
            (
                self.chain(el2),
                ["--append", "1", "3"],
                self.chain(el2, el1, el3),
            ),
            (self.chain(), ["--append", "1", "3"], self.chain(el1, el3)),
            (
                self.chain(el2),
                ["--prepend", "1", "3"],
                self.chain(el1, el3, el2),
            ),
            (self.chain(), ["--prepend", "1", "3"], self.chain(el1, el3)),
            (
                self.chain(el1, el2, el3),
                ["--remove", "1"],
                self.chain(el2, el3),
            ),
            (
                self.chain(el1, el2, el3),
                ["--set", "3", "1"],
                self.chain(el3, el1),
            ),
            (
                self.chain(el1),
                ["--append", "2", "--prepend", "3"],
                self.chain(el3, el1, el2),
            ),
            (
                self.chain(el1),
                ["--append", "2", "--set", "3"],
                self.chain(el3),
            ),
            (
                self.chain(el2),
                ["--prepend", "1", "3"],
                self.chain(el1, el3, el2),
            ),
        ):
            with self.subTest(current=current, args=args):
                self.assertEqual(
                    self.cli(args, current=current),
                    expected,
                )

    def test_change_invalid_chain(self) -> None:
        El = WorkspaceInheritanceChainElement
        with (
            mock.patch.object(
                self.debusine,
                "get_workspace_inheritance",
                return_value=WorkspaceInheritanceChain(),
            ),
            mock.patch.object(
                self.debusine,
                "set_workspace_inheritance",
                side_effect=DebusineError(problem={"error": "expected error"}),
            ) as set_inheritance,
        ):
            cli = self.create_cli(
                ["workspace-inheritance", "workspace", "--set", "1", "1"]
            )
            stderr, stdout = self.capture_output(
                cli.execute, assert_system_exit_code=3
            )
            self.assertEqual(
                stdout, "result: failure\nerror:\n  error: expected error\n"
            )
            self.assertEqual(stderr, "")
            set_inheritance.assert_called_with(
                "workspace", chain=self.chain(El(id=1), El(id=1))
            )

    def test_edit(self) -> None:
        def mock_edit(self_: YamlEditor[dict[str, Any]]) -> bool:
            self_.value = self.chain(el3, el2, el1).dict()
            return True

        El = WorkspaceInheritanceChainElement
        el1, el2, el3 = El(id=1), El(id=2), El(id=3)
        with mock.patch(
            "debusine.utils.input.YamlEditor.edit",
            side_effect=mock_edit,
            autospec=True,
        ):
            self.assertEqual(
                self.cli(["--edit"], current=self.chain(el1, el2, el3)),
                self.chain(el3, el2, el1),
            )

    def test_edit_failed(self) -> None:
        def mock_edit(self_: YamlEditor[dict[str, Any]]) -> bool:
            self_.value = self.chain(el3, el2, el1).dict()
            return False

        El = WorkspaceInheritanceChainElement
        el1, el2, el3 = El(id=1), El(id=2), El(id=3)
        with mock.patch(
            "debusine.utils.input.YamlEditor.edit",
            side_effect=mock_edit,
            autospec=True,
        ):
            self.assertIsNone(
                self.cli(["--edit"], current=self.chain(el1, el2, el3))
            )
