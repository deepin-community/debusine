# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Debusine wait tasks.

These represent steps in workflows where debusine needs to wait until
something else happens.
"""

from debusine.server.tasks.wait.base import BaseWaitTask  # isort: split

# Sub-tasks need to be imported in order to be available to BaseTask
# (e.g. for BaseTask.is_valid_task_name). They are registered via
# BaseTask.__init_subclass__.
from debusine.server.tasks.wait.delay import Delay
from debusine.server.tasks.wait.external_debsign import ExternalDebsign

__all__ = [
    "BaseWaitTask",
    "Delay",
    "ExternalDebsign",
]
