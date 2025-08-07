# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine Workflow orchestration infrastructure."""

import sys
from typing import cast

from debusine.server.workflows.autopkgtest import AutopkgtestWorkflow
from debusine.server.workflows.base import Workflow, WorkflowValidationError
from debusine.server.workflows.create_experiment_workspace import (
    CreateExperimentWorkspaceWorkflow,
)
from debusine.server.workflows.debian_pipeline import DebianPipelineWorkflow
from debusine.server.workflows.lintian import LintianWorkflow
from debusine.server.workflows.make_signed_source import (
    MakeSignedSourceWorkflow,
)
from debusine.server.workflows.noop import NoopWorkflow
from debusine.server.workflows.package_publish import PackagePublishWorkflow
from debusine.server.workflows.package_upload import PackageUploadWorkflow
from debusine.server.workflows.piuparts import PiupartsWorkflow
from debusine.server.workflows.qa import QAWorkflow
from debusine.server.workflows.reverse_dependencies_autopkgtest import (
    ReverseDependenciesAutopkgtestWorkflow,
)
from debusine.server.workflows.sbuild import SbuildWorkflow
from debusine.server.workflows.update_environments import (
    UpdateEnvironmentsWorkflow,
)

# Import the documentation from where the code lives
__doc__ = cast(str, sys.modules[Workflow.__module__].__doc__)

__all__ = [
    "AutopkgtestWorkflow",
    "DebianPipelineWorkflow",
    "CreateExperimentWorkspaceWorkflow",
    "LintianWorkflow",
    "MakeSignedSourceWorkflow",
    "NoopWorkflow",
    "PackagePublishWorkflow",
    "PackageUploadWorkflow",
    "PiupartsWorkflow",
    "QAWorkflow",
    "ReverseDependenciesAutopkgtestWorkflow",
    "SbuildWorkflow",
    "UpdateEnvironmentsWorkflow",
    "Workflow",
    "WorkflowValidationError",
]
