# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Waiter utility: waits until success/failure and fails after timeout."""

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partialmethod
from typing import Any


class Waiter:
    """Waits for a function to return True or fails."""

    @staticmethod
    def _wait_for(
        expected: Any, timeout: float, func: Callable[[], Any]
    ) -> bool:
        """
        Execute func until it returns expected, or fails on timeout.

        :param expected: expected output of the func
        :param timeout: maximum number of seconds to wait
        :param func: function that gets executed
        :return: True if it succeeded, False if it timed out
        """
        start_at = datetime.now()

        while func() != expected:
            if datetime.now() - start_at > timedelta(seconds=timeout):
                return False

            time.sleep(0.2)

        return True

    wait_for_success: partialmethod[bool] = partialmethod(_wait_for, True)
    wait_for_failure: partialmethod[bool] = partialmethod(_wait_for, False)
