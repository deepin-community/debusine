# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to manage workers."""

import warnings
from typing import Any, NoReturn

from django.core.management import CommandParser

from debusine.server.management.commands import worker
from debusine.tasks.models import WorkerType


class Command(worker.Command):
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

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Forward to `debusine-admin worker enable` or `... disable`."""
        action = options["action"]
        warnings.warn(
            f"The `debusine-admin manage_worker {action}` command has been "
            f"deprecated in favour of `debusine-admin worker {action}`",
            DeprecationWarning,
        )
        super().handle(*args, **options)
