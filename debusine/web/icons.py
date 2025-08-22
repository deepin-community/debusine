# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Icon selection for debusine UI.

This module defines what icons are used in the UI to represent Debusine
concepts.
"""

import enum


class Icons(enum.StrEnum):
    """Maps constants to bootstrap5 icon names."""

    # Concepts
    ARTIFACT_RELATION_SOURCE = "arrow-left"
    ARTIFACT_RELATION_TARGET = "arrow-right"
    ARTIFACTS_INPUT = "box-arrow-in-down-right"
    ARTIFACTS_OUTPUT = "box-arrow-down-right"
    CATEGORY = "postcard"
    COLLECTION = "collection"
    COLLECTIONS = "stack"
    USER = "person-circle"
    WORKER = "pc-horizontal"
    WORKER_EXTERNAL = "pc-horizontal"
    WORKER_SIGNING = "patch-check"
    WORKFLOW = "diagram-3"
    WORK_REQUEST = "hammer"
    WORK_REQUEST_STATUS = "heart-pulse"
    WORK_REQUEST_SUPERSEDED = "arrow-repeat"
    WORK_REQUEST_SUPERSEDES = "shuffle"
    WORKSPACE = "box"

    # Common attributes
    CREATED_AT = "clock"
    DURATION = "stopwatch"
    EXPIRE_AT = "hourglass-split"
    STARTED_AT = "rocket-takeoff"

    # Actions
    ARTIFACT_DOWNLOAD = "download"
    COLLECTION_ITEM_DETAILS = "link-45deg"
    FILE_DOWNLOAD = "file-earmark-arrow-down"
    FILE_VIEW = "file-earmark"
    FILE_VIEW_RAW = "file-earmark-code"
    WORK_REQUEST_RETRY = "arrow-repeat"
    WORK_REQUEST_ABORT = "x"
