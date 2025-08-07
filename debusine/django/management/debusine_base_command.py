# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""DebusineBaseCommand extends BaseCommand with extra functionality."""

import signal
import types
from typing import Any, NoReturn

import yaml
from django.core.management import BaseCommand, CommandError
from django.db.utils import DatabaseError
from yaml import YAMLError


class DebusineBaseCommand(BaseCommand):
    """Extend Django Base Command with functionality used by Debusine."""

    @staticmethod
    def _exit_handler(signum: int, frame: types.FrameType | None) -> NoReturn:
        """
        Exit without printing Python's default stack trace.

        A user can Control+C and debusine-admin does not print all the
        stack trace. This could happen in any command but is more obvious
        on the interactive commands (such as `remove_tokens` when it asks for
        confirmation)
        """
        signum, frame  # fake usage for vulture
        raise SystemExit(3)

    def cleanup_arguments(
        self, *args: Any, **options: Any  # noqa: U100
    ) -> None:
        """
        Clean up objects created by parsing arguments.

        For example, this can be used to close files created by
        :py:class:`argparse.FileType`.
        """

    def execute(self, *args: Any, **options: Any) -> None:
        """
        Debusine BaseCommand common functionality.

        - Catch OperationError exceptions to print a helpful message
        - Set self.verbosity for --verbosity/-v option

        Possible OperationErrors: database not reachable, invalid database
        credentials, etc.
        """
        try:
            signal.signal(signal.SIGINT, self._exit_handler)
            signal.signal(signal.SIGTERM, self._exit_handler)

            self.verbosity = options["verbosity"]

            super().execute(*args, **options)
        except DatabaseError as exc:
            raise CommandError(f"Database error: {exc}", returncode=3)
        finally:
            self.cleanup_arguments(*args, **options)

    def print_verbose(self, msg: str) -> None:
        r"""Write msg + "\n" to self.stdout if self.verbosity > 1."""
        if self.verbosity > 1:
            self.stdout.write(msg)

    def parse_yaml_data(self, data_yaml: str) -> Any:
        """Parse data_yaml. If not valid raise CommandError."""
        try:
            data = yaml.safe_load(data_yaml)
        except YAMLError as err:
            raise CommandError(f"Error parsing YAML: {err}", returncode=3)

        return data
