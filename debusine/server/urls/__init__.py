# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""URLs for the server application - API."""

from django.urls import include, path

from debusine.server.views.artifacts import (
    ArtifactRelationsView,
    ArtifactView,
    UploadFileView,
)
from debusine.server.views.assets import AssetPermissionCheckView, AssetView
from debusine.server.views.base import Whoami
from debusine.server.views.enroll import EnrollView
from debusine.server.views.lookups import LookupMultipleView, LookupSingleView
from debusine.server.views.service_status import ServiceStatusView
from debusine.server.views.work_requests import (
    WorkRequestExternalDebsignView,
    WorkRequestRetryView,
    WorkRequestUnblockView,
    WorkRequestView,
)
from debusine.server.views.workers import (
    GetNextWorkRequestView,
    RegisterView,
    UpdateWorkRequestAsCompletedView,
    UpdateWorkerDynamicMetadataView,
)
from debusine.server.views.workflows import WorkflowTemplateView, WorkflowView

app_name = 'server'


urlpatterns = [
    path(
        'auth/',
        include('rest_framework.urls', namespace='rest_framework'),
    ),
    path("enroll/", EnrollView.as_view(), name="enroll"),
    path(
        '1.0/whoami/',
        Whoami.as_view(),
        name='whoami',
    ),
    path(
        '1.0/work-request/get-next-for-worker/',
        GetNextWorkRequestView.as_view(),
        name='work-request-get-next',
    ),
    path(
        '1.0/work-request/',
        WorkRequestView.as_view(),
        name='work-requests',
    ),
    path(
        '1.0/work-request/<int:work_request_id>/',
        WorkRequestView.as_view(),
        name='work-request-detail',
    ),
    path(
        '1.0/work-request/<int:work_request_id>/completed/',
        UpdateWorkRequestAsCompletedView.as_view(),
        name='work-request-completed',
    ),
    path(
        '1.0/work-request/<int:work_request_id>/retry/',
        WorkRequestRetryView.as_view(),
        name='work-requests-retry',
    ),
    path(
        '1.0/work-request/<int:work_request_id>/unblock/',
        WorkRequestUnblockView.as_view(),
        name='work-request-unblock',
    ),
    path(
        '1.0/work-request/<int:work_request_id>/external-debsign/',
        WorkRequestExternalDebsignView.as_view(),
        name='work-request-external-debsign',
    ),
    path(
        "1.0/artifact/",
        ArtifactView.as_view(),
        name="artifact-create",
    ),
    path(
        "1.0/artifact/<int:artifact_id>/",
        ArtifactView.as_view(),
        name="artifact",
    ),
    path(
        "1.0/artifact/<int:artifact_id>/files/<path:file_path>/",
        UploadFileView.as_view(),
        name="upload-file",
    ),
    path(
        "1.0/asset/",
        AssetView.as_view(),
        name="asset",
    ),
    path(
        (
            "1.0/asset/<str:asset_category>/<str:asset_slug>/"
            "<str:permission_name>/"
        ),
        AssetPermissionCheckView.as_view(),
        name="asset-permission-check",
    ),
    path(
        "1.0/lookup/single/", LookupSingleView.as_view(), name="lookup-single"
    ),
    path(
        "1.0/lookup/multiple/",
        LookupMultipleView.as_view(),
        name="lookup-multiple",
    ),
    path('1.0/worker/register/', RegisterView.as_view(), name='register'),
    path(
        '1.0/worker/dynamic-metadata/',
        UpdateWorkerDynamicMetadataView.as_view(),
        name='worker-dynamic-metadata',
    ),
    path(
        "1.0/artifact-relation/",
        ArtifactRelationsView.as_view(),
        name="artifact-relation-list",
    ),
    path(
        "1.0/artifact-relation/<int:pk>/",
        ArtifactRelationsView.as_view(),
        name="artifact-relation-detail",
    ),
    path(
        "1.0/workflow-template/",
        WorkflowTemplateView.as_view(),
        name="workflow-templates",
    ),
    path(
        "1.0/workflow-template/<int:pk>/",
        WorkflowTemplateView.as_view(),
        name="workflow-template-detail",
    ),
    path("1.0/workflow/", WorkflowView.as_view(), name="workflows"),
    path(
        "1.0/service-status/",
        ServiceStatusView.as_view(),
        name="service-status",
    ),
]
