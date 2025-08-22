# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common utilities for Django-based packages."""

import os
import sys


def in_test_suite() -> bool:
    """Check whether we're running in the test suite."""
    # `"PYTEST_VERSION" in os.environ` would be a cleaner and simpler way to
    # detect whether pytest is running, but that requires pytest >= 8.2.
    return (
        sys.argv[1:2] == ["test"]
        or "pytest" in sys.argv[0].split(os.sep)
        or "PYTEST_XDIST_WORKER" in os.environ
    )
