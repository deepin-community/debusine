# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Debusine signing tasks.

The tasks in this package run in a signing worker.
"""

from debusine.signing.tasks.base import BaseSigningTask  # isort: split

# Sub-tasks need to be imported in order to be available to BaseTask
# (e.g. for BaseTask.is_valid_task_name). They are registered via
# BaseTask.__init_subclass__.
from debusine.signing.tasks.debsign import Debsign  # noqa: I202
from debusine.signing.tasks.generate_key import GenerateKey  # noqa: I202
from debusine.signing.tasks.noop import SigningNoop  # noqa: I202
from debusine.signing.tasks.sign import Sign  # noqa: I202
from debusine.signing.tasks.sign_repository_index import (  # noqa: I202
    SignRepositoryIndex,
)

__all__ = [
    "BaseSigningTask",
    "Debsign",
    "GenerateKey",
    "Sign",
    "SignRepositoryIndex",
    "SigningNoop",
]
