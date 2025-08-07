# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to manage workers."""

from typing import Any

from django.core.management import CommandError, CommandParser
from django.db import transaction
from django.db.models import Q

from debusine.db.models import WorkRequest, Worker
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.tasks.models import WorkerType


class Command(DebusineBaseCommand):
    """Command to manage workers. E.g. enable and disable them."""

    help = 'Enables and disables workers'

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the manage_worker command."""
        parser.add_argument(
            "--worker-type",
            choices=["external", "signing"],
            type=WorkerType,
            default=WorkerType.EXTERNAL,
        )
        parser.add_argument(
            'action',
            choices=['enable', 'disable'],
            help="Enable or disable the worker. Disabling a worker makes the "
            "worker's token disabled (worker cannot communicate with the "
            "server) and de-assigns work requests for the worker, which "
            "will be assigned to another worker. No attempt is made to stop "
            "any worker's operations but the server will not accept any "
            "results",
        )
        parser.add_argument('worker', help='Name of the worker to modify')

    def handle(self, *args: Any, **options: Any) -> None:
        """Enable or disable the worker."""
        worker = Worker.objects.get_worker_or_none(options['worker'])
        if worker is None:
            worker = Worker.objects.get_worker_by_token_key_or_none(
                options['worker']
            )

        if worker is None:
            raise CommandError('Worker not found', returncode=3)
        if worker.worker_type != options["worker_type"]:
            raise CommandError(
                f'Worker "{worker.name}" is of type "{worker.worker_type}", '
                f'not "{options["worker_type"]}"',
                returncode=4,
            )
        # By this point the worker cannot be a Celery worker, and a database
        # constraint ensures that all non-Celery workers have a token.
        assert worker.token is not None

        action = options['action']

        if action == 'enable':
            worker.token.enable()
        elif action == 'disable':
            with transaction.atomic():
                worker.token.disable()
                for work_request in worker.assigned_work_requests.filter(
                    Q(status=WorkRequest.Statuses.RUNNING)
                    | Q(status=WorkRequest.Statuses.PENDING)
                ):
                    work_request.de_assign_worker()
        else:  # pragma: no cover
            pass  # Never reached
