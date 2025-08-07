# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine worker launcher."""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from debusine.worker import Worker
from debusine.worker.config import ConfigHandler


def main(argv: Sequence[str]) -> None:
    """Run the debusine-worker command (main entry-point)."""
    config_paths = ', '.join(ConfigHandler.default_directories)
    parser = argparse.ArgumentParser(
        prog='debusine-worker',
        description=f'Start Debusine worker process: register if needed and '
        f'connects to the server. The configuration is searched in '
        f'the files config.ini and token in the '
        f'directories {config_paths})',
    )
    parser.add_argument(
        '--log-file',
        type=str,
        help='Log to this file. Overrides log-file (in [General] section) '
        'from config.ini. If not specified anywhere logs to stderr.',
    )
    parser.add_argument(
        '--log-level',
        help='Minimum log level. Overrides log-level (in [General] section) '
        'from config.ini. If not specified anywhere logs '
        f'{logging.getLevelName(Worker.DEFAULT_LOG_LEVEL)} and above.',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
    )

    args = parser.parse_args(argv)

    log_file_name = args.log_file

    worker = Worker(log_file=log_file_name, log_level=args.log_level)

    asyncio.run(worker.main())


def entry_point() -> None:  # pragma: no cover
    """Entry point for the packaging."""
    main(sys.argv[1:])


if __name__ == '__main__':
    entry_point()
