# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Advisory database locks for Debusine."""

from enum import IntEnum


class LockType(IntEnum):
    """
    The type of a database lock.

    This is a 32-bit value used as the first parameter when taking a lock.
    The second parameter, also a 32-bit value, is available for the part of
    the application that owns each lock type.
    """

    #: Used by :py:class:`debusine.server.tasks.aptmirror`.
    APT_MIRROR = 1

    #: Used by :py:class:`debusine.server.tasks.generate_suite_indexes`.
    GENERATE_SUITE_INDEXES = 2


class LockError(Exception):
    """A lock could not be acquired."""
