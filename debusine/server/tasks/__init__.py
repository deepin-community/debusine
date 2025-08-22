# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Server-side Debusine tasks.

The tasks in this package run in a Celery worker.
"""
from debusine.server.tasks.base import BaseServerTask  # isort: split

# Sub-tasks need to be imported in order to be available to BaseTask
# (e.g. for BaseTask.is_valid_task_name). They are registered via
# BaseTask.__init_subclass__.
from debusine.server.tasks.aptmirror import APTMirror
from debusine.server.tasks.base import ServerTaskPermissionDenied
from debusine.server.tasks.cloud_provisioning import CloudProvisioning
from debusine.server.tasks.copy_collection_items import CopyCollectionItems
from debusine.server.tasks.create_experiment_workspace import (
    CreateExperimentWorkspace,
)
from debusine.server.tasks.generate_suite_indexes import GenerateSuiteIndexes
from debusine.server.tasks.noop import ServerNoop
from debusine.server.tasks.package_upload import PackageUpload
from debusine.server.tasks.update_suite_lintian_collection import (
    UpdateSuiteLintianCollection,
)

__all__ = [
    "APTMirror",
    "BaseServerTask",
    "CloudProvisioning",
    "CopyCollectionItems",
    "CreateExperimentWorkspace",
    "GenerateSuiteIndexes",
    "PackageUpload",
    "ServerNoop",
    "ServerTaskPermissionDenied",
    "UpdateSuiteLintianCollection",
]
