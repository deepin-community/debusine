# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to manage workers."""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, cast

from django.core.management import CommandError, CommandParser
from django.db import transaction

from debusine.db.models import Token, WorkRequest, Worker
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import Workers
from debusine.tasks.models import WorkerType
from debusine.utils.input import EditorInteractionError, InvalidData, YamlEditor


class Command(DebusineBaseCommand):
    """Command to manage workers."""

    help = "Manage workers."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the worker command."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        create = subparsers.add_parser(
            "create", help="Create a worker and print its activation token."
        )
        create.add_argument(
            "--worker-type",
            choices=["external", "signing"],
            type=WorkerType,
            default=WorkerType.EXTERNAL,
        )
        create.add_argument(
            "fqdn", help="Fully-qualified domain name of the worker"
        )

        enable = subparsers.add_parser("enable", help="Enable a worker.")
        enable.add_argument(
            "--worker-type",
            choices=["external", "signing"],
            type=WorkerType,
            default=WorkerType.EXTERNAL,
        )
        enable.add_argument("worker", help="Name of the worker to enable")

        disable = subparsers.add_parser(
            "disable",
            help=(
                "Disable a worker. This disables the worker's token (so the "
                "worker cannot communicate with the server) and de-assigns "
                "work requests for the worker, which will be assigned to "
                "another worker. No attempt is made to stop any worker's "
                "operations, but the server will not accept any results."
            ),
        )
        disable.add_argument(
            "--worker-type",
            choices=["external", "signing"],
            type=WorkerType,
            default=WorkerType.EXTERNAL,
        )
        disable.add_argument("worker", help="Name of the worker to disable")

        edit_metadata = subparsers.add_parser(
            "edit_metadata", help="Edit a worker's metadata."
        )
        edit_metadata.add_argument(
            "--set",
            type=Path,
            help=(
                "Filename with the metadata in YAML"
                " (note: its contents will be displayed to users in the web UI)"
            ),
            metavar="PATH",
        )
        edit_metadata.add_argument(
            "worker", help="Name of the worker whose metadata should be edited"
        )

        list_ = subparsers.add_parser("list", help="List workers.")
        list_.add_argument(
            '--yaml', action="store_true", help="Machine readable YAML output"
        )

    def get_worker(self, name_or_token: str, worker_type: WorkerType) -> Worker:
        """Get a worker by name or token, checking its type."""
        worker = Worker.objects.get_worker_or_none(name_or_token)
        if worker is None:
            worker = Worker.objects.get_worker_by_token_key_or_none(
                name_or_token
            )

        if worker is None:
            raise CommandError("Worker not found", returncode=3)
        if worker.worker_type != worker_type:
            raise CommandError(
                f'Worker "{worker.name}" is of type "{worker.worker_type}", '
                f'not "{worker_type}"',
                returncode=4,
            )

        return worker

    def handle_create(self, *args: Any, **options: Any) -> NoReturn:
        """Create a worker and print its activation token."""
        activation_token = Token.objects.create_worker_activation()
        Worker.objects.create_with_fqdn(
            options["fqdn"],
            activation_token=activation_token,
            worker_type=options["worker_type"],
        )
        print(activation_token.key)

        raise SystemExit(0)

    def handle_enable(self, *args: Any, **options: Any) -> NoReturn:
        """Enable a worker."""
        worker = self.get_worker(options["worker"], options["worker_type"])
        # By this point the worker cannot be a Celery worker, and a database
        # constraint ensures that all non-Celery workers have a token.
        assert worker.token is not None

        worker.token.enable()

        raise SystemExit(0)

    def handle_disable(self, *args: Any, **options: Any) -> NoReturn:
        """Disable a worker."""
        worker = self.get_worker(options["worker"], options["worker_type"])
        # By this point the worker cannot be a Celery worker, and a database
        # constraint ensures that all non-Celery workers have a token.
        assert worker.token is not None

        with transaction.atomic():
            worker.token.disable()
            for work_request in worker.assigned_work_requests.filter(
                status__in={
                    WorkRequest.Statuses.PENDING,
                    WorkRequest.Statuses.RUNNING,
                }
            ):
                work_request.de_assign_worker()

        raise SystemExit(0)

    def _set_metadata_from_file(self, worker: Worker, path: Path) -> bool:
        """Set the worker's static metadata from a YAML file."""
        editor: YamlEditor[dict[str, Any]] = YamlEditor({})
        try:
            shutil.copy2(path, editor.editor_file)
        except OSError as e:
            self.stderr.write("Error: " + str(e))
            return False
        try:
            editor.read_edited()
        except InvalidData as e:
            self.stderr.write("Error: " + str(e))
            return False
        except EditorInteractionError as e:
            self.stderr.write("Error: " + str(e))
            return False
        if editor.value:
            worker.static_metadata = editor.value
            worker.save()
            self.stdout.write(f"debusine: metadata set for {worker.name}")
            return True
        else:
            self.stderr.write("Error: file is empty")
            return False

    def _edit_metadata(self, worker: Worker) -> bool:
        editor: YamlEditor[dict[str, Any]] = YamlEditor(worker.static_metadata)
        if editor.edit():
            worker.static_metadata = editor.value
            worker.save()
            self.stdout.write(f"debusine: metadata set for {worker.name}")
            return True
        else:
            return False

    def handle_edit_metadata(self, *args: Any, **options: Any) -> NoReturn:
        """Edit a worker's metadata."""
        worker_name = options["worker"]

        try:
            worker = Worker.objects.get(name=worker_name)
        except Worker.DoesNotExist:
            raise CommandError(
                f'Error: worker "{worker_name}" is not registered\n'
                f'Use the command `debusine-admin worker list` to list the '
                f'existing workers',
                returncode=3,
            )

        if options["set"]:
            if self._set_metadata_from_file(worker, options["set"]):
                raise SystemExit(0)
            else:
                raise SystemExit(3)
        else:
            if self._edit_metadata(worker):
                raise SystemExit(0)
            else:
                raise SystemExit(3)

    def handle_list(self, *args: Any, **options: Any) -> NoReturn:
        """List the workers."""
        workers = Worker.objects.all()
        Workers(options["yaml"]).print(workers, self.stdout)
        raise SystemExit(0)

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Dispatch the requested action."""
        func = cast(
            Callable[..., NoReturn],
            getattr(self, f"handle_{options['action']}", None),
        )
        func(*args, **options)
