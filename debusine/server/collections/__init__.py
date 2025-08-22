# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Collection managers.

Provides interfaces to interact with Collections: add items, remove,
lookups, etc.

Each specific CollectionManager implement their own business logic.
"""

from django.conf import settings

from debusine.server.collections.base import (
    CollectionManagerInterface,
    ItemAdditionError,
    ItemRemovalError,
)
from debusine.server.collections.debian_archive import DebianArchiveManager
from debusine.server.collections.debian_environments import (
    DebianEnvironmentsManager,
)
from debusine.server.collections.debian_package_build_logs import (
    DebianPackageBuildLogsManager,
)
from debusine.server.collections.debian_qa_results import DebianQAResultsManager
from debusine.server.collections.debian_suite import DebianSuiteManager
from debusine.server.collections.debian_suite_lintian import (
    DebianSuiteLintianManager,
)
from debusine.server.collections.task_history import TaskHistoryManager
from debusine.server.collections.workflow_internal import (
    WorkflowInternalManager,
)

__all__ = [
    "CollectionManagerInterface",
    "DebianArchiveManager",
    "DebianEnvironmentsManager",
    "DebianPackageBuildLogsManager",
    "DebianQAResultsManager",
    "DebianSuiteLintianManager",
    "DebianSuiteManager",
    "ItemAdditionError",
    "ItemRemovalError",
    "TaskHistoryManager",
    "WorkflowInternalManager",
]

if getattr(settings, "TEST_MODE", False):  # pragma: no cover
    # Register a manager to make debusine:test collections easily usable by
    # any test.  This manager doesn't implement any specific semantics, but
    # it at least allows basic name lookups to work.
    from debusine.server.collections.tests.test_base import (  # noqa: F401
        DebusineTestManager,
    )

    __all__.append("DebusineTestManager")
