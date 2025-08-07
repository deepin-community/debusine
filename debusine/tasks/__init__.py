# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Debusine tasks.

Import this module using for example:
from debusine.tasks import RunCommandTask, TaskConfigError

Debusine tasks such as sbuild become available
"""

import sys
from typing import cast

from debusine.tasks._task import (  # isort: split
    BaseExternalTask,
    BaseTask,
    BaseTaskWithExecutor,
    DefaultDynamicData,
    ExtraRepositoryMixin,
    RunCommandTask,
    TaskConfigError,
    get_environment,
)

# Sub-tasks need to be imported in order to be available to BaseTask
# (e.g. for BaseTask.is_valid_task_name). They are registered via
# BaseTask.__init_subclass__.
from debusine.tasks.assemble_signed_source import AssembleSignedSource
from debusine.tasks.autopkgtest import Autopkgtest
from debusine.tasks.blhc import Blhc
from debusine.tasks.debdiff import DebDiff
from debusine.tasks.extract_for_signing import ExtractForSigning
from debusine.tasks.lintian import Lintian
from debusine.tasks.makesourcepackageupload import MakeSourcePackageUpload
from debusine.tasks.mergeuploads import MergeUploads
from debusine.tasks.mmdebstrap import MmDebstrap
from debusine.tasks.noop import Noop
from debusine.tasks.piuparts import Piuparts
from debusine.tasks.sbuild import Sbuild
from debusine.tasks.simplesystemimagebuild import SimpleSystemImageBuild

# Import the documentation from where the code lives
__doc__ = cast(str, sys.modules[BaseTask.__module__].__doc__)

__all__ = [
    "AssembleSignedSource",
    "Autopkgtest",
    "BaseExternalTask",
    "BaseTask",
    "BaseTaskWithExecutor",
    "Blhc",
    "DebDiff",
    "DefaultDynamicData",
    "ExtraRepositoryMixin",
    "ExtractForSigning",
    "Lintian",
    "MakeSourcePackageUpload",
    "MergeUploads",
    "MmDebstrap",
    "Noop",
    "Piuparts",
    "RunCommandTask",
    "Sbuild",
    "SimpleSystemImageBuild",
    "TaskConfigError",
    "get_environment",
]
