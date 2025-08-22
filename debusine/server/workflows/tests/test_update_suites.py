# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the ``update_suites`` workflow."""

from datetime import timedelta
from unittest import mock

from django.db import connection
from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.db.models import Collection, CollectionItem, WorkRequest
from debusine.server.workflows import UpdateSuitesWorkflow
from debusine.server.workflows.base import orchestrate_workflow
from debusine.test.django import TestCase


class UpdateSuitesWorkflowTests(TestCase):
    """Unit tests for :py:class:`UpdateSuitesWorkflow`."""

    def create_binary_package_item(self, suite: Collection) -> CollectionItem:
        """Create a minimal debian:binary-package item in the suite."""
        return CollectionItem.objects.create_from_artifact(
            self.playground.create_minimal_binary_package_artifact(),
            parent_collection=suite,
            name="hello_1.0_amd64",
            data={},
            created_by_user=self.playground.get_default_user(),
        )

    def create_release_item(self, suite: Collection) -> CollectionItem:
        """Create a minimal debian:repository-index item in the suite."""
        return CollectionItem.objects.create_from_artifact(
            self.playground.create_artifact(
                category=ArtifactCategory.REPOSITORY_INDEX, data={}
            )[0],
            parent_collection=suite,
            name="index:Release",
            data={"path": "Release"},
            created_by_user=self.playground.get_default_user(),
        )

    def test_find_stale_suites(self) -> None:
        """Suites with changes after their latest Release file are selected."""
        workspaces = [
            self.playground.create_workspace(name=name, public=True)
            for name in ("one", "two")
        ]
        suites = {
            name: self.playground.create_collection(
                name, CollectionCategory.SUITE, workspace=workspace
            )
            for name, workspace in (
                ("empty-unchanged", workspaces[0]),
                ("empty-item-created", workspaces[0]),
                ("release-unchanged", workspaces[0]),
                ("release-item-created", workspaces[0]),
                ("release-item-removed", workspaces[0]),
                ("different-workspace", workspaces[1]),
            )
        }
        self.create_binary_package_item(suites["release-unchanged"])
        release_item_removed_binary = self.create_binary_package_item(
            suites["release-item-removed"]
        )
        self.create_release_item(suites["release-unchanged"])
        self.create_release_item(suites["release-item-created"])
        self.create_release_item(suites["release-item-removed"])
        self.create_binary_package_item(suites["empty-item-created"])
        self.create_binary_package_item(suites["release-item-created"])
        release_item_removed_binary.removed_at = timezone.now()
        release_item_removed_binary.save()
        self.create_binary_package_item(suites["different-workspace"])
        workflow = self.playground.create_workflow(
            task_name="update_suites", workspace=workspaces[0]
        )

        self.assertEqual(
            UpdateSuitesWorkflow(workflow)._find_stale_suite_names(),
            sorted(
                [
                    "empty-item-created",
                    "release-item-created",
                    "release-item-removed",
                ]
            ),
        )

    def test_find_stale_suites_force_basic_indexes(self) -> None:
        """``force_basic_indexes`` selects all suites in the workspace."""
        workspaces = [
            self.playground.create_workspace(name=name, public=True)
            for name in ("one", "two")
        ]
        for name, workspace in (
            ("one", workspaces[0]),
            ("two", workspaces[0]),
            ("three", workspaces[0]),
            ("four", workspaces[1]),
        ):
            self.playground.create_collection(
                name, CollectionCategory.SUITE, workspace=workspace
            )
        workflow = self.playground.create_workflow(
            task_name="update_suites",
            task_data={"force_basic_indexes": True},
            workspace=workspaces[0],
        )

        self.assertEqual(
            UpdateSuitesWorkflow(workflow)._find_stale_suite_names(),
            sorted(["one", "two", "three"]),
        )

    def test_populate(self) -> None:
        """``populate`` creates child work requests for all stale suites."""
        suites = [
            self.playground.create_collection(name, CollectionCategory.SUITE)
            for name in ("one", "two")
        ]
        self.create_binary_package_item(suites[0])
        self.create_binary_package_item(suites[1])
        workflow = self.playground.create_workflow(
            task_name="update_suites",
            workspace=self.playground.get_default_workspace(),
        )

        workflow.mark_running()
        orchestrate_workflow(workflow)

        with connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_TIMESTAMP")
            [now] = cursor.fetchone()
        self.assertQuerySetEqual(
            workflow.children.values_list(
                "task_type",
                "task_name",
                "task_data",
                "workflow_data_json",
                "status",
            ),
            [
                (
                    TaskTypes.SERVER,
                    "generatesuiteindexes",
                    {
                        "suite_collection": "one@debian:suite",
                        "generate_at": now.isoformat(),
                    },
                    {
                        "display_name": "Generate indexes for one",
                        "step": "generate-suite-indexes-one",
                    },
                    WorkRequest.Statuses.PENDING,
                ),
                (
                    TaskTypes.SERVER,
                    "generatesuiteindexes",
                    {
                        "suite_collection": "two@debian:suite",
                        "generate_at": now.isoformat(),
                    },
                    {
                        "display_name": "Generate indexes for two",
                        "step": "generate-suite-indexes-two",
                    },
                    WorkRequest.Statuses.PENDING,
                ),
            ],
        )

    def test_populate_already_exists(self) -> None:
        """``populate`` skips work requests that already exist."""
        suites = [
            self.playground.create_collection(name, CollectionCategory.SUITE)
            for name in ("one", "two")
        ]
        self.create_binary_package_item(suites[0])
        workflow = self.playground.create_workflow(
            task_name="update_suites",
            workspace=self.playground.get_default_workspace(),
        )

        workflow.mark_running()
        previous = timezone.now() - timedelta(hours=1)
        with mock.patch(
            "debusine.server.workflows.update_suites.UpdateSuitesWorkflow"
            "._get_current_transaction_timestamp",
            return_value=previous,
        ):
            orchestrate_workflow(workflow)

        self.create_binary_package_item(suites[1])

        orchestrate_workflow(workflow)

        with connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_TIMESTAMP")
            [now] = cursor.fetchone()
        self.assertQuerySetEqual(
            workflow.children.values_list(
                "task_type",
                "task_name",
                "task_data",
                "workflow_data_json",
                "status",
            ),
            [
                (
                    TaskTypes.SERVER,
                    "generatesuiteindexes",
                    {
                        "suite_collection": "one@debian:suite",
                        "generate_at": previous.isoformat(),
                    },
                    {
                        "display_name": "Generate indexes for one",
                        "step": "generate-suite-indexes-one",
                    },
                    WorkRequest.Statuses.PENDING,
                ),
                (
                    TaskTypes.SERVER,
                    "generatesuiteindexes",
                    {
                        "suite_collection": "two@debian:suite",
                        "generate_at": now.isoformat(),
                    },
                    {
                        "display_name": "Generate indexes for two",
                        "step": "generate-suite-indexes-two",
                    },
                    WorkRequest.Statuses.PENDING,
                ),
            ],
        )

    def test_get_label(self) -> None:
        """Test ``get_label``."""
        workflow = self.playground.create_workflow(
            task_name="update_suites",
            workspace=self.playground.get_default_workspace(),
        )

        self.assertEqual(
            UpdateSuitesWorkflow(workflow).get_label(),
            "update all stale suites",
        )
