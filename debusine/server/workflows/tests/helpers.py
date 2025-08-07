# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common test-helper code involving workflows."""

from typing import TypeVar

from debusine.server.workflows import Workflow
from debusine.server.workflows.models import BaseWorkflowData
from debusine.tasks.models import BaseDynamicTaskData
from debusine.tasks.tests.helper_mixin import TestBaseTask

WD = TypeVar("WD", bound=BaseWorkflowData)
DTD = TypeVar("DTD", bound=BaseDynamicTaskData)


class TestWorkflow(TestBaseTask[WD, DTD], Workflow[WD, DTD]):
    """Common test implementation of Workflow methods."""
