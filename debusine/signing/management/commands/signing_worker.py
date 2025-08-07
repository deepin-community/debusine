# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-signing command to run a signing worker."""

import asyncio
import logging
from typing import Any

from django.core.management import CommandParser

from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.tasks.models import WorkerType
from debusine.worker import Worker


class Command(DebusineBaseCommand):
    """Command to run a signing worker."""

    help = "Run a signing worker"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the signing_worker command."""
        parser.add_argument(
            "--log-file",
            type=str,
            help=(
                "Log to this file. Overrides log-file (in [General] section) "
                "from config.ini. If not specified anywhere logs to stderr."
            ),
        )
        parser.add_argument(
            "--log-level",
            help=(
                f"Minimum log level. Overrides log-level (in [General] "
                f"section) from config.ini. If not specified anywhere logs "
                f"{logging.getLevelName(Worker.DEFAULT_LOG_LEVEL)} and above."
            ),
            choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run a signing worker."""
        log_file_name = options["log_file"]

        worker = Worker(
            log_file=log_file_name,
            log_level=options["log_level"],
            worker_type=WorkerType.SIGNING,
        )
        asyncio.run(worker.main())

        raise SystemExit(0)
