# PYTHON_ARGCOMPLETE_OK
#
# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine command line interface launcher."""

import sys

from debusine.client.cli import Cli


def entry_point() -> None:
    """Entry point for the packaging."""
    main = Cli(sys.argv[1:])
    main.execute()


if __name__ == '__main__':
    entry_point()
