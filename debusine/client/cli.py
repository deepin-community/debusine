# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine command line interface."""

import argparse
import http
import logging
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import types
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any, NoReturn

import yaml
from dateutil.parser import isoparse
from requests.exceptions import RequestException
from yaml import YAMLError

from debusine.artifacts import LocalArtifact, Upload
from debusine.assets import AssetCategory, asset_data_model
from debusine.client import exceptions
from debusine.client.client_utils import (
    copy_file,
    get_debian_package,
    prepare_changes_for_upload,
    prepare_deb_for_upload,
    prepare_dsc_for_upload,
)
from debusine.client.config import ConfigHandler
from debusine.client.debusine import Debusine
from debusine.client.exceptions import DebusineError
from debusine.client.models import (
    CreateWorkflowRequest,
    FileResponse,
    RelationType,
    WorkRequestExternalDebsignRequest,
    WorkRequestRequest,
    WorkRequestResponse,
    WorkflowTemplateRequest,
    model_to_json_serializable_dict,
    model_to_json_serializable_list,
)


class Cli:
    """
    Entry point for the command line debusine client.

    Usage:
        main = Cli(sys.argv[1:]) # [1:] to exclude the script name
        main.execute()
    """

    def __init__(self, argv: list[str]) -> None:
        """Initialize object."""
        self._argv = argv

    @staticmethod
    def _exit(signum: int, frame: types.FrameType | None) -> NoReturn:
        signum, frame  # fake usage for vulture
        raise SystemExit(0)

    def _parse_args(self) -> None:
        """Parse argv and store results in self.args."""
        parser = argparse.ArgumentParser(
            prog='debusine',
            description='Interacts with a Debusine server.',
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )

        parser.add_argument(
            '--server',
            help=(
                'Set server to be used, either by section name or as '
                'FQDN/scope (use configuration file default if not specified)'
            ),
        )

        parser.add_argument(
            '--scope',
            help=(
                'Set scope to be used (use configuration file default '
                'if not specified)'
            ),
        )

        parser.add_argument(
            '--config-file',
            default=ConfigHandler.DEFAULT_CONFIG_FILE_PATH,
            help='Config file path',
        )

        parser.add_argument("-s", "--silent", action="store_true")

        parser.add_argument(
            "-d", "--debug", action="store_true", help='Print HTTP traffic'
        )

        subparsers = parser.add_subparsers(
            help='Sub command', dest='sub-command', required=True
        )
        list_work_requests = subparsers.add_parser(
            'list-work-requests',
            help='List all work requests',
        )
        list_work_requests.add_argument(
            '--limit',
            type=int,
            metavar="LIMIT",
            default=20,
            help="List the LIMIT most recent work requests",
        )

        show_work_request = subparsers.add_parser(
            'show-work-request',
            help='Print the status of a work request',
        )
        show_work_request.add_argument(
            'work_request_id',
            type=int,
            help='Work request id to show the information',
        )

        create_work_request = subparsers.add_parser(
            'create-work-request',
            help='Create a work request and schedule the execution. '
            'Work request is read from stdin in YAML format',
        )
        create_work_request.add_argument(
            'task_name', type=str, help='Task name for the work request'
        )
        create_work_request.add_argument(
            "--workspace", type=str, help="Workspace name"
        )
        create_work_request.add_argument(
            "--data",
            type=argparse.FileType("r"),
            help="File path (or - for stdin) to read the data "
            "for the work request. YAML format. Defaults to stdin.",
            default="-",
        )
        create_work_request.add_argument(
            "--event-reactions",
            type=argparse.FileType("r"),
            help="File path in YAML format requesting notifications.",
        )

        manage_work_request = subparsers.add_parser(
            "manage-work-request", help="Manage a work request"
        )
        manage_work_request.add_argument(
            "work_request_id", type=int, help="Work request id to manage"
        )
        manage_work_request.add_argument(
            "--set-priority-adjustment",
            type=int,
            metavar="ADJUSTMENT",
            help="Set priority adjustment (positive or negative number)",
        )

        retry_work_request = subparsers.add_parser(
            "retry-work-request", help="Retry a failed work request"
        )
        retry_work_request.add_argument(
            "work_request_id", type=int, help="Work request id to retry"
        )

        provide_signature = subparsers.add_parser(
            "provide-signature",
            help="Provide a work request with an external signature",
        )
        provide_signature.add_argument(
            "work_request_id",
            type=int,
            help="Work request id that needs a signature",
        )
        provide_signature.add_argument(
            "--local-file",
            "-l",
            type=Path,
            help=(
                "Path to the .changes file to sign, locally. "
                "If not specified, it will be downloaded from the server."
            ),
        )
        provide_signature.add_argument(
            "extra_args",
            nargs="*",
            help="Additional arguments passed to debsign",
        )

        create_workflow_template = subparsers.add_parser(
            "create-workflow-template", help="Create a workflow template"
        )
        create_workflow_template.add_argument(
            "template_name", type=str, help="Name of the new workflow template"
        )
        create_workflow_template.add_argument(
            "task_name",
            type=str,
            help="Name of the workflow task that the template will use",
        )
        create_workflow_template.add_argument(
            "--workspace", type=str, help="Workspace name"
        )
        create_workflow_template.add_argument(
            "--data",
            type=argparse.FileType("r"),
            default="-",
            help=(
                "File path (or - for stdin) to read the data or the workflow "
                "template. YAML format. Defaults to stdin."
            ),
        )
        create_workflow_template.add_argument(
            "--priority",
            type=int,
            default=0,
            help="Base priority for work requests created from this template",
        )

        create_workflow = subparsers.add_parser(
            "create-workflow", help="Create a workflow from a template"
        )
        create_workflow.add_argument(
            "template_name", type=str, help="Workflow template name"
        )
        create_workflow.add_argument(
            "--workspace", type=str, help="Workspace name"
        )
        create_workflow.add_argument(
            "--data",
            type=argparse.FileType("r"),
            default="-",
            help=(
                "File path (or - for stdin) to read the data for the workflow. "
                "YAML format. Defaults to stdin."
            ),
        )

        create_artifact = subparsers.add_parser(
            "create-artifact", help="Create an artifact"
        )
        create_artifact.add_argument(
            "category",
            type=str,
            help="Category of artifact",
            choices=list(map(str, LocalArtifact.artifact_categories())),
        )
        create_artifact.add_argument(
            "--workspace", type=str, help="Workspace for this artifact"
        )
        create_artifact.add_argument(
            "--upload", nargs="+", help="Files to upload", default=[]
        )
        create_artifact.add_argument(
            "--upload-base-directory",
            type=str,
            default=None,
            help="Base directory for files with relative file path",
        )
        create_artifact.add_argument(
            "--expire-at",
            type=isoparse,
            help="If not passed: artifact does not expire. "
            "If passed: set expire date of artifact.",
        )
        create_artifact.add_argument(
            "--data",
            type=argparse.FileType("r"),
            help="File path (or - for stdin) to read the data "
            "for the artifact. YAML format",
        )

        import_debian_artifact = subparsers.add_parser(
            "import-debian-artifact",
            help="Import a Debian source/binary package",
        )
        import_debian_artifact.add_argument(
            "--workspace", type=str, help="Workspace for this artifact"
        )
        import_debian_artifact.add_argument(
            "--expire-at",
            type=isoparse,
            help="If not passed: artifact does not expire. "
            "If passed: set expire date of artifact.",
        )
        import_debian_artifact.add_argument(
            "upload",
            help=".changes, .dsc, or .deb to upload",
        )

        download_artifact = subparsers.add_parser(
            "download-artifact",
            help="Download an artifact in .tar.gz format",
        )
        download_artifact.add_argument(
            "id", type=int, help="Artifact to download"
        )
        download_artifact.add_argument(
            "--target-directory",
            type=Path,
            help="Directory to save the artifact",
            default=Path.cwd(),
        )
        download_artifact.add_argument(
            "--tarball",
            action="store_true",
            help="Save the artifact as .tar.gz",
        )

        show_artifact = subparsers.add_parser(
            "show-artifact",
            help="Show artifact information",
        )
        show_artifact.add_argument(
            "id", type=int, help="Show information of the artifact"
        )

        show_artifact_relations = subparsers.add_parser(
            "show-artifact-relations",
            help="Show artifact relations",
        )
        show_artifact_relations.add_argument(
            "id", type=int, help="Show the relations of an artifact"
        )
        show_artifact_relations.add_argument(
            "--reverse",
            action="store_true",
            help="Show reverse relationships, rather than forward ones",
        )

        create_asset = subparsers.add_parser(
            "create-asset", help="Create an asset"
        )
        create_asset.add_argument(
            "category",
            type=AssetCategory,
            help="Category of asset",
        )
        create_asset.add_argument(
            "--workspace",
            type=str,
            required=True,
            help="Workspace for this asset",
        )
        create_asset.add_argument(
            "--data",
            type=argparse.FileType("r"),
            required=True,
            help="File path (or - for stdin) to read the data "
            "for the asset. YAML format",
        )

        list_assets = subparsers.add_parser("list-assets", help="List assets")
        list_assets.add_argument(
            "--id",
            metavar="ID",
            type=int,
            help="Show an individual asset by ID.",
        )
        list_assets.add_argument(
            "--workspace",
            metavar="NAME",
            type=str,
            help="List all assets in workspace.",
        )
        list_assets.add_argument(
            "--work-request",
            metavar="ID",
            type=int,
            help="List assets created by work request.",
        )

        on_work_request_completed = subparsers.add_parser(
            "on-work-request-completed",
            help="Execute a command when a work request is completed. "
            "Arguments to the command: WorkRequest.id and "
            "WorkRequest.result",
        )
        on_work_request_completed.add_argument(
            "--workspace",
            nargs="+",
            type=str,
            help="Workspace names to monitor. If not specified: receive "
            "notifications from all the workspaces that the user "
            "has access to",
        )
        on_work_request_completed.add_argument(
            "--last-completed-at",
            type=Path,
            help="If not passed: does not get any past notifications. "
            "If passed: write into it the WorkRequest.completed_at "
            "and when running again retrieve missed notifications",
        )
        on_work_request_completed.add_argument(
            "command",
            type=str,
            help="Path to the command to execute",
        )

        subparsers.add_parser("setup", help="Guided setup of debusine-client")

        self.args = parser.parse_args(self._argv)

    def _build_debusine_object(self) -> Debusine:
        """Return the debusine object matching the command line parameters."""
        configuration = ConfigHandler(
            server_name=self.args.server, config_file_path=self.args.config_file
        )

        try:
            server_configuration = configuration.server_configuration()
        except ValueError as exc:
            self._fail(exc)

        logging_level = logging.WARNING if self.args.silent else logging.INFO

        logger = logging.getLogger("debusine")
        logger.propagate = False
        logger.setLevel(logging_level)

        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter('%(message)s')
        handler.setFormatter(formatter)

        logger.addHandler(handler)

        return Debusine(
            base_api_url=server_configuration['api-url'],
            scope=self.args.scope or server_configuration['scope'],
            api_token=server_configuration['token'],
            logger=logger,
        )

    def _setup_http_logging(self) -> None:
        if self.args.debug:
            """Do setup urllib3&requests traces."""
            # https://docs.python-requests.org/en/latest/api/#api-changes
            # Debug connection establishment
            # Use a separate DEBUG logger
            urllib3_log = logging.getLogger("urllib3")
            urllib3_log.setLevel(logging.DEBUG)
            sh = logging.StreamHandler()
            sh.setLevel(logging.DEBUG)
            sh.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
            urllib3_log.addHandler(sh)

            # Debug at http.client level (requests->urllib3->http.client)
            # Use a separate DEBUG logger
            # Display the REQUEST, including HEADERS and DATA, and
            # RESPONSE with HEADERS but without DATA.  The only thing
            # missing will be the response.body which is not logged.
            http.client.HTTPConnection.debuglevel = 1
            # Divert to new logger (stderr) to avoid polluting CLI output
            requests_log = logging.getLogger("requests")
            requests_log.setLevel(logging.DEBUG)
            requests_log.addHandler(sh)

            def print_to_log(*args: str) -> None:
                requests_log.debug(" ".join(args))

            setattr(http.client, "print", print_to_log)

    def execute(self) -> None:
        """Execute the command requested by the user."""
        signal.signal(signal.SIGINT, self._exit)
        signal.signal(signal.SIGTERM, self._exit)

        self._parse_args()

        # Early dispatch for subcommands that do not need the Debusine object
        # to operate
        match getattr(self.args, 'sub-command'):
            case "setup":
                from debusine.client.setup import setup_server

                setup_server(
                    config_file_path=self.args.config_file,
                    server=self.args.server,
                    scope=self.args.scope,
                )
                return

        debusine = self._build_debusine_object()
        self._setup_http_logging()

        match getattr(self.args, 'sub-command'):
            case "list-work-requests":
                self._list_work_requests(debusine, self.args.limit)
            case "show-work-request":
                self._show_work_request(debusine, self.args.work_request_id)
            case "create-work-request":
                self.execute_create_work_request(debusine)
            case "manage-work-request":
                self._manage_work_request(
                    debusine,
                    self.args.work_request_id,
                    priority_adjustment=self.args.set_priority_adjustment,
                )
            case "retry-work-request":
                self._retry_work_request(
                    debusine,
                    self.args.work_request_id,
                )
            case "provide-signature":
                self._provide_signature(
                    debusine,
                    self.args.work_request_id,
                    self.args.local_file,
                    self.args.extra_args,
                )
            case "create-artifact":
                if self.args.data is not None:
                    data = self.args.data.read()
                    self.args.data.close()
                    if self.args.data == sys.stdin:
                        print("---")  # avoid confusion between input and output
                else:
                    data = None

                self._create_artifact(
                    debusine,
                    self.args.category,
                    self.args.workspace,
                    self.args.upload,
                    self.args.upload_base_directory,
                    self.args.expire_at,
                    data,
                )
            case "import-debian-artifact":
                self._import_debian_artifact(
                    debusine,
                    self.args.workspace,
                    self.args.upload,
                    self.args.expire_at,
                )
            case "download-artifact":
                self._download_artifact(
                    debusine,
                    self.args.id,
                    tarball=self.args.tarball,
                    target_directory=self.args.target_directory,
                )
            case "show-artifact":
                self._show_artifact(debusine, self.args.id)
            case "show-artifact-relations":
                self._show_artifact_relations(
                    debusine, self.args.id, self.args.reverse
                )
            case "create-asset":
                data = self.args.data.read()
                self.args.data.close()
                if self.args.data == sys.stdin:
                    print("---")  # avoid confusion between input and output

                self._create_asset(
                    debusine,
                    self.args.category,
                    self.args.workspace,
                    data,
                )
            case "list-assets":
                self._list_assets(
                    debusine,
                    asset=self.args.id,
                    workspace=self.args.workspace,
                    work_request=self.args.work_request,
                )
            case "on-work-request-completed":
                self._on_work_request_completed(
                    debusine,
                    workspaces=self.args.workspace,
                    last_completed_at=self.args.last_completed_at,
                    command=self.args.command,
                )
            case "create-workflow-template":
                self.execute_create_workflow_template(debusine)
            case "create-workflow":
                self.execute_create_workflow(debusine)
            case _ as unreachable:
                raise AssertionError(
                    f"Unexpected sub-command name: {unreachable}"
                )

    def execute_create_work_request(self, debusine: Debusine) -> None:
        """Execute the work request action requested by the user."""
        data = self.args.data.read()
        self.args.data.close()
        if self.args.data == sys.stdin:
            print("---")  # avoid confusion between input and output

        event_reactions = '{}'
        if self.args.event_reactions:
            event_reactions = self.args.event_reactions.read()
            self.args.event_reactions.close()

        self._create_work_request(
            debusine,
            self.args.task_name,
            self.args.workspace,
            data,
            event_reactions,
        )

    @staticmethod
    def _print_yaml(data: Any) -> None:
        """Print data to stdout as yaml."""
        output = yaml.safe_dump(data, sort_keys=False, width=math.inf)
        print(output, end="")

    @staticmethod
    def _fail(error: object, *, summary: str | None = None) -> NoReturn:
        print(error, file=sys.stderr)
        if summary is not None:
            print(summary, file=sys.stderr)
        raise SystemExit(3)

    def _list_work_requests(self, debusine: Debusine, limit: int) -> None:
        """List up to limit most recent work requests."""
        with self._api_call_or_fail():
            work_requests = list(islice(debusine.work_request_iter(), limit))
        result = {
            "results": [work_request.dict() for work_request in work_requests]
        }
        self._print_yaml(result)

    def _show_work_request(
        self, debusine: Debusine, work_request_id: int
    ) -> None:
        """Print the task information for work_request_id."""
        with self._api_call_or_fail():
            work_request = debusine.work_request_get(work_request_id)

        artifacts_information = []
        for artifact_id in work_request.artifacts:
            with self._api_call_or_fail():
                artifact_information = debusine.artifact_get(artifact_id)
            artifacts_information.append(
                model_to_json_serializable_dict(artifact_information)
            )

        result = work_request.dict()
        result["artifacts"] = artifacts_information

        self._print_yaml(result)

    def _create_work_request(
        self,
        debusine: Debusine,
        task_name: str,
        workspace: str,
        task_data: str,
        event_reactions: str,
    ) -> None:
        parsed_task_data = self._parse_yaml_data(task_data)
        parsed_event_reactions = self._parse_yaml_data(event_reactions)

        work_request = WorkRequestRequest(
            task_name=task_name,
            workspace=workspace,
            task_data=parsed_task_data,
            event_reactions=parsed_event_reactions,
        )

        output: dict[str, Any]
        try:
            with self._api_call_or_fail():
                work_request_created = debusine.work_request_create(
                    work_request
                )
        except DebusineError as err:
            output = {"result": "failure", "error": err.asdict()}
        else:
            output = {
                'result': 'success',
                'message': f'Work request registered on '
                f'{debusine.base_api_url} '
                f'with id {work_request_created.id}.',
                'work_request_id': work_request_created.id,
            }
        self._print_yaml(output)

    def _manage_work_request(
        self,
        debusine: Debusine,
        work_request_id: int,
        *,
        priority_adjustment: int | None = None,
    ) -> None:
        """Make changes to an existing work request."""
        if priority_adjustment is None:
            self._fail("Error: no changes specified")

        with self._api_call_or_fail():
            debusine.work_request_update(
                work_request_id, priority_adjustment=priority_adjustment
            )

    def _retry_work_request(
        self,
        debusine: Debusine,
        work_request_id: int,
    ) -> None:
        """Retry an aborted/failed work request."""
        with self._api_call_or_fail():
            debusine.work_request_retry(work_request_id)

    def _fetch_local_file(
        self, src: Path, dest: Path, artifact_file: FileResponse
    ) -> None:
        """Copy src to dest and verify that its hash matches artifact_file."""
        assert src.exists()
        hashes = copy_file(src, dest, artifact_file.checksums.keys())
        if hashes["size"] != artifact_file.size:
            self._fail(
                f'"{src}" size mismatch (expected {artifact_file.size} bytes)'
            )

        for hash_name, expected_value in artifact_file.checksums.items():
            if hashes[hash_name] != expected_value:
                self._fail(
                    f'"{src}" hash mismatch (expected {hash_name} '
                    f'= {expected_value})'
                )

    def _provide_signature_debsign(
        self,
        debusine: Debusine,
        work_request: WorkRequestResponse,
        local_file: Path | None,
        debsign_args: list[str],
    ) -> None:
        """Provide a work request with an external signature using `debsign`."""
        if local_file is not None:
            if local_file.suffix != ".changes":
                self._fail(
                    f"--local-file {str(local_file)!r} is not a .changes file."
                )
            if not local_file.exists():
                self._fail(f"--local-file {str(local_file)!r} does not exist.")
        # Get a version of the work request with its dynamic task data
        # resolved.
        with self._api_call_or_fail():
            work_request = debusine.work_request_external_debsign_get(
                work_request.id
            )
        assert work_request.dynamic_task_data is not None
        with self._api_call_or_fail():
            unsigned = debusine.artifact_get(
                work_request.dynamic_task_data["unsigned_id"]
            )

        with tempfile.TemporaryDirectory(
            prefix="debusine-debsign-"
        ) as tmp_name:
            # Get the individual files that need to be signed.
            tmp = Path(tmp_name).resolve()
            for name, file_response in unsigned.files.items():
                path = tmp / name
                assert path.resolve().is_relative_to(tmp)
                assert "/" not in name
                if (
                    name.endswith(".changes")
                    or name.endswith(".dsc")
                    or name.endswith(".buildinfo")
                ):
                    if local_file:
                        self._fetch_local_file(
                            local_file.parent / name, path, file_response
                        )
                    else:
                        with self._api_call_or_fail():
                            debusine.download_artifact_file(
                                unsigned, name, path
                            )
            # Upload artifacts are guaranteed to have exactly one .changes
            # file.
            [changes_path] = [
                path for path in tmp.iterdir() if path.name.endswith(".changes")
            ]

            # Call debsign.
            subprocess.run(
                ["debsign", "--re-sign", changes_path.name, *debsign_args],
                cwd=tmp,
                check=True,
            )

            # Create a new artifact and upload the files to it.
            signed_local = Upload.create(
                changes_file=changes_path, allow_remote=True
            )
            with self._api_call_or_fail():
                signed_remote = debusine.upload_artifact(
                    signed_local, workspace=unsigned.workspace
                )

            # Complete the work request using the signed artifact.
            with self._api_call_or_fail():
                debusine.work_request_external_debsign_complete(
                    work_request.id,
                    WorkRequestExternalDebsignRequest(
                        signed_artifact=signed_remote.id
                    ),
                )

    def _provide_signature(
        self,
        debusine: Debusine,
        work_request_id: int,
        local_file: Path | None,
        extra_args: list[str],
    ) -> None:
        """Provide a work request with an external signature."""
        # Find out what kind of work request we're dealing with.
        with self._api_call_or_fail():
            work_request = debusine.work_request_get(work_request_id)
        match (work_request.task_type, work_request.task_name):
            case "Wait", "externaldebsign":
                self._provide_signature_debsign(
                    debusine, work_request, local_file, extra_args
                )
            case _:
                self._fail(
                    f"Don't know how to provide signature for "
                    f"{work_request.task_type}/{work_request.task_name} "
                    f"work request"
                )

    def _create_workflow_template(
        self,
        debusine: Debusine,
        *,
        template_name: str,
        task_name: str,
        workspace: str | None,
        task_data: str,
        priority: int,
    ) -> None:
        parsed_task_data = self._parse_yaml_data(task_data)

        workflow_template = WorkflowTemplateRequest(
            name=template_name,
            task_name=task_name,
            workspace=workspace,
            task_data=parsed_task_data,
            priority=priority,
        )

        output: dict[str, Any]
        try:
            with self._api_call_or_fail():
                workflow_template_created = debusine.workflow_template_create(
                    workflow_template
                )
        except DebusineError as err:
            output = {"result": "failure", "error": err.asdict()}
        else:
            output = {
                "result": "success",
                "message": (
                    f"Workflow template registered on {debusine.base_api_url} "
                    f"with id {workflow_template_created.id}."
                ),
                "workflow_template_id": workflow_template_created.id,
            }
        self._print_yaml(output)

    def execute_create_workflow_template(self, debusine: Debusine) -> None:
        """Execute the workflow template action requested by the user."""
        data = self.args.data.read()
        self.args.data.close()
        if self.args.data == sys.stdin:
            print("---")  # avoid confusion between input and output

        self._create_workflow_template(
            debusine,
            template_name=self.args.template_name,
            task_name=self.args.task_name,
            workspace=self.args.workspace,
            task_data=data,
            priority=self.args.priority,
        )

    def _create_workflow(
        self,
        debusine: Debusine,
        *,
        template_name: str,
        workspace: str | None,
        task_data: str,
    ) -> None:
        parsed_task_data = self._parse_yaml_data(task_data)

        workflow = CreateWorkflowRequest(
            template_name=template_name,
            workspace=workspace,
            task_data=parsed_task_data,
        )

        output: dict[str, Any]
        try:
            with self._api_call_or_fail():
                workflow_created = debusine.workflow_create(workflow)
        except DebusineError as err:
            output = {"result": "failure", "error": err.asdict()}
        else:
            output = {
                "result": "success",
                "message": (
                    f"Workflow created on {debusine.base_api_url} with id "
                    f"{workflow_created.id}."
                ),
                "workflow_id": workflow_created.id,
            }
        self._print_yaml(output)

    def execute_create_workflow(self, debusine: Debusine) -> None:
        """Execute the workflow action requested by the user."""
        data = self.args.data.read()
        self.args.data.close()
        if self.args.data == sys.stdin:
            print("---")  # avoid confusion between input and output

        self._create_workflow(
            debusine,
            template_name=self.args.template_name,
            workspace=self.args.workspace,
            task_data=data,
        )

    @contextmanager
    def _api_call_or_fail(self) -> Generator[None, None, None]:
        """
        Context manager to handle failures from calling a method.

        :raises: exceptions.NotFoundError: server returned 404.
        :raises: UnexpectedResponseError: e.g. invalid JSON.
        :raises: ClientConnectionError: e.g. cannot connect to the server.
        :raises: DebusineError (via method() call) when debusine server
          reports an error.
        """
        try:
            yield
        except exceptions.NotFoundError as exc:
            self._fail(exc)
        except exceptions.UnexpectedResponseError as exc:
            self._fail(exc)
        except exceptions.ClientForbiddenError as server_error:
            self._fail(f'Server rejected connection: {server_error}')
        except exceptions.ClientConnectionError as client_error:
            self._fail(f'Error connecting to debusine: {client_error}')

    @classmethod
    def _parse_yaml_data(cls, data_yaml: str | None) -> dict[str, Any]:
        if data_yaml is not None:
            if data_yaml.strip() == "":
                cls._fail("Error: data must be a dictionary. It is empty")
            try:
                data = yaml.safe_load(data_yaml)
            except YAMLError as err:
                cls._fail(
                    f"Error parsing YAML: {err}",
                    summary="Fix the YAML data",
                )

            if (data_type := type(data)) != dict:
                cls._fail(
                    f"Error: data must be a dictionary. "
                    f"It is: {data_type.__name__}"
                )
        else:
            data = {}

        assert isinstance(data, dict)
        return data

    def _create_artifact(
        self,
        debusine: Debusine,
        artifact_type: str,
        workspace: str,
        upload_paths: list[str],
        upload_base_directory: str | None,
        expire_at: datetime | None,
        data_yaml: str | None,
    ) -> None:
        data = self._parse_yaml_data(data_yaml)

        artifact_cls = LocalArtifact.class_from_category(artifact_type)
        local_artifact = artifact_cls(data=data)

        if upload_base_directory is not None:
            upload_base_dir = Path(upload_base_directory).absolute()
        else:
            upload_base_dir = None

        try:
            for upload_path in upload_paths:
                local_artifact.add_local_file(
                    Path(upload_path), artifact_base_dir=upload_base_dir
                )
        except ValueError as exc:
            self._fail(f"Cannot create artifact: {exc}")

        output: dict[str, Any]
        try:
            with self._api_call_or_fail():
                artifact_created = debusine.artifact_create(
                    local_artifact, workspace=workspace, expire_at=expire_at
                )
        except DebusineError as err:
            output = {"result": "failure", "error": err.asdict()}
            self._print_yaml(output)
        else:
            output = {
                "result": "success",
                "message": f"New artifact created in {debusine.base_api_url} "
                f"in workspace {artifact_created.workspace} "
                f"with id {artifact_created.id}.",
                "artifact_id": artifact_created.id,
                "files_to_upload": len(artifact_created.files_to_upload),
                "expire_at": expire_at,
            }

            files_to_upload = {}
            for artifact_path in artifact_created.files_to_upload:
                files_to_upload[artifact_path] = local_artifact.files[
                    artifact_path
                ]

            self._print_yaml(output)
            with self._api_call_or_fail():
                debusine.upload_files(artifact_created.id, files_to_upload)

    def _import_debian_artifact(
        self,
        debusine: Debusine,
        workspace: str,
        upload: str,
        expire_at: datetime | None,
    ) -> None:
        result: dict[str, Any] = {}
        uploaded = []
        try:
            with get_debian_package(upload) as path:
                artifacts: list[LocalArtifact[Any]]
                if path.suffix == ".changes":
                    artifacts = prepare_changes_for_upload(path)
                elif path.suffix == ".dsc":
                    artifacts = [prepare_dsc_for_upload(path)]
                elif path.suffix in (".deb", ".ddeb", ".udeb"):
                    artifacts = [prepare_deb_for_upload(path)]
                else:
                    raise ValueError(
                        "Only source packages (.dsc), binary packages (.deb), "
                        "and source/binary uploads (.changes) can be directly "
                        f"imported with this command. {path} is not supported."
                    )
                for artifact in artifacts:
                    with self._api_call_or_fail():
                        remote_artifact = debusine.upload_artifact(
                            artifact, workspace=workspace, expire_at=expire_at
                        )
                    uploaded.append(remote_artifact)
            primary_artifact = uploaded[0]
            result = {
                "result": "success",
                "message": (
                    f"New artifact created in {debusine.base_api_url} "
                    f"in workspace {remote_artifact.workspace} with id "
                    f"{primary_artifact.id}."
                ),
                "artifact_id": primary_artifact.id,
            }
            for related_artifact in uploaded[1:]:
                with self._api_call_or_fail():
                    debusine.relation_create(
                        primary_artifact.id,
                        related_artifact.id,
                        RelationType.EXTENDS,
                    )
                result.setdefault("extends", []).append(
                    {"artifact_id": related_artifact.id}
                )
                with self._api_call_or_fail():
                    debusine.relation_create(
                        primary_artifact.id,
                        related_artifact.id,
                        RelationType.RELATES_TO,
                    )
                result.setdefault("relates_to", []).append(
                    {"artifact_id": related_artifact.id}
                )

        except (FileNotFoundError, ValueError, RequestException) as err:
            result["result"] = "failure"
            result["error"] = str(err)
        except DebusineError as err:
            result["result"] = "failure"
            result["error"] = err.asdict()

        self._print_yaml(result)
        if result["result"] != "success":
            raise SystemExit(3)

    def _download_artifact(
        self,
        debusine: Debusine,
        artifact_id: int,
        *,
        target_directory: Path,
        tarball: bool,
    ) -> None:
        if not os.access(target_directory, os.W_OK):
            self._fail(f"Error: Cannot write to {target_directory}")

        with self._api_call_or_fail():
            debusine.download_artifact(
                artifact_id, destination=target_directory, tarball=tarball
            )

    def _show_artifact(self, debusine: Debusine, artifact_id: int) -> None:
        with self._api_call_or_fail():
            artifact = debusine.artifact_get(artifact_id)
        self._print_yaml(model_to_json_serializable_dict(artifact))

    def _show_artifact_relations(
        self, debusine: Debusine, artifact_id: int, reverse: bool
    ) -> None:
        with self._api_call_or_fail():
            if reverse:
                models = debusine.relation_list(target_id=artifact_id)
            else:
                models = debusine.relation_list(artifact_id=artifact_id)

        relations = [model_to_json_serializable_dict(model) for model in models]
        self._print_yaml(relations)

    def _create_asset(
        self,
        debusine: Debusine,
        category: AssetCategory,
        workspace: str,
        data_yaml: str | None,
    ) -> None:
        data = self._parse_yaml_data(data_yaml)

        asset_data = asset_data_model(category, data)

        try:
            with self._api_call_or_fail():
                asset = debusine.asset_create(
                    category=category, data=asset_data, workspace=workspace
                )
        except DebusineError as err:
            output = {"result": "failure", "error": err.asdict()}
            self._print_yaml(output)
            raise SystemExit(3)
        else:
            self._print_yaml(
                {
                    "result": "success",
                    "message": (
                        f"New asset created in {debusine.base_api_url} "
                        f"in workspace {asset.workspace} with id {asset.id}."
                    ),
                }
            )

    def _list_assets(
        self,
        debusine: Debusine,
        asset: int | None,
        workspace: str | None,
        work_request: int | None,
    ) -> None:
        if asset is None and workspace is None and work_request is None:
            self._fail(
                "At least one of --id, --workspace, and --work-request must "
                "be specified."
            )
        try:
            with self._api_call_or_fail():
                assets = debusine.asset_list(
                    asset_id=asset,
                    work_request=work_request,
                    workspace=workspace,
                )
        except DebusineError as err:
            output = {"result": "failure", "error": err.asdict()}
            self._print_yaml(output)
            raise SystemExit(3)
        self._print_yaml(model_to_json_serializable_list(assets))

    def _on_work_request_completed(
        self,
        debusine: Debusine,
        *,
        workspaces: list[str],
        last_completed_at: Path | None,
        command: str,
    ) -> None:
        if shutil.which(command) is None:
            self._fail(
                f'Error: "{command}" does not exist or is not executable'
            )

        if last_completed_at is not None:
            # Check that the file can be written or created
            if not os.access(last_completed_at.parent, os.W_OK):
                self._fail(
                    f'Error: write access '
                    f'denied for directory "{last_completed_at.parent}"'
                )

            if last_completed_at.exists() and (
                not os.access(last_completed_at, os.W_OK)
                or not os.access(last_completed_at, os.R_OK)
            ):
                self._fail(
                    'Error: write or read access '
                    f'denied for "{last_completed_at}"'
                )

            if not last_completed_at.exists():
                # Create it now. If it fails better now than later
                Debusine.write_last_completed_at(last_completed_at, None)

        debusine.on_work_request_completed(
            workspaces=workspaces,
            last_completed_at=last_completed_at,
            command=command,
            working_directory=Path.cwd(),
        )
