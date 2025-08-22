# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the work request views."""
import copy
import json
import textwrap
from collections.abc import Iterable
from datetime import timedelta
from itertools import zip_longest
from typing import Any, ClassVar, Literal
from unittest.mock import patch

import lxml
import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import constants as messages_constants
from django.contrib.messages import get_messages
from django.contrib.messages.storage.base import Message
from django.core.validators import ProhibitNullCharactersValidator
from django.template.response import SimpleTemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from lxml import etree, html
from rest_framework import status

from debusine.artifacts import LintianArtifact
from debusine.artifacts.models import (
    CollectionCategory,
    RuntimeStatistics,
    TaskTypes,
)
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    TaskDatabase,
    WorkRequest,
    WorkflowTemplate,
    Workspace,
)
from debusine.db.playground import scenarios
from debusine.server.workflows.models import (
    WorkRequestManualUnblockAction,
    WorkRequestManualUnblockData,
    WorkRequestManualUnblockLog,
    WorkRequestWorkflowData,
)
from debusine.tasks import Lintian, Noop
from debusine.tasks.models import (
    BaseDynamicTaskData,
    MmDebstrapBootstrapOptions,
    MmDebstrapData,
    NoopData,
    OutputData,
    OutputDataError,
    SystemBootstrapRepository,
)
from debusine.test.django import AllowAll, TestCase, override_permission
from debusine.web.templatetags.artifacts import artifact_category_label
from debusine.web.views import ui_shortcuts
from debusine.web.views.tests.utils import ViewTestMixin
from debusine.web.views.work_request import (
    WorkRequestDetailView,
    WorkRequestPlugin,
)


class WorkRequestDetailViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`WorkRequestDetailView`."""

    scenario = scenarios.DefaultContext()
    source: ClassVar[Artifact]
    work_request: ClassVar[WorkRequest]

    INTERNAL_COLLECTION_ID = "internal-collection"

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common objects."""
        super().setUpTestData()
        started_at = timezone.now()
        duration = 73
        completed_at = started_at + timedelta(seconds=duration)

        environment_item = cls.playground.create_debian_environment(
            workspace=cls.scenario.workspace
        )
        assert environment_item.artifact is not None
        environment = environment_item.artifact

        cls.source = cls.playground.create_source_artifact(
            workspace=cls.scenario.workspace
        )
        cls.work_request = cls.playground.create_sbuild_work_request(
            source=cls.source,
            architecture="amd64",
            environment=environment,
            workspace=cls.scenario.workspace,
        )
        cls.work_request.mark_running()
        cls.work_request.assign_worker(cls.playground.create_worker())
        cls.work_request.mark_completed(WorkRequest.Results.SUCCESS)
        cls.work_request.started_at = started_at
        cls.work_request.completed_at = completed_at
        cls.work_request.save()

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        self.assertSetsCurrentWorkspace(
            self.scenario.workspace,
            self.work_request.get_absolute_url(),
        )

    def assertArtifacts(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        table_id: Literal["input-artifacts", "output-artifacts"],
        artifacts: Iterable[Artifact],
    ) -> None:
        """Ensure that the given artifacts are listed."""
        table = tree.xpath(f"//table[@id='{table_id}']")[0]
        tbody = table.tbody[0]

        for tr, artifact in zip_longest(tbody.tr, artifacts):
            with self.subTest(str(artifact)):
                self.assertTextContentEqual(
                    tr.td[0], artifact_category_label(artifact).capitalize()
                )
                self.assertEqual(
                    tr.td[1].a.get("href"), artifact.get_absolute_url()
                )
                label = artifact.get_label()
                self.assertTextContentEqual(tr.td[1].a, label)
                if artifact.fileinartifact_set.filter(complete=False).exists():
                    self.assertTextContentEqual(
                        tr.td[1], f"{label} (incomplete)"
                    )
                else:
                    self.assertTextContentEqual(tr.td[1], label)

    def assertHasCardYaml(
        self, tree: lxml.objectify.ObjectifiedElement, *, div_id: str
    ) -> Any:
        """Ensure that metadata is shown in the page, and return it parsed."""
        metadata = self.assertHasElement(tree, f"//div[@id='{div_id}']")
        return yaml.safe_load("".join(metadata.div[0].itertext()))

    def assertHasCardJson(
        self, tree: lxml.objectify.ObjectifiedElement, *, div_id: str
    ) -> Any:
        """Ensure that metadata is shown in the page, and return it parsed."""
        metadata = self.assertHasElement(tree, f"//div[@id='{div_id}']")
        return json.loads("".join(metadata.div[0].itertext()))

    def test_get_title(self) -> None:
        """Test get_title method."""
        view = WorkRequestDetailView()
        view.object = self.work_request
        self.assertEqual(
            view.get_title(),
            f"{self.work_request.pk}: {self.work_request.get_label()}",
        )

    def test_get_work_request(self) -> None:
        """View detail return work request information."""
        artifact, _ = self.playground.create_artifact(
            work_request=self.work_request
        )

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        sidebar = response.context["sidebar_items"]
        self.assertEqual(sidebar[0].value, "build hello")
        self.assertEqual(
            sidebar[0].detail,
            f'{self.work_request.task_type} {self.work_request.task_name} task',
        )
        self.assertEqual(sidebar[1].content, "-")
        self.assertEqual(
            sidebar[2].content,
            '<span class="badge text-bg-primary">Completed</span>'
            ' <span class="badge text-bg-success">Success</span>',
        )
        self.assertEqual(sidebar[3].content, self.work_request.workspace.name)
        self.assertEqual(sidebar[4].content, str(self.work_request.created_by))
        assert self.work_request.worker is not None
        self.assertEqual(sidebar[6].content, self.work_request.worker.name)
        self.assertEqual(sidebar[7].content, "0\xa0minutes")
        self.assertEqual(sidebar[8].content, "1\xa0minute")
        self.assertEqual(sidebar[9].content, "Never")

        self.assertArtifacts(
            tree,
            "input-artifacts",
            Artifact.objects.filter(
                pk__in=self.work_request.get_task().get_input_artifacts_ids()
            ).order_by("id"),
        )
        self.assertEqual(
            tree.xpath("//p[@id='generic-description']")[0],
            "Task implementing a Debian package build with sbuild.",
        )
        self.assertArtifacts(tree, "output-artifacts", [artifact])

        # Not in a workflow
        self.assertNotContains(
            response, "<h2>Workflow information</h2>", html=True
        )

        self.assertNotIn("task_data_original-work_request", response.context)

        self.assertFalse(tree.xpath("//p[@id='task_data_not_configured']"))

    def test_get_work_request_no_input_output_artifacts(self) -> None:
        work_request = self.playground.create_work_request()

        response = self.client.get(work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        self.assertTextContentEqual(
            self.assertHasElement(tree, "//p[@id='no-input-artifacts']"),
            "No input artifacts.",
        )
        self.assertTextContentEqual(
            self.assertHasElement(tree, "//p[@id='no-output-artifacts']"),
            "No artifacts produced.",
        )

    def test_get_configured_work_request(self) -> None:
        """View detail return work request information."""
        # Simulate a configuration step
        self.work_request.configured_task_data = (
            self.work_request.task_data.copy()
        )
        self.work_request.configured_task_data["host_architecture"] = "arm64"
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertIn("task_data", response.context)
        self.assertTrue(response.context["task_data_configured"])
        orig = self.assertHasCardYaml(tree, div_id="card-task_data")
        configured = self.assertHasCardYaml(
            tree, div_id="card-configured_task_data"
        )

        self.assertEqual(orig["host_architecture"], "amd64")
        self.assertEqual(configured["host_architecture"], "arm64")
        self.assertEqual(orig["host_architecture"], "amd64")

        dynamic_data = self.assertHasCardYaml(tree, div_id="card-dynamic_data")
        configured_task_data = self.assertHasCardYaml(
            tree, div_id="card-configured_task_data"
        )

        self.assertEqual(dynamic_data, self.work_request.dynamic_task_data)
        self.assertEqual(
            configured_task_data, self.work_request.configured_task_data
        )

    def test_get_work_request_task_data_not_configured(self) -> None:
        self.work_request.configured_task_data = None
        self.work_request.save()
        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        task_data_not_configured_p = self.assertHasElement(
            tree, "//p[@id='task_data_not_configured']"
        )
        self.assertTextContentEqual(
            task_data_not_configured_p, "Work request is not configured."
        )
        self.assertFalse(tree.xpath("//p[@id='dynamic_data_not_available']"))

    def test_get_work_request_dynamic_data_not_available(self) -> None:
        self.work_request.dynamic_task_data = None
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        task_dynamic_data_not_available_p = self.assertHasElement(
            tree, "//p[@id='dynamic_data_not_available']"
        )
        self.assertTextContentEqual(
            task_dynamic_data_not_available_p, "No dynamic task data."
        )

    def test_get_work_request_dynamic_data(self) -> None:
        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        dynamic_task_data = self.assertHasCardYaml(
            tree, div_id="card-dynamic_data"
        )

        self.assertEqual(dynamic_task_data, self.work_request.dynamic_task_data)

    def test_get_work_request_incomplete(self) -> None:
        """Artifacts with incomplete files are marked as incomplete."""
        artifact, _ = self.playground.create_artifact(
            paths=["incomplete"],
            create_files=True,
            skip_add_files_in_store=True,
        )
        artifact.created_by_work_request = self.work_request
        artifact.save()

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertArtifacts(
            tree,
            "input-artifacts",
            Artifact.objects.filter(
                pk__in=self.work_request.get_task().get_input_artifacts_ids()
            ).order_by("id"),
        )
        self.assertArtifacts(tree, "output-artifacts", [artifact])

    def test_get_work_request_private_workspace_unauthenticated(self) -> None:
        """Work requests in private workspaces 403 to the unauthenticated."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.work_request.workspace = private_workspace
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"],
            "Workspace Private not found in scope debusine, or you are not "
            "authorized to see it",
        )

    def test_get_work_request_private_workspace_authenticated(self) -> None:
        """Work requests in private workspaces 200 to the authenticated."""
        private_workspace = self.playground.create_workspace(name="Private")
        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )
        self.work_request.workspace = private_workspace
        self.work_request.save()

        self.client.force_login(self.scenario.user)

        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response)

    def test_get_work_request_no_retry_if_logged_out(self) -> None:
        """No retry link if the user is logged out."""
        self.work_request.task_name = "noop"
        self.work_request.task_data = {}
        self.work_request.dynamic_task_data = {}
        self.work_request.status = WorkRequest.Statuses.ABORTED
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response)
        self.assertEqual(response.context["main_ui_shortcuts"], [])

    def test_get_work_request_no_retry_if_successful(self) -> None:
        """No retry link if the work request is successful."""
        self.work_request.task_name = "noop"
        self.work_request.task_data = {}
        self.work_request.dynamic_task_data = {}
        self.work_request.save()

        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user],
        )
        self.client.force_login(self.scenario.user)
        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response)
        self.assertEqual(response.context["main_ui_shortcuts"], [])

    def test_get_work_request_verify_retry(self) -> None:
        """Show the retry link if a work request can be retried."""
        self.work_request.task_name = "noop"
        self.work_request.task_data = {}
        self.work_request.configured_task_data = {}
        self.work_request.dynamic_task_data = {}
        self.work_request.status = WorkRequest.Statuses.ABORTED
        self.work_request.save()

        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user],
        )
        self.client.force_login(self.scenario.user)
        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_work_request_retry(self.work_request),
            ],
        )

    def test_get_work_request_no_abort_if_logged_out(self) -> None:
        """No abort link if the user is logged out."""
        self.work_request.status = WorkRequest.Statuses.PENDING
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response)
        self.assertEqual(response.context["main_ui_shortcuts"], [])

    def test_get_work_request_no_abort_if_completed(self) -> None:
        """No abort link if the work request is completed."""
        self.assertEqual(
            self.work_request.status, WorkRequest.Statuses.COMPLETED
        )
        self.client.force_login(self.playground.get_default_user())
        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response)
        self.assertEqual(response.context["main_ui_shortcuts"], [])

    def test_get_work_request_verify_abort(self) -> None:
        """Show the abort link if a work request can be aborted."""
        self.work_request.status = WorkRequest.Statuses.PENDING
        self.work_request.save()

        self.client.force_login(self.playground.get_default_user())
        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_work_request_abort(self.work_request),
            ],
        )

    def test_get_work_request_superseded(self) -> None:
        """Check that superseding/superseded links show up."""
        with context.disable_permission_checks():
            self.work_request.status = WorkRequest.Statuses.ABORTED
            self.work_request.result = WorkRequest.Results.NONE
            self.work_request.save()
            wr_new = self.work_request.retry()

        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response)

        sidebar = response.context["sidebar_items"]
        self.assertEqual(sidebar[1].value, str(wr_new))
        self.assertEqual(sidebar[1].label, "Superseded by")
        self.assertEqual(sidebar[1].url, wr_new.get_absolute_url())

        response = self.client.get(wr_new.get_absolute_url())
        self.assertResponseHTML(response)
        sidebar = response.context["sidebar_items"]
        self.assertEqual(sidebar[1].value, str(self.work_request))
        self.assertEqual(sidebar[1].label, "Supersedes")
        self.assertEqual(sidebar[1].url, self.work_request.get_absolute_url())

    def test_get_work_request_expire_at(self) -> None:
        self.work_request.expiration_delay = timedelta(days=2)
        self.work_request.save()
        response = self.client.get(self.work_request.get_absolute_url())

        sidebar = response.context["sidebar_items"]

        assert self.work_request.expire_at
        self.assertEqual(
            sidebar[9].value,
            timesince(self.work_request.expire_at, reversed=True),
        )
        self.assertEqual(sidebar[9].label, "Expiration date")

    def test_get_work_request_running(self) -> None:
        """Check that elapsed execution time displays."""
        self.work_request.status = WorkRequest.Statuses.RUNNING
        self.work_request.result = WorkRequest.Results.NONE
        self.work_request.started_at = timezone.now() - timedelta(minutes=10)
        self.work_request.completed_at = None
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        self.assertResponseHTML(response)

        sidebar = response.context["sidebar_items"]
        self.assertEqual(
            [item.content for item in sidebar if item.label == "Duration"],
            ["10\xa0minutes"],
        )

    def test_get_work_request_no_output_data_errors(self) -> None:
        """If the work request has no output data errors, they are not shown."""
        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath("//ul[@id='errors']"))
        self.assertNotContains(response, "<h2>Errors</h2>")

        output_no_output_data = self.assertHasElement(
            tree, "//p[@id='internals-no-output-data']"
        )
        self.assertTextContentEqual(output_no_output_data, "No output data.")

        internals_no_output_data = self.assertHasElement(
            tree, "//p[@id='internals-no-output-data']"
        )
        self.assertTextContentEqual(internals_no_output_data, "No output data.")

    def test_get_work_request_output_data_errors(self) -> None:
        """If the work request has output data errors, they are shown."""
        self.work_request.result = WorkRequest.Results.ERROR
        self.work_request.output_data = OutputData(
            errors=[
                OutputDataError(message="One error", code=""),
                OutputDataError(message="Another error", code=""),
            ]
        )
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)
        errors = tree.xpath("//ul[@id='errors']")[0]
        self.assertEqual(len(errors.li), 2)
        self.assertTextContentEqual(errors.li[0], "One error")
        self.assertTextContentEqual(errors.li[1], "Another error")

    def make_work_request_lintian(self, work_request: WorkRequest) -> None:
        """Change work_request to "lintian", create source artifact."""
        work_request.task_name = "lintian"
        source_artifact = self.playground.create_source_artifact()
        work_request.task_data = {
            "input": {
                "source_artifact": source_artifact.id,
                "binary_artifacts": [],
            }
        }
        work_request.dynamic_task_data = None
        work_request.save()
        work_request.artifact_set.add(self.create_lintian_source_artifact())

    def create_lintian_source_artifact(self) -> Artifact:
        """
        Create a Lintian source artifact result.

        Contains lintian.txt file.
        """
        artifact, _ = self.playground.create_artifact(
            paths=[Lintian.CAPTURE_OUTPUT_FILENAME],
            create_files=True,
            category=LintianArtifact._category,
            data={
                "architecture": "source",
                "summary": {
                    "package_filename": {"hello": "hello.dsc"},
                    "tags_count_by_severity": {},
                    "tags_found": [],
                    "overridden_tags_found": [],
                    "lintian_version": "2.117.0",
                    "distribution": "sid",
                },
            },
        )
        artifact.created_by_work_request = self.work_request
        artifact.save()
        return artifact

    def test_multi_line_string(self) -> None:
        """Multi-line strings are rendered using the literal style."""
        self.work_request.task_name = "mmdebstrap"
        self.work_request.task_data = {
            "bootstrap_options": {"architecture": "amd64"},
            "bootstrap_repositories": [
                {"mirror": "https://deb.debian.org/debian", "suite": "bookworm"}
            ],
            "customization_script": "multi-line\nstring\n",
        }
        self.work_request.dynamic_task_data = {}
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)
        card = tree.xpath("//div[@id='card-task_data']")[0]

        self.assertTextContentEqual(
            card.div[0],
            """\
            bootstrap_options:
              architecture: amd64
            bootstrap_repositories:
            - mirror: https://deb.debian.org/debian
              suite: bookworm
            customization_script: |
              multi-line
              string
            """,
        )

    def test_workflow_root(self) -> None:
        """A workflow root shows information on its descendants."""
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=self.scenario.workspace,
            task_name="noop",
            task_data={},
        )
        root = self.playground.create_workflow(template, task_data={})
        child_template = WorkflowTemplate.objects.create(
            name="child",
            workspace=self.scenario.workspace,
            task_name="noop",
            task_data={},
        )
        child = self.playground.create_workflow(
            child_template, task_data={}, parent=root
        )
        WorkRequest.objects.create_workflow_callback(
            parent=root, step="invisible", visible=False
        )
        grandchildren = []
        for i in range(2):
            wr = self.playground.create_work_request(
                parent=child,
                workflow_data=WorkRequestWorkflowData(
                    display_name=f"Lintian {i + 1}",
                    step=f"lintian{i + 1}",
                ),
            )
            wr.add_dependency(child)
            self.make_work_request_lintian(wr)
            grandchildren.append(wr)

        # There's still room for improvement, but the important thing is
        # that this doesn't make a query for each child work request.
        with self.assertNumQueries(24):
            response = self.client.get(root.get_absolute_url())
        self.assertResponseHTML(response)

        def wr_url(wr: WorkRequest) -> str:
            return wr.get_absolute_url()

        root_link = f'<a href="{wr_url(root)}">noop</a>'
        child_link = f'<a href="{wr_url(child)}">noop</a>'
        grandchild_links: list[str] = []
        for grandchild in grandchildren:
            assert grandchild.workflow_data is not None
            grandchild_links.append(
                f'<a href="{wr_url(grandchild)}">'
                f'{grandchild.workflow_data.display_name}</a>'
            )
        pending = '<span class="badge text-bg-secondary">Pending</span>'
        blocked = '<span class="badge text-bg-dark">Blocked</span>'
        self.assertContains(
            response,
            textwrap.dedent(
                f"""
                <h2>Workflow information</h2>
                <ul>
                    <li>
                        <details open>
                            <summary>{root_link} ({pending})</summary>
                            <ul>
                                <li>
                                    <details open>
                                        <summary>
                                            {child_link} ({pending})
                                        </summary>
                                        <ul>
                                            <li>
                                                {grandchild_links[0]}
                                                ({blocked})
                                            </li>
                                            <li>
                                                {grandchild_links[1]}
                                                ({blocked})
                                            </li>
                                        </ul>
                                    </details>
                                </li>
                            </ul>
                        </details>
                    </li>
                </ul>
                """
            ),
            html=True,
        )

    def test_workflow_child(self) -> None:
        """A workflow child shows information on its root/parent/descendants."""
        template = WorkflowTemplate.objects.create(
            name="test",
            workspace=self.scenario.workspace,
            task_name="noop",
            task_data={},
        )
        root = self.playground.create_workflow(template, task_data={})
        child_template = WorkflowTemplate.objects.create(
            name="child",
            workspace=self.scenario.workspace,
            task_name="noop",
            task_data={},
        )
        child = self.playground.create_workflow(
            child_template, task_data={}, parent=root
        )
        grandchildren = []
        for i in range(2):
            wr = self.playground.create_work_request(
                parent=child,
                workflow_data=WorkRequestWorkflowData(
                    display_name=f"Lintian {i + 1}",
                    step=f"lintian{i + 1}",
                ),
            )
            self.make_work_request_lintian(wr)
            grandchildren.append(wr)
        grandchildren[0].mark_completed(WorkRequest.Results.SUCCESS)
        grandchildren[1].add_dependency(child)

        # There's still room for improvement, but the important thing is
        # that this doesn't make a query for each child work request.
        with self.assertNumQueries(29):
            response = self.client.get(child.get_absolute_url())
        self.assertResponseHTML(response)

        def wr_url(wr: WorkRequest) -> str:
            return wr.get_absolute_url()

        root_link = f'<a href="{wr_url(root)}">noop</a>'
        child_link = f'<a href="{wr_url(child)}">noop</a>'
        grandchild_links: list[str] = []
        for grandchild in grandchildren:
            assert grandchild.workflow_data is not None
            grandchild_links.append(
                f'<a href="{wr_url(grandchild)}">'
                f'{grandchild.workflow_data.display_name}</a>'
            )
        pending = '<span class="badge text-bg-secondary">Pending</span>'
        blocked = '<span class="badge text-bg-dark">Blocked</span>'
        c_success = (
            '<span class="badge text-bg-primary">Completed</span>'
            '<span class="badge text-bg-success">Success</span>'
        )
        self.assertContains(
            response,
            textwrap.dedent(
                f"""
                <h2>Workflow information</h2>
                <ul>
                    <li class="workflow-ancestor">
                        <span title="Root workflow">⬆️</span>
                        {root_link} ({pending})
                    </li>
                    <li>
                        <details open>
                            <summary>{child_link} ({pending})</summary>
                            <ul>
                                <li>{grandchild_links[0]} ({c_success})</li>
                                <li>{grandchild_links[1]} ({blocked})</li>
                            </ul>
                        </details>
                    </li>
                </ul>
                """
            ),
            html=True,
        )

    def test_workflow_manual_unblock_log(self) -> None:
        """A work request's manual unblock log is rendered if present."""
        user = self.playground.get_default_user()
        self.playground.add_user_permission(
            user, WorkRequest, "change_workrequest"
        )
        template = self.playground.create_workflow_template("test", "noop")
        root = self.playground.create_workflow(
            template, task_data={}, status=WorkRequest.Statuses.RUNNING
        )
        work_request = WorkRequest.objects.create_synchronization_point(
            parent=root, step="test", status=WorkRequest.Statuses.BLOCKED
        )
        work_request.unblock_strategy = WorkRequest.UnblockStrategy.MANUAL
        workflow_data = work_request.workflow_data
        assert workflow_data is not None
        workflow_data.manual_unblock = WorkRequestManualUnblockData(
            log=[
                WorkRequestManualUnblockLog(
                    user_id=self.playground.get_default_user().id,
                    timestamp=timezone.now(),
                    notes="LGTM",
                    action=WorkRequestManualUnblockAction.ACCEPT,
                )
            ]
        )
        work_request.workflow_data = workflow_data
        work_request.save()

        self.client.force_login(user)
        response = self.client.get(work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)
        tbody = tree.xpath("//table[@id='review-log']")[0].tbody[0]
        self.assertEqual(len(tbody.tr), 1)
        self.assertEqual(tbody.tr.td[1], self.scenario.user.username)
        self.assertHTMLEqual(
            html.tostring(tbody.tr.td[2], encoding="unicode"),
            '<td>'
            '<span class="bi bi-check2 text-success" title="Accept"></span>'
            '</td>',
        )
        self.assertTextContentEqual(tbody.tr.td[3], "LGTM")

        self.assertTextContentEqual(
            tree.xpath("//p[@id='generic-description']")[0], "-"
        )

        form = tree.xpath("//form[@id='manual-unblock-form']")[0]
        self.assertEqual(
            form.get("action"), work_request.get_absolute_url_unblock()
        )
        submit_buttons = [
            tag for tag in form.input if tag.get("type") == "submit"
        ]
        self.assertEqual(
            ["Accept", "Reject", "Record notes only"],
            [tag.get("value") for tag in submit_buttons],
        )

    def test_external_debsign_requires_signature(self) -> None:
        """A running `ExternalDebsign` work request shows a prompt."""
        user = self.playground.get_default_user()
        work_request = self.playground.create_work_request(
            task_type=TaskTypes.WAIT,
            task_name="externaldebsign",
            created_by=user,
            status=WorkRequest.Statuses.RUNNING,
            workflow_data=WorkRequestWorkflowData(needs_input=True),
        )

        self.client.force_login(user)
        response = self.client.get(work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)
        requires_signature = tree.xpath("//div[@id='requires-signature']")[0]
        self.assertTextContentEqual(
            requires_signature.div[0], "Waiting for signature"
        )
        csrf_token = requires_signature.div[1].xpath(
            '//input[@name="csrfmiddlewaretoken"]/@value'
        )[0]
        self.assertHTMLContentsEquivalent(
            requires_signature.div[1],
            f'<div class="card-body">'
            f'Run <code>debusine --server '
            f'{settings.DEBUSINE_FQDN}/{work_request.workspace.scope.name} '
            f'provide-signature {work_request.id}</code> '
            f'to sign this request. '
            f'<form method="post"'
            f' action="{work_request.get_absolute_url_abort()}">'
            f'<input type="hidden" name="csrfmiddlewaretoken"'
            f' value="{csrf_token}" />'
            f'If you do not want to sign it, you can '
            f'<button class="btn btn-link in-running-text" type="submit">'
            f'abort</button> it instead.'
            f'</form>'
            f'</div>',
        )

    def test_external_debsign_wrong_user(self) -> None:
        """A running `ExternalDebsign` only shows a prompt to its creator."""
        work_request = self.playground.create_work_request(
            task_type=TaskTypes.WAIT,
            task_name="externaldebsign",
            status=WorkRequest.Statuses.RUNNING,
            workflow_data=WorkRequestWorkflowData(needs_input=True),
        )
        other_user = get_user_model().objects.create_user(
            username="another", email="another@example.org"
        )

        self.client.force_login(other_user)
        response = self.client.get(work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertEqual(tree.xpath("//div[@id='requires-signature']"), [])

    def test_internals_event_reactions_json(self) -> None:
        """Assert event_reactions_json card in "Internals" tab."""
        self.work_request.event_reactions_json = {"a": "b"}
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        event_reactions_json = self.assertHasCardJson(
            tree, div_id="card-event_reactions"
        )

        self.assertEqual(
            event_reactions_json,
            self.work_request.event_reactions_json,
        )

    def test_internals_output_data(self) -> None:
        """Assert output_data_field" in "Internals" tab with data."""
        self.work_request.output_data = OutputData(
            runtime_statistics=RuntimeStatistics(duration=10)
        )
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        output_data = self.assertHasCardJson(tree, div_id="card-output_data")

        self.assertEqual(output_data["runtime_statistics"]["duration"], 10)

    def test_internals_workflow_data(self) -> None:
        """Assert "workflow_data" in "Internals" tab with data."""
        workflow = self.playground.create_workflow()
        workflow.workflow_data = WorkRequestWorkflowData(
            step="test", display_name="test"
        )
        workflow.save()

        response = self.client.get(workflow.get_absolute_url())
        tree = self.assertResponseHTML(response)

        workflow_json = self.assertHasCardJson(
            tree, div_id="card-workflow_data"
        )

        self.assertEqual(
            workflow_json,
            workflow.workflow_data_json,
        )

    def test_internals_collection_name_root_workflow(self) -> None:
        collection = self.playground.create_collection(
            name="Test", category=CollectionCategory.TEST
        )
        root_workflow = self.playground.create_workflow()
        root_workflow.internal_collection = collection
        root_workflow.save()

        workflow = self.playground.create_workflow(parent=root_workflow)

        self.work_request.internal_collection = (
            self.playground.create_collection(
                name="Collection", category=CollectionCategory.TEST
            )
        )
        self.work_request.parent = workflow
        self.work_request.save()

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        collection_p = self.assertHasElement(
            tree, f"//p[@id='{self.INTERNAL_COLLECTION_ID}']"
        )
        self.assertTextContentEqual(collection_p, "Internal collection: Test")
        self.assertEqual(
            collection_p.a.attrib["href"], collection.get_absolute_url()
        )

    def test_internals_collection_no_internal_collection(self) -> None:
        # Work requests that are not part of a workflow do not have an
        # internal collection. We are skipping the section
        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        self.assertFalse(
            tree.xpath(f"//p[@id='{self.INTERNAL_COLLECTION_ID}']")
        )

    def test_format_output_data_empty(self) -> None:
        output_data = OutputData()

        self.assertEqual(
            WorkRequestDetailView._format_output_data(output_data),
            {"Runtime statistics": {}, "Errors": []},
        )

    def test_format_output_data_none(self) -> None:
        self.assertEqual(WorkRequestDetailView._format_output_data(None), {})

    def test_format_output_data_to_dict(self) -> None:
        output_data = OutputData(
            runtime_statistics=RuntimeStatistics(
                disk_space=11 * 1024 * 1024, duration=7400, cpu_time=2000
            ),
            errors=[OutputDataError(message="File not found", code="ENOENT")],
        )

        self.assertEqual(
            WorkRequestDetailView._format_output_data(output_data),
            {
                "Errors": [{"code": "ENOENT", "message": "File not found"}],
                "Runtime statistics": {
                    "Available disk space": None,
                    "Available memory": None,
                    "CPU count": None,
                    "CPU time": "00:33:20",
                    "Disk space": "11.0\xa0MB",
                    "Duration": "02:03:20",
                    "Memory": None,
                },
            },
        )

    def test_format_output_data_with_skip_reason(self) -> None:
        output_data = OutputData(skip_reason="Boring")

        self.assertEqual(
            WorkRequestDetailView._format_output_data(output_data),
            {"Runtime statistics": {}, "Errors": [], "Skipped": "Boring"},
        )

    def test_dependencies_empty(self) -> None:
        """Test when there are no dependencies."""
        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertHasElement(
            tree,
            "//p[normalize-space(text()) = 'This work request does "
            "not depend on any other one.']",
        )
        self.assertHasElement(
            tree,
            "//p[normalize-space(text()) = 'This work request is "
            "not required by any other one.']",
        )

    def test_dependencies_tab_dependencies(self) -> None:
        dependency = self.playground.create_work_request()
        self.work_request.dependencies.add(dependency)

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        table = self.assertHasElement(
            tree, "//table[@id='dependencies-work_request-list']"
        )
        self.assertWorkRequestRow(table.tbody.tr[0], dependency)

    def test_dependencies_tab_reverse_dependencies(self) -> None:
        dependent = self.playground.create_work_request()
        dependent.dependencies.add(self.work_request)

        response = self.client.get(self.work_request.get_absolute_url())
        tree = self.assertResponseHTML(response)

        table = self.assertHasElement(
            tree, "//table[@id='reverse_dependencies-work_request-list']"
        )
        self.assertWorkRequestRow(table.tbody.tr[0], dependent)


class WorkRequestListViewTests(ViewTestMixin, TestCase):
    """Tests for WorkRequestListView class."""

    scenario = scenarios.DefaultContext()

    def assertListsWorkRequests(
        self, tree: etree._Element, work_requests: list[WorkRequest]
    ) -> None:
        """Ensure the list view shows the given work requests only."""
        table = tree.xpath("//table[@id='work_request-list']")[0]
        for idx, work_request in enumerate(work_requests):
            self.assertWorkRequestRow(table.tbody.tr[idx], work_request)
        self.assertEqual(len(table.tbody.tr), len(work_requests))

    def assertNoWorkRequests(self, tree: etree._Element) -> None:
        """Ensure the list view shows no work requests."""
        self.assertEqual(len(tree.xpath("//table[@id='work_request-list']")), 0)
        p = tree.xpath("//p[@id='work_request-list-empty']")[0]
        self.assertTextContentEqual(p, "No work requests.")

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        self.assertSetsCurrentWorkspace(
            self.scenario.workspace,
            reverse(
                "workspaces:work-requests:list",
                kwargs={"wname": self.scenario.workspace.name},
            ),
        )

    def test_get_no_work_request(self) -> None:
        """No work requests in the server: 'No work requests' in response."""
        response = self.client.get(
            reverse(
                "workspaces:work-requests:list",
                kwargs={"wname": self.scenario.workspace.name},
            )
        )
        tree = self.assertResponseHTML(response)
        self.assertNoWorkRequests(tree)

    def test_get_work_requests_workspace_filter(self) -> None:
        """Two work requests in different workspaces."""
        private_workspace = self.playground.create_workspace(name="Private")
        public_work_request = self.playground.create_work_request(
            task_name="noop",
            result=WorkRequest.Results.SUCCESS,
        )
        private_work_request = self.playground.create_work_request(
            task_name="noop", workspace=private_workspace
        )

        self.playground.create_group_role(
            private_workspace, Workspace.Roles.OWNER, users=[self.scenario.user]
        )

        self.client.force_login(self.scenario.user)
        response = self.client.get(
            reverse(
                "workspaces:work-requests:list",
                kwargs={"wname": self.scenario.workspace.name},
            )
        )
        tree = self.assertResponseHTML(response)
        self.assertListsWorkRequests(tree, [public_work_request])

        response = self.client.get(
            reverse(
                "workspaces:work-requests:list",
                kwargs={"wname": private_workspace.name},
            )
        )
        tree = self.assertResponseHTML(response)
        self.assertListsWorkRequests(tree, [private_work_request])

    def test_get_work_requests_exclude_internal(self) -> None:
        """The list excludes INTERNAL work requests."""
        template = WorkflowTemplate.objects.create(
            name="test",
            task_name="noop",
            task_data={},
            workspace=self.scenario.workspace,
        )
        root = self.playground.create_workflow(template, task_data={})
        WorkRequest.objects.create_synchronization_point(
            parent=root, step="test"
        )

        response = self.client.get(
            reverse(
                "workspaces:work-requests:list",
                kwargs={"wname": self.scenario.workspace.name},
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tree = self.assertResponseHTML(response)
        assert isinstance(response, SimpleTemplateResponse)
        assert response.context_data is not None
        self.assertQuerySetEqual(response.context_data["object_list"], [root])

        table = tree.xpath("//table[@id='work_request-list']")[0]
        self.assertWorkRequestRow(table.tbody.tr[0], root)
        self.assertEqual(len(table.tbody.tr), 1)

    def test_pagination(self) -> None:
        """Pagination is set up and rendered by the template."""
        self.playground.create_work_request(task_name="noop")

        response = self.client.get(
            reverse(
                "workspaces:work-requests:list",
                kwargs={"wname": self.scenario.workspace.name},
            )
        )
        tree = self.assertResponseHTML(response)
        self.assertHasElement(tree, "//table[@id='work_request-list']")
        pagination = response.context["paginator"]
        self.assertEqual(pagination.per_page, 50)

    def test_filter_by_status(self) -> None:
        """Test get_queryset() with status=XX returns expected WorkRequests."""
        work_requests = {
            "pending": self.playground.create_work_request(
                status=WorkRequest.Statuses.PENDING,
                task_name="noop",
            ),
            "running": self.playground.create_work_request(
                status=WorkRequest.Statuses.RUNNING,
                task_name="noop",
            ),
            "completed": self.playground.create_work_request(
                status=WorkRequest.Statuses.COMPLETED,
                task_name="noop",
            ),
            "aborted": self.playground.create_work_request(
                status=WorkRequest.Statuses.ABORTED,
                task_name="noop",
            ),
            "blocked": self.playground.create_work_request(
                status=WorkRequest.Statuses.BLOCKED,
                task_name="noop",
            ),
        }

        for work_request_status, work_request in work_requests.items():
            with self.subTest(work_request_status):
                response = self.client.get(
                    reverse(
                        "workspaces:work-requests:list",
                        kwargs={"wname": work_request.workspace.name},
                    )
                    + f"?status={work_request_status}"
                )
                assert isinstance(response, SimpleTemplateResponse)
                assert response.context_data is not None
                self.assertQuerySetEqual(
                    response.context_data["work_request_list"], [work_request]
                )

    def test_filter_by_architecture_invalid_status(self) -> None:
        """
        Test get_queryset() with invalid status returns all work requests.

        It also adds a Message to the page.
        """
        work_request = self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING,
            task_name="noop",
        )

        response = self.client.get(
            reverse(
                "workspaces:work-requests:list",
                kwargs={"wname": self.scenario.workspace.name},
            )
            + "?status=invalid"
        )

        self.assertEqual(
            list(response.context["messages"]),
            [
                Message(
                    messages_constants.WARNING,
                    'Invalid "status" parameter, ignoring it',
                    extra_tags="",
                )
            ],
        )

        assert isinstance(response, SimpleTemplateResponse)
        assert response.context_data is not None
        self.assertQuerySetEqual(
            response.context_data["paginator"].page_obj.object_list,
            [work_request],
        )

    def test_get_queryset_filter_by_architecture_wrong_status(self) -> None:
        """Test get_queryset() filters by architecture only for pending."""
        work_request = self.playground.create_work_request(
            status=WorkRequest.Statuses.RUNNING,
            task_name="noop",
        )

        response = self.client.get(
            reverse(
                "workspaces:work-requests:list",
                kwargs={"wname": self.scenario.workspace.name},
            )
            + "?status=running&arch=amd64"
        )

        self.assertEqual(
            list(response.context["messages"]),
            [
                Message(
                    messages_constants.WARNING,
                    'Filter by architecture is only supported when '
                    'also filtering by "status=pending", ignoring architecture'
                    'filtering',
                    extra_tags="",
                )
            ],
        )

        assert isinstance(response, SimpleTemplateResponse)
        assert response.context_data is not None
        self.assertQuerySetEqual(
            response.context_data["work_request_list"], [work_request]
        )

    def test_filter_by_architecture(self) -> None:
        """Test filtering by architecture."""
        # Create two amd64 work requests
        work_request_amd64_1 = self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING,
            task_name="noop",
            task_data={"host_architecture": "amd64"},
        )
        bootstrap_repository = SystemBootstrapRepository(
            mirror="https://deb.debian.org/debian", suite="stable"
        )
        work_request_amd64_2 = self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING,
            task_name="mmdebstrap",
            task_data=MmDebstrapData(
                bootstrap_options=MmDebstrapBootstrapOptions(
                    architecture="amd64"
                ),
                bootstrap_repositories=[bootstrap_repository],
            ),
        )
        # Assert architecture is as expected
        self.assertEqual(
            work_request_amd64_2.get_task().host_architecture(), "amd64"
        )

        # Create two i386 work requests
        self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING,
            task_name="noop",
            task_data={"host_architecture": "i386"},
        )

        self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING,
            task_name="mmdebstrap",
            task_data=MmDebstrapData(
                bootstrap_options=MmDebstrapBootstrapOptions(
                    architecture="i386"
                ),
                bootstrap_repositories=[bootstrap_repository],
            ),
        )

        # Create a work request for which "WorkRequest.get_task()" raise
        # TaskConfigError.
        self.playground.create_work_request(
            status=WorkRequest.Statuses.PENDING,
            task_name="sbuild",
        )

        response = self.client.get(
            reverse(
                "workspaces:work-requests:list",
                kwargs={"wname": self.scenario.workspace.name},
            )
            + "?status=pending&arch=amd64"
        )

        assert isinstance(response, SimpleTemplateResponse)
        assert response.context_data is not None
        self.assertQuerySetEqual(
            response.context_data["paginator"].page_obj.object_list,
            [work_request_amd64_2, work_request_amd64_1],
        )


class WorkRequestCreateViewTests(ViewTestMixin, TestCase):
    """Tests for WorkRequestCreateView."""

    scenario = scenarios.DefaultContext()

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        url = reverse(
            "workspaces:work_requests:create",
            kwargs={"wname": self.scenario.workspace.name},
        )
        self.assertSetsCurrentWorkspace(self.scenario.workspace, url)
        self.assertSetsCurrentWorkspace(
            self.scenario.workspace, url, method="post"
        )

        self.assertEnforcesPermission(
            self.scenario.workspace.can_create_work_requests,
            url,
            "get_context_data",
            method="get",
        )

        self.assertEnforcesPermission(
            self.scenario.workspace.can_create_work_requests,
            url,
            "form_valid",
            method="post",
            data={
                "workspace": self.scenario.workspace.id,
                "task_name": "noop",
                "task_data": "",
            },
        )

    def test_create_work_request(self) -> None:
        """Post to "work_requests:create" to create a work request."""
        self.playground.create_debian_environment(codename="bookworm")
        source_artifact = self.playground.create_source_artifact()
        self.client.force_login(self.scenario.user)
        workspace = self.scenario.workspace
        name = "sbuild"

        task_data_yaml = textwrap.dedent(
            f"""
        build_components:
        - any
        - all
        environment: debian/match:codename=bookworm
        host_architecture: amd64
        input:
          source_artifact: {source_artifact.id}
        """
        )

        self.assertEqual(WorkRequest.objects.count(), 0)

        with override_permission(
            Workspace, "can_create_work_requests", AllowAll
        ):
            response = self.client.post(
                reverse(
                    "workspaces:work_requests:create",
                    kwargs={"wname": workspace.name},
                ),
                {
                    "workspace": workspace.id,
                    "task_name": name,
                    "task_data": task_data_yaml,
                },
            )
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)

        # The work request got created
        work_request = WorkRequest.objects.latest("id")
        self.assertIsNotNone(work_request.id)
        # and has the user assigned
        self.assertEqual(work_request.created_by, self.scenario.user)

        # the browser got redirected to the work_requests:detail
        self.assertRedirects(response, work_request.get_absolute_url())

    def test_create_work_request_invalid_task_data(self) -> None:
        """Test form invalid due to some_method raising a ValidationError."""
        source_artifact, _ = self.playground.create_artifact()

        self.client.force_login(self.scenario.user)
        workspace = self.scenario.workspace
        name = "sbuild"

        task_data_yaml = textwrap.dedent(
            """
            build_components:
            - any
            - all
            host_architecture: amd64
            input:
                source_artifact: 536
            environment: does-not-exist/match:codename=trixie:variant=sbuild
            """
        )

        self.assertEqual(WorkRequest.objects.count(), 0)

        with override_permission(
            Workspace, "can_create_work_requests", AllowAll
        ):
            response = self.client.post(
                reverse(
                    "workspaces:work_requests:create",
                    kwargs={"wname": workspace.name},
                ),
                {
                    "workspace": workspace.id,
                    "task_name": name,
                    "task_data": task_data_yaml,
                },
            )

        # The work request is not created
        self.assertEqual(WorkRequest.objects.count(), 0)

        # The form displays the error on the task_data field
        self.assertContains(response, "Invalid task data")

        # Get the error message
        try:
            work_request = WorkRequest.objects.create(
                task_name=name,
                workspace=workspace,
                task_data=yaml.safe_load(task_data_yaml),
                created_by=self.scenario.user,
            )
            work_request.get_task().compute_dynamic_data(
                TaskDatabase(work_request)
            )
        except (KeyError, ValueError) as exc:
            error_message = f"Invalid task data: {exc}"

        self.assertFormError(
            response.context["form"], "task_data", error_message
        )


class WorkRequestRetryViewTests(ViewTestMixin, TestCase):
    """Tests for WorkRequestRetryView."""

    scenario = scenarios.DefaultContext()

    work_request: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.work_request = cls.playground.create_work_request(
            task_name="noop",
            task_data=NoopData(),
            status=WorkRequest.Statuses.ABORTED,
        )

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        self.assertSetsCurrentWorkspace(
            self.scenario.workspace,
            self.work_request.get_absolute_url_retry(),
            method="post",
        )

        self.assertEnforcesPermission(
            self.work_request.can_retry,
            self.work_request.get_absolute_url_retry(),
            "retry",
            method="post",
        )

    def test_no_get(self) -> None:
        """Test that GET requests are rejected."""
        response = self.client.get(self.work_request.get_absolute_url_retry())
        self.assertEqual(
            response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
        )

    def test_retry_invalid(self) -> None:
        """Try retrying a work request that cannot be retried."""
        self.work_request.status = WorkRequest.Statuses.COMPLETED
        self.work_request.result = WorkRequest.Results.SUCCESS
        self.work_request.save()

        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_retry", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_retry()
            )
        self.assertRedirects(response, self.work_request.get_absolute_url())
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            messages[0].message,
            "Cannot retry: Only aborted or failed tasks can be retried",
        )

    def test_retry_does_not_exist(self) -> None:
        """Try retrying a nonexistent work request."""
        url = self.work_request.get_absolute_url_retry()
        self.work_request.delete()

        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_retry", AllowAll):
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"],
            "No work request found matching the query",
        )

    def test_retry(self) -> None:
        """Try retrying a work request that can be retried."""
        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_retry", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_retry()
            )

        self.work_request.refresh_from_db()
        self.assertTrue(getattr(self.work_request, "superseded"))
        new_wr = self.work_request.superseded

        self.assertRedirects(response, new_wr.get_absolute_url())


class WorkRequestAbortViewTests(ViewTestMixin, TestCase):
    """Tests for WorkRequestAbortView."""

    scenario = scenarios.DefaultContext()

    work_request: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.work_request = cls.playground.create_work_request(
            task_name="noop", task_data=NoopData()
        )

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        self.assertSetsCurrentWorkspace(
            self.scenario.workspace,
            self.work_request.get_absolute_url_abort(),
            method="post",
        )

        self.assertEnforcesPermission(
            self.work_request.can_abort,
            self.work_request.get_absolute_url_abort(),
            "abort",
            method="post",
        )

    def test_no_get(self) -> None:
        """Test that GET requests are rejected."""
        response = self.client.get(self.work_request.get_absolute_url_abort())
        self.assertEqual(
            response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
        )

    def test_abort_invalid(self) -> None:
        """Try aborting a work request that cannot be aborted."""
        self.work_request.status = WorkRequest.Statuses.ABORTED
        self.work_request.save()

        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_abort", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_abort()
            )
        self.assertRedirects(response, self.work_request.get_absolute_url())
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            messages[0].message,
            "Cannot abort: Only pending, blocked, or running tasks can be "
            "aborted",
        )

    def test_abort_does_not_exist(self) -> None:
        """Try aborting a nonexistent work request."""
        url = self.work_request.get_absolute_url_abort()
        self.work_request.delete()

        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_abort", AllowAll):
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"],
            "No work request found matching the query",
        )

    def test_abort(self) -> None:
        """Try aborting a work request that can be aborted."""
        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_abort", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_abort()
            )

        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.aborted_by, self.scenario.user)
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.ABORTED)
        self.assertRedirects(response, self.work_request.get_absolute_url())


class WorkRequestUnblockViewTests(ViewTestMixin, TestCase):
    """Tests for WorkRequestUnblockView."""

    scenario = scenarios.DefaultContext()
    workflow: ClassVar[WorkRequest]
    work_request: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        template = cls.playground.create_workflow_template("test", "noop")
        cls.workflow = cls.playground.create_workflow(
            template,
            task_data={},
            status=WorkRequest.Statuses.RUNNING,
        )
        cls.work_request = WorkRequest.objects.create_synchronization_point(
            parent=cls.workflow,
            step="test",
            status=WorkRequest.Statuses.BLOCKED,
        )
        cls.work_request.unblock_strategy = WorkRequest.UnblockStrategy.MANUAL
        cls.work_request.save()

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        self.assertSetsCurrentWorkspace(
            self.scenario.workspace,
            self.work_request.get_absolute_url_unblock(),
            method="post",
        )

        self.assertEnforcesPermission(
            self.work_request.can_unblock,
            self.work_request.get_absolute_url_unblock(),
            "unblock",
            method="post",
        )

    def test_no_get(self) -> None:
        """Test that GET requests are rejected."""
        response = self.client.get(self.work_request.get_absolute_url_unblock())
        self.assertEqual(
            response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
        )

    def test_does_not_exist(self) -> None:
        """Try unblocking a nonexistent work request."""
        url = self.work_request.get_absolute_url_unblock()
        self.work_request.delete()
        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_unblock", AllowAll):
            response = self.client.post(
                url,
                {"action": "Accept"},
            )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"],
            "No work request found matching the query",
        )

    def test_invalid_form(self) -> None:
        """The view returns 400 if the form is invalid."""
        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_unblock", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_unblock(),
                {"notes": "\0"},
            )

        self.assertContains(
            response,
            str(ProhibitNullCharactersValidator.message),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    def test_invalid_action(self) -> None:
        """The view returns 400 if the action parameter is invalid."""
        self.client.force_login(self.scenario.user)

        with override_permission(WorkRequest, "can_unblock", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_unblock(),
                {"action": "Nonsense"},
            )

        self.assertContains(
            response,
            "Invalid action parameter: 'Nonsense'",
            status_code=status.HTTP_400_BAD_REQUEST,
            html=True,
        )

    def test_cannot_unblock(self) -> None:
        """The view returns 400 if the work request cannot be unblocked."""
        self.work_request.unblock_strategy = WorkRequest.UnblockStrategy.DEPS
        self.work_request.save()

        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_unblock", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_unblock(),
                {"action": "Accept"},
            )

        self.assertContains(
            response,
            f"Cannot unblock: Work request {self.work_request.pk} cannot be "
            f"manually unblocked",
            status_code=status.HTTP_400_BAD_REQUEST,
            html=True,
        )

    def test_accept(self) -> None:
        """Accept a blocked work request."""
        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_unblock", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_unblock(),
                {"action": "Accept"},
            )

        self.assertRedirects(response, self.work_request.get_absolute_url())
        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.PENDING)
        assert self.work_request.workflow_data is not None
        manual_unblock = self.work_request.workflow_data.manual_unblock
        assert manual_unblock is not None
        self.assertEqual(len(manual_unblock.log), 1)
        self.assertEqual(manual_unblock.log[0].user_id, self.scenario.user.id)
        self.assertLess(manual_unblock.log[0].timestamp, timezone.now())
        self.assertIsNone(manual_unblock.log[0].notes)
        self.assertEqual(
            manual_unblock.log[0].action, WorkRequestManualUnblockAction.ACCEPT
        )

    def test_reject(self) -> None:
        """Reject a blocked work request."""
        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_unblock", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_unblock(),
                {"notes": "Go away", "action": "Reject"},
            )

        self.assertRedirects(response, self.work_request.get_absolute_url())
        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.ABORTED)
        assert self.work_request.workflow_data is not None
        manual_unblock = self.work_request.workflow_data.manual_unblock
        assert manual_unblock is not None
        self.assertEqual(len(manual_unblock.log), 1)
        self.assertEqual(manual_unblock.log[0].user_id, self.scenario.user.id)
        self.assertLess(manual_unblock.log[0].timestamp, timezone.now())
        self.assertEqual(manual_unblock.log[0].notes, "Go away")
        self.assertEqual(
            manual_unblock.log[0].action, WorkRequestManualUnblockAction.REJECT
        )

    def test_record_notes_only(self) -> None:
        """Record notes on a blocked work request."""
        self.client.force_login(self.scenario.user)
        with override_permission(WorkRequest, "can_unblock", AllowAll):
            response = self.client.post(
                self.work_request.get_absolute_url_unblock(),
                {"notes": "Not sure", "action": "Record notes only"},
            )

        self.assertRedirects(response, self.work_request.get_absolute_url())
        self.work_request.refresh_from_db()
        self.assertEqual(self.work_request.status, WorkRequest.Statuses.BLOCKED)
        assert self.work_request.workflow_data is not None
        manual_unblock = self.work_request.workflow_data.manual_unblock
        assert manual_unblock is not None
        self.assertEqual(len(manual_unblock.log), 1)
        self.assertEqual(manual_unblock.log[0].user_id, self.scenario.user.id)
        self.assertLess(manual_unblock.log[0].timestamp, timezone.now())
        self.assertEqual(manual_unblock.log[0].notes, "Not sure")
        self.assertIsNone(manual_unblock.log[0].action)


class WorkRequestPluginTests(TestCase):
    """Tests for WorkRequestPlugin."""

    def setUp(self) -> None:
        super().setUp()

        # Copy it to unregister the temporary NoopPlugin
        self._work_request_plugin_orig = copy.deepcopy(
            WorkRequestPlugin._work_request_plugins
        )

        class NoopPlugin(WorkRequestPlugin):
            """Noop plugin."""

            task_type = TaskTypes.WORKER
            task_name = "noop"

        work_request = self.playground.create_work_request(task_name="noop")
        self.plugin = NoopPlugin(work_request)

    def tearDown(self) -> None:
        # WorkRequestPlugins as before
        WorkRequestPlugin._work_request_plugins = self._work_request_plugin_orig
        super().tearDown()

    def test_get_description_data(self) -> None:
        """Return value from do_get_description_data() (dynamic_data is set)."""
        self.plugin.task.dynamic_data = BaseDynamicTaskData()

        with patch.object(
            self.plugin, "do_get_description_data", return_value="X"
        ) as mocked:
            self.assertEqual(self.plugin.get_description_data(), "X")
            mocked.assert_called_once()

    def test_get_description_data_dynamic_data_is_none(self) -> None:
        """Return empty dictionary (dynamic data is None)."""
        self.plugin.task.dynamic_data = None

        with patch.object(self.plugin, "do_get_description_data") as mocked:
            result = self.plugin.get_description_data()
            self.assertEqual(result, {})
            mocked.assert_not_called()

    def test_do_get_description_data(self) -> None:
        """Default implementation do_get_description_data() return {}."""
        self.assertEqual(self.plugin.do_get_description_data(), {})

    def test_task(self) -> None:
        """WorkRequestPlugin.task return the Task."""
        self.assertIsInstance(self.plugin.task, Noop)
