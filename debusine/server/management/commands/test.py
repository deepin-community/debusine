# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Decorate 'test' command."""

import os
import shutil
import sys
import tempfile
from typing import Any

from django.conf import settings
from django.core.management import CommandParser
from django.core.management.commands import test


def setup_temp_data_directories() -> None:
    """
    Create and configure temporary directories for use by the test suite.

    Do not pollute the development/production directories, such as the store.
    Called through from LOGGING_CONFIG, after settings are fully processed.
    """
    tempdir = tempfile.mkdtemp(prefix="debusine-testsuite-data-")
    settings.DEBUSINE_DATA_PATH = tempdir
    settings.DEBUSINE_CACHE_DIRECTORY = os.path.join(tempdir, 'cache')
    settings.DEBUSINE_TEMPLATE_DIRECTORY = os.path.join(tempdir, 'templates')
    settings.DEBUSINE_UPLOAD_DIRECTORY = os.path.join(tempdir, 'uploads')
    settings.DEBUSINE_STORE_DIRECTORY = os.path.join(tempdir, 'store')
    settings.DEBUSINE_LOG_DIRECTORY = os.path.join(tempdir, 'logs')
    os.mkdir(settings.DEBUSINE_CACHE_DIRECTORY)
    os.mkdir(settings.DEBUSINE_TEMPLATE_DIRECTORY)
    os.mkdir(settings.DEBUSINE_UPLOAD_DIRECTORY)
    os.mkdir(settings.DEBUSINE_STORE_DIRECTORY)
    os.mkdir(settings.DEBUSINE_LOG_DIRECTORY)

    # We could send signals for tests that cache settings, and restore them:
    # https://docs.djangoproject.com/en/3.2/ref/signals/#django.test.signals.setting_changed
    # but we're early in the start-up process, this complexifies the code,
    # and it's hard to assess the side effects, so let's not.


class Command(test.Command):
    """Decorate 'test' command."""

    def add_arguments(self, parser: CommandParser) -> None:
        """Register new --keepdata option, similar to --keepdb."""
        super().add_arguments(parser)
        parser.add_argument(
            "--keepdata",
            action="store_true",
            help="Preserves the test data directory between runs.",
        )

    def handle(self, *args: Any, **kwargs: Any) -> None:
        """Clean-up or keep temporary test directories hierarchy."""
        try:
            super().handle(*args, **kwargs)
        finally:
            if kwargs["keepdata"]:
                if kwargs["verbosity"] >= 1:
                    sys.stderr.write(
                        "Preserving test data directory '%s'...\n"
                        % settings.DEBUSINE_DATA_PATH
                    )
            else:
                shutil.rmtree(settings.DEBUSINE_DATA_PATH)
