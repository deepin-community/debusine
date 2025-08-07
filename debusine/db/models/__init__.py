# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Data models for the db application."""


from debusine.db.models.artifacts import (
    Artifact,
    ArtifactRelation,
    FileInArtifact,
    FileUpload,
)
from debusine.db.models.assets import Asset, AssetUsage
from debusine.db.models.auth import (
    Group,
    Identity,
    SYSTEM_USER_NAME,
    Token,
    User,
    system_user,
)
from debusine.db.models.collections import (
    Collection,
    CollectionItem,
    CollectionItemMatchConstraint,
)
from debusine.db.models.files import (
    DEFAULT_FILE_STORE_NAME,
    File,
    FileInStore,
    FileStore,
    default_file_store,
)
from debusine.db.models.scopes import FileStoreInScope, Scope
from debusine.db.models.task_database import TaskDatabase
from debusine.db.models.work_requests import (
    NotificationChannel,
    WorkRequest,
    WorkflowTemplate,
)
from debusine.db.models.worker_pools import (
    ScopeWorkerPool,
    WorkerPool,
    WorkerPoolStatistics,
    WorkerPoolTaskExecutionStatistics,
)
from debusine.db.models.workers import Worker
from debusine.db.models.workspaces import Workspace, default_workspace

__all__ = [
    "Artifact",
    "ArtifactRelation",
    "Asset",
    "AssetUsage",
    "Collection",
    "CollectionItem",
    "CollectionItemMatchConstraint",
    "DEFAULT_FILE_STORE_NAME",
    "File",
    "FileInArtifact",
    "FileInStore",
    "FileStore",
    "FileStoreInScope",
    "FileUpload",
    "Group",
    "Identity",
    "NotificationChannel",
    "Scope",
    "ScopeWorkerPool",
    "SYSTEM_USER_NAME",
    "TaskDatabase",
    "Token",
    "User",
    "Worker",
    "WorkerPool",
    "WorkerPoolStatistics",
    "WorkerPoolTaskExecutionStatistics",
    "WorkflowTemplate",
    "WorkRequest",
    "Workspace",
    "default_file_store",
    "default_workspace",
    "system_user",
]
