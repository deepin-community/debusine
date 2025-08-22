# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the artifact views."""

import abc
import io
import json
import os.path
import tarfile
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar, cast

import lxml
import yaml
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Max, TextChoices
from django.db.models.functions import Lower
from django.http.response import HttpResponseBase
from django.test import Client
from django.urls import reverse
from django.utils.http import http_date
from django.utils.timesince import timesince
from rest_framework import status

from debusine.artifacts.models import ArtifactCategory
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    FileInArtifact,
    Token,
    User,
    WorkRequest,
    Workspace,
)
from debusine.db.playground import scenarios
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    TestResponseType,
    override_permission,
)
from debusine.web.views import ui_shortcuts
from debusine.web.views.artifacts import ArtifactDetailView, FileView
from debusine.web.views.tests.utils import ViewTestMixin, date_format


class PermissionTests(TestCase, abc.ABC):
    """Permission checks common to all other test cases."""

    # Note: this is deleted at the end of the file, to prevent it from being
    # run as a test case

    user: ClassVar[User]

    @abc.abstractmethod
    def permission_tests_get(
        self, *, include_token: bool, public_workspace: bool = False
    ) -> TestResponseType:
        """Override to perform a get request to drive permission tests."""

    def test_check_denied_private_workspace(self) -> None:
        """Permission denied: no token, logged user or public workspace."""
        response = self.permission_tests_get(include_token=False)
        self.assertContains(
            response,
            "Workspace System not found in scope debusine, or you are not "
            "authorized to see it",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_check_permissions_valid_token_allowed(self) -> None:
        """Permission granted: valid token."""
        response = self.permission_tests_get(include_token=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_check_permissions_public_workspace(self) -> None:
        """Permission granted: without a token but it is a public workspace."""
        response = self.permission_tests_get(
            include_token=False, public_workspace=True
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_check_permission_logged_user(self) -> None:
        """Permission granted: user is logged in."""
        self.client.force_login(self.user)

        response = self.permission_tests_get(include_token=False)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_check_can_display(self) -> None:
        """Check that Artifact.can_display is honored."""
        with override_permission(Artifact, "can_display", AllowAll):
            response = self.permission_tests_get(
                include_token=False, public_workspace=True
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        with override_permission(Artifact, "can_display", DenyAll):
            response = self.permission_tests_get(
                include_token=False, public_workspace=True
            )
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ArtifactDetailViewTests(ViewTestMixin, PermissionTests):
    """Tests for the ArtifactDetailView class."""

    user: ClassVar[User]
    token: ClassVar[Token]
    path_in_artifact: ClassVar[str]
    file_size: ClassVar[int]
    artifact: ClassVar[Artifact]
    files_contents: ClassVar[dict[str, bytes]]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up the test fixture."""
        super().setUpTestData()
        cls.user = cls.playground.get_default_user()
        cls.token = cls.playground.create_worker_token()
        cls.path_in_artifact = "README.md"
        cls.file_size = 100
        cls.artifact, cls.files_contents = cls.playground.create_artifact(
            [cls.path_in_artifact, "AUTHORS"],
            files_size=cls.file_size,
            expiration_delay=1,
            create_files=True,
        )
        cls.playground.create_group_role(
            cls.artifact.workspace, Workspace.Roles.OWNER, users=[cls.user]
        )

    def permission_tests_get(
        self, *, include_token: bool, public_workspace: bool = False
    ) -> TestResponseType:
        """Perform a get request to drive permission tests."""
        if public_workspace:
            self.assertTrue(self.artifact.workspace.public)
        else:
            self.artifact.workspace.public = False
            self.artifact.workspace.save()

        headers: dict[str, Any] = {}
        if include_token:
            headers["HTTP_TOKEN"] = self.token.key
        return self.client.get(
            self.artifact.get_absolute_url(),
            **headers,
        )

    def _get(
        self,
        pk: int | None = None,
    ) -> TestResponseType:
        """GET request on the ArtifactDetail view."""
        if pk is None:
            pk = self.artifact.pk
        return self.client.get(
            reverse(
                "workspaces:artifacts:detail",
                kwargs={
                    "wname": self.playground.get_default_workspace().name,
                    "artifact_id": pk,
                },
            ),
            headers={"token": self.token.key},
        )

    def assertFile(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        artifact: Artifact,
        *,
        suffix: str | None = None,
    ) -> None:
        """Ensure that the single file in the artifact is shown."""
        file = artifact.fileinartifact_set.select_related("file").get()
        div_id = (
            "file-contents" if suffix is None else f"file-contents-{suffix}"
        )
        file_contents = self.assertHasElement(tree, f"//div[@id='{div_id}']")

        self.assertTextContentEqual(file_contents.div[0].div[0], file.path)
        gadgets = file_contents.div[0].div[1]

        if file.complete:
            self.assertEqual(gadgets.get("class"), "btn-group btn-group-sm")
            self.assertEqual(
                gadgets.a[0].get("href"), file.get_absolute_url_raw()
            )
            self.assertEqual(
                gadgets.a[1].get("href"), file.get_absolute_url_download()
            )
            card_body_class = file_contents.div[1].get("class")
            assert card_body_class is not None
            self.assertIn("card-body", card_body_class.split())
        else:
            self.assertTextContentEqual(gadgets, "incomplete")
            self.assertEqual(len(file_contents.div), 1)

    def assertFileList(
        self, tree: lxml.objectify.ObjectifiedElement, artifact: Artifact
    ) -> None:
        """Ensure there is a file list with all files in the artifact."""
        files = artifact.fileinartifact_set.select_related("file").order_by(
            Lower("path")
        )
        table = self.assertHasElement(tree, "//table[@id='file-list']")

        for tr, file in zip(table.tbody.tr, files):
            with self.subTest(file.path):
                basename = os.path.basename(file.path)
                if file.complete:
                    self.assertEqual(
                        tr.td[0].a.get("href"), file.get_absolute_url()
                    )
                    self.assertTextContentEqual(tr.td[0].a, basename)
                else:
                    self.assertEqual(tr.td[0].countchildren(), 0)
                    self.assertTextContentEqual(
                        tr.td[0], f"{basename} (incomplete)"
                    )
                self.assertEqual(tr.td[1].get("title"), str(file.file.size))

    def assertRelations(
        self, tree: lxml.objectify.ObjectifiedElement, artifact: Artifact
    ) -> None:
        """Ensure that there is a relation list with all the relations."""
        relations = artifact.relations.all()
        targeted_by = artifact.targeted_by.filter(
            type=ArtifactRelation.Relations.EXTENDS
        )
        num_relations = len(relations) + len(targeted_by)
        tables = tree.xpath("//table[@id='relation-list']")

        relation_button = self.assertHasElement(
            tree, "//button[@id='nav-relations-tab']"
        )

        if num_relations == 1:
            self.assertTextContentEqual(relation_button, "Relation (1)")
        else:
            self.assertTextContentEqual(
                relation_button, f"Relations ({num_relations})"
            )

        button_classes = relation_button.get("class")
        assert button_classes is not None

        if num_relations == 0:
            self.assertIn("disabled", button_classes.split())

            # No "relation-list" tables
            self.assertFalse(tables)

            div = tree.xpath("//div[@id='nav-relations']")
            self.assertTextContentEqual(div[0], "No relations.")
            return

        self.assertNotIn("disabled", button_classes.split())

        table = tables[0]
        self.assertEqual(len(table.tbody.tr), num_relations)
        for tr, relation in zip(table.tbody.tr, relations):
            with self.subTest(str(tr)):
                self.assertEqual(tr.td[0].text, relation.type)
                self.assertEqual(
                    tr.td[1].i.attrib["class"], "bi bi-arrow-right"
                )
                label = relation.target.get_label()
                self.assertEqual(tr.td[3].a.text, label)
                if relation.target.fileinartifact_set.filter(
                    complete=False
                ).exists():
                    self.assertTextContentEqual(
                        tr.td[3], f"{label} (incomplete)"
                    )
                else:
                    self.assertTextContentEqual(tr.td[3], label)
        for tr, relation in zip(table.tbody.tr[len(relations) :], targeted_by):
            with self.subTest(str(tr)):
                self.assertEqual(tr.td[0].text, relation.type)
                self.assertEqual(tr.td[1].i.attrib["class"], "bi bi-arrow-left")
                label = relation.artifact.get_label()
                self.assertEqual(tr.td[3].a.text, label)
                if relation.artifact.fileinartifact_set.filter(
                    complete=False
                ).exists():
                    self.assertTextContentEqual(
                        tr.td[3], f"{label} (incomplete)"
                    )
                else:
                    self.assertTextContentEqual(tr.td[3], label)

    def test_permissions(self) -> None:
        """Test basic permission enforcement."""
        self.assertSetsCurrentWorkspace(
            self.artifact.workspace, self.artifact.get_absolute_url()
        )
        self.assertEnforcesPermission(
            self.artifact.can_display,
            self.artifact.get_absolute_url(),
            "get_context_data",
        )

    def test_get_title(self) -> None:
        """Test get_title method."""
        view = ArtifactDetailView()
        view.object = self.artifact
        self.assertEqual(view.get_title(), "Artifact debusine:test")

    def test_invalid_artifact_id(self) -> None:
        """Test viewing an artifact ID that does not exist."""
        artifact_id = Artifact.objects.aggregate(Max("id"))['id__max'] + 1
        response = self._get(pk=artifact_id)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"],
            "No artifact found matching the query",
        )

    def test_get_success_html_files_list(self) -> None:
        """View shows a list of files."""
        work_request = self.playground.create_work_request(task_name="noop")
        with context.disable_permission_checks():
            self.artifact.created_by_work_request = work_request
            self.artifact.save()
        response = self._get()
        tree = self.assertResponseHTML(response)
        self.assertFileList(tree, self.artifact)
        self.assertRelations(tree, self.artifact)
        self.assertFalse(tree.xpath("//div[@id='file-contents']"))

        file_button = self.assertHasElement(
            tree, "//button[@id='nav-files-tab']"
        )
        assert file_button is not None
        self.assertTextContentEqual(file_button, "Files (2)")

        # Assert file contents is displayed by default
        file_contents = self.assertHasElement(tree, "//div[@id='nav-files']")
        self.assertIn("show active", file_contents.attrib["class"])

    def test_get_success_html_files_list_incomplete(self) -> None:
        """View shows incomplete files in a list differently."""
        self.artifact.fileinartifact_set.filter(path="AUTHORS").update(
            complete=False
        )
        response = self._get()
        tree = self.assertResponseHTML(response)
        self.assertFileList(tree, self.artifact)
        self.assertRelations(tree, self.artifact)
        self.assertFalse(tree.xpath("//div[@id='file-contents']"))

    def test_get_success_html_singlefile(self) -> None:
        """View show the content of the only file in the artifact."""
        self.artifact.fileinartifact_set.filter(path="AUTHORS").delete()
        response = self._get()
        tree = self.assertResponseHTML(response)
        file_button = self.assertHasElement(
            tree, "//button[@id='nav-files-tab']"
        )
        assert file_button is not None
        self.assertTextContentEqual(file_button, "File (1)")

        file_classes = file_button.get("class")
        assert file_classes is not None
        self.assertNotIn("disabled", file_classes.split())
        self.assertFalse(tree.xpath("//table[@id='file-list']"))
        self.assertFile(tree, self.artifact, suffix="files")

    def test_get_success_html_singlefile_incomplete(self) -> None:
        """View shows a single incomplete file in the artifact differently."""
        self.artifact.fileinartifact_set.filter(path="AUTHORS").delete()
        self.artifact.fileinartifact_set.update(complete=False)
        response = self._get()
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath("//table[@id='file-list']"))
        self.assertFile(tree, self.artifact, suffix="files")

    def test_get_success_html_empty_artifact(self) -> None:
        """Test HTML output if there are no files in the artifact."""
        artifact, _ = self.playground.create_artifact([])
        response = self._get(artifact.id)
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath("//table[@id='file-list']"))
        self.assertFalse(tree.xpath("//div[@id='file-contents']"))

        files_button = self.assertHasElement(
            tree, "//button[@id='nav-files-tab']"
        )
        self.assertTextContentEqual(files_button, "Files (0)")
        files_classes = files_button.get("class")
        assert files_classes is not None
        self.assertIn("disabled", files_classes.split())

        div = tree.xpath("//div[@id='nav-files']")
        self.assertTextContentEqual(div[0], "No files.")

    def test_get_success_html_user_and_no_expiration(self) -> None:
        """Test HTML output with no user and expiration."""
        with context.disable_permission_checks():
            self.artifact.created_by = self.user
            self.artifact.expiration_delay = timedelta(0)
            self.artifact.save()
        response = self._get()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_success_html_fwd_relations(self) -> None:
        """View shows a list of forward relations."""
        other_artifact = self.playground.create_build_log_artifact()
        self.playground.create_artifact_relation(self.artifact, other_artifact)
        response = self._get()
        tree = self.assertResponseHTML(response)
        self.assertRelations(tree, self.artifact)

    def test_get_success_html_fwd_relations_incomplete(self) -> None:
        """View shows whether forward-related artifacts are incomplete."""
        other_artifact = self.playground.create_build_log_artifact(
            skip_add_files_in_store=True
        )
        self.playground.create_artifact_relation(self.artifact, other_artifact)
        response = self._get()
        tree = self.assertResponseHTML(response)
        self.assertRelations(tree, self.artifact)

    def test_get_success_html_rev_relations(self) -> None:
        """View shows a list of reverse relations."""
        other_artifact = self.playground.create_build_log_artifact()
        self.playground.create_artifact_relation(other_artifact, self.artifact)
        self.playground.create_artifact_relation(
            other_artifact,
            self.artifact,
            relation_type=ArtifactRelation.Relations.EXTENDS,
        )
        response = self._get()
        tree = self.assertResponseHTML(response)
        self.assertRelations(tree, self.artifact)

    def test_get_success_html_rev_relations_incomplete(self) -> None:
        """View shows whether reverse-related artifacts are incomplete."""
        other_artifact = self.playground.create_build_log_artifact(
            skip_add_files_in_store=True
        )
        self.playground.create_artifact_relation(other_artifact, self.artifact)
        self.playground.create_artifact_relation(
            other_artifact,
            self.artifact,
            relation_type=ArtifactRelation.Relations.EXTENDS,
        )
        response = self._get()
        tree = self.assertResponseHTML(response)
        self.assertRelations(tree, self.artifact)

    def test_ui_shortcuts_source(self) -> None:
        """Check that UI shortcuts for source packages are as expected."""
        artifact = self.playground.create_source_artifact()
        response = self._get(pk=artifact.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_artifact_download(artifact),
            ],
        )

    def test_ui_shortcuts_source_with_work_request(self) -> None:
        """Check UI shortcuts for artifact with a work request."""
        work_request = self.playground.create_work_request(task_name="noop")
        artifact = self.playground.create_source_artifact(
            work_request=work_request
        )
        response = self._get(pk=artifact.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_artifact_download(artifact),
            ],
        )

    def test_ui_shortcuts_build_log(self) -> None:
        """Check UI shortcuts for build logs."""
        artifact = self.playground.create_build_log_artifact()
        response = self._get(pk=artifact.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_artifact_download(artifact),
            ],
        )

    def test_ui_shortcuts_build_log_with_work_request(self) -> None:
        """Check UI shortcuts for build logs part of a work request."""
        work_request = self.playground.create_work_request(task_name="noop")
        artifact = self.playground.create_build_log_artifact(
            work_request=work_request
        )
        response = self._get(pk=artifact.pk)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_artifact_download(artifact),
            ],
        )

    def test_ui_shortcuts_related_build_log(self) -> None:
        """Check UI shortcuts for build logs."""
        work_request = self.playground.create_work_request(task_name="noop")
        with context.disable_permission_checks():
            self.artifact.created_by_work_request = work_request
            self.artifact.save()
        build_log = self.playground.create_build_log_artifact(
            work_request=work_request
        )
        response = self._get()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_artifact_view(build_log),
                ui_shortcuts.create_artifact_download(self.artifact),
            ],
        )

    def test_ui_shortcuts_multiple_build_log(self) -> None:
        """Check UI shortcuts for a work request with multiple build logs."""
        work_request = self.playground.create_work_request(task_name="noop")
        with context.disable_permission_checks():
            self.artifact.created_by_work_request = work_request
            self.artifact.save()
        # This can happen if the work request is retried.
        build_logs = [
            self.playground.create_build_log_artifact(work_request=work_request)
            for _ in range(2)
        ]
        response = self._get()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_artifact_view(build_logs[-1]),
                ui_shortcuts.create_artifact_download(self.artifact),
            ],
        )

    def test_metadata(self) -> None:
        tree = self.assertResponseHTML(self._get())
        table = self.assertHasElement(tree, "//table[@id='metadata-table']")

        artifact = self.artifact

        # Assert Category
        category = self.assertHasElement(table, ".//tr[1]/td[1]")
        self.assertTextContentEqual(category, artifact.category)

        # Assert Workspace
        workspace_link = self.assertHasElement(table, ".//tr[2]/td[1]/a")
        self.assertEqual(
            workspace_link.get("href"), artifact.workspace.get_absolute_url()
        )

        self.assertTextContentEqual(workspace_link, str(artifact.workspace))

        # Assert Created at
        created_at = self.assertHasElement(table, ".//tr[3]/td[1]")
        self.assertTextContentEqual(
            created_at, f"{timesince(artifact.created_at)} ago"
        )

        # Assert user
        user = self.assertHasElement(table, ".//tr[4]/td[1]")
        self.assertTextContentEqual(user, str(artifact.created_by))

        # Assert Expiration date
        expiration = self.assertHasElement(table, ".//tr[5]/td[1]")
        assert artifact.expire_at is not None
        self.assertTextContentEqual(expiration, date_format(artifact.expire_at))

        # Assert Data
        data = self.assertHasElement(table, ".//tr[7]/td[1]")
        self.assertEqual(
            json.loads(self.get_node_text_normalized(data)), artifact.data
        )

    def test_origin_created_by_user(self) -> None:
        self.artifact.created_by = self.user
        self.artifact.save()

        tree = self.assertResponseHTML(self._get())

        created_by = self.assertHasElement(
            tree, "//p[@id='artifact-created-by-user']"
        )
        self.assertTextContentEqual(
            created_by, f"Artifact created by {self.user}."
        )
        self.assertEqual(
            created_by.a.attrib["href"], self.user.get_absolute_url()
        )

    def test_origin_work_request_no_workflow(self) -> None:
        work_request = self.playground.create_work_request(
            task_name="noop", assign_new_worker=True
        )
        with context.disable_permission_checks():
            self.artifact.created_by_work_request = work_request
            self.artifact.save()

        tree = self.assertResponseHTML(self._get())
        table = self.assertHasElement(tree, "//table[@id='work_request-table']")

        # Assert work_request link
        work_request_link = self.assertHasElement(table, ".//tr[1]/td[1]/a")
        self.assertEqual(
            work_request_link.get("href"), work_request.get_absolute_url()
        )
        self.assertTextContentEqual(
            work_request_link, f"{work_request.id} ({work_request.task_name})"
        )

        # Assert Status and result
        status_result = self.assertHasElement(table, ".//tr[2]/td[1]")
        work_request_status = WorkRequest.Statuses(work_request.status)
        work_request_result = WorkRequest.Results(work_request.result)
        self.assertTextContentEqual(
            status_result,
            f"{work_request_status.label} {work_request_result.label}",
        )

        # Assert workspace link
        workspace_link = self.assertHasElement(table, ".//tr[3]/td[1]/a")
        self.assertEqual(
            workspace_link.get("href"),
            work_request.workspace.get_absolute_url(),
        )
        self.assertTextContentEqual(workspace_link, str(work_request.workspace))

        # Assert created by user
        user = self.assertHasElement(table, ".//tr[4]/td[1]")
        self.assertTextContentEqual(user, str(work_request.created_by))

        # Assert created at
        created_at = self.assertHasElement(table, ".//tr[5]/td[1]")
        self.assertTextContentEqual(
            created_at, f"{timesince(work_request.created_at)} ago"
        )

        # Assert worker
        worker = self.assertHasElement(table, ".//tr[6]/td[1]")
        assert work_request.worker
        self.assertTextContentEqual(worker, work_request.worker.name)

        # Assert no workflow table
        with self.assertRaises(AssertionError):
            self.assertHasElement(tree, "//table[@id='workflow-table']")

    def test_origin_work_request_workflow(self) -> None:
        workflow = self.playground.create_workflow()

        work_request = self.playground.create_work_request(
            task_name="noop", assign_new_worker=True, parent=workflow
        )
        with context.disable_permission_checks():
            self.artifact.created_by_work_request = work_request
            self.artifact.save()

        tree = self.assertResponseHTML(self._get())

        # Has the work_request-table, tested in
        # test_origin_work_request_no_workflow()
        self.assertHasElement(tree, "//table[@id='work_request-table']")

        table = self.assertHasElement(tree, "//table[@id='workflow-table']")

        # Assert workflow link
        workflow_link = self.assertHasElement(table, ".//tr[1]/td[1]/a")
        self.assertEqual(workflow_link.get("href"), workflow.get_absolute_url())
        self.assertTextContentEqual(
            workflow_link, f"{workflow.id} ({workflow.task_name})"
        )

        # Assert status and result
        status_result = self.assertHasElement(table, ".//tr[2]/td[1]")
        workflow_status = cast(TextChoices, workflow.status)
        workflow_result = cast(TextChoices, workflow.result)
        self.assertTextContentEqual(
            status_result,
            f"{workflow_status.label} {workflow_result.label}",
        )

        # Assert workspace link
        workspace_link = self.assertHasElement(table, ".//tr[3]/td[1]/a")
        self.assertEqual(
            workspace_link.get("href"),
            workflow.workspace.get_absolute_url(),
        )
        self.assertTextContentEqual(workspace_link, str(workflow.workspace))

        # Assert created by user
        user = self.assertHasElement(table, ".//tr[4]/td[1]")
        self.assertTextContentEqual(user, str(workflow.created_by))

        # Assert created at
        created_at = self.assertHasElement(table, ".//tr[5]/td[1]")
        self.assertTextContentEqual(
            created_at, f"{timesince(workflow.created_at)} ago"
        )


class FileViewTests(TestCase):
    """Test :py:class:`FileView`."""

    contents: ClassVar[dict[str, bytes]]
    artifact: ClassVar[Artifact]
    file: ClassVar[FileInArtifact]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up the common test fixture."""
        super().setUpTestData()
        cls.contents = {
            "file.md": b"Line 1\nLine 2\n",
        }
        cls.artifact, _ = cls.playground.create_artifact(
            paths=cls.contents,
            create_files=True,
        )
        cls.file = cls.artifact.fileinartifact_set.get(path="file.md")

    def test_get_title(self) -> None:
        """Test get_title method."""
        view = FileView()
        view.object = self.file
        self.assertEqual(view.get_title(), self.file.path)

    def test_get_title_incomplete(self) -> None:
        """Test get_title method with an incomplete file."""
        self.file.complete = False
        view = FileView()
        view.object = self.file
        self.assertEqual(view.get_title(), self.file.path + " (incomplete)")


class FileDetailViewTests(ViewTestMixin, PermissionTests):
    """Test FileDetailView."""

    user: ClassVar[User]
    token: ClassVar[Token]
    contents: ClassVar[dict[str, bytes]]
    artifact: ClassVar[Artifact]
    file: ClassVar[FileInArtifact]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up the common test fixture."""
        super().setUpTestData()
        cls.user = cls.playground.get_default_user()
        cls.token = cls.playground.create_worker_token()
        cls.contents = {
            "file.md": b"Line 1\nLine 2\n",
        }
        cls.artifact, _ = cls.playground.create_artifact(
            paths=cls.contents,
            create_files=True,
        )
        cls.file = cls.artifact.fileinartifact_set.get(path="file.md")
        cls.playground.create_group_role(
            cls.artifact.workspace, Workspace.Roles.OWNER, users=[cls.user]
        )

    def permission_tests_get(
        self, *, include_token: bool, public_workspace: bool = False
    ) -> TestResponseType:
        """Perform a get request to drive permission tests."""
        if public_workspace:
            self.assertTrue(self.artifact.workspace.public)
        else:
            self.artifact.workspace.public = False
            self.artifact.workspace.save()

        headers: dict[str, Any] = {}
        if include_token:
            headers["HTTP_TOKEN"] = self.token.key
        return self.client.get(
            self.file.get_absolute_url(),
            **headers,
        )

    def test_permissions(self) -> None:
        """Test basic permission enforcement."""
        url = self.file.get_absolute_url()
        self.assertSetsCurrentWorkspace(self.artifact.workspace, url)
        self.assertEnforcesPermission(
            self.artifact.can_display,
            url,
            "get_context_data",
        )

    def test_invalid_artifact_id(self) -> None:
        """Test viewing an artifact ID that does not exist."""
        artifact_id = Artifact.objects.aggregate(Max("id"))['id__max'] + 1
        response = self.client.get(
            reverse(
                "workspaces:artifacts:file-detail",
                kwargs={
                    "wname": self.playground.get_default_workspace().name,
                    "artifact_id": artifact_id,
                    "path": self.file.path,
                },
            ),
            headers={"token": self.token.key},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"],
            "No FileInArtifact matches the given query.",
        )

    def test_invalid_file_in_artifact_path(self) -> None:
        """Test viewing a file_path that does not exist."""
        response = self.client.get(
            reverse(
                "workspaces:artifacts:file-detail",
                kwargs={
                    "wname": self.artifact.workspace.name,
                    "artifact_id": self.artifact.id,
                    "path": "invalid-path",
                },
            ),
            headers={"token": self.token.key},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"],
            "No FileInArtifact matches the given query.",
        )

    def test_get(self) -> None:
        """Test a simple get."""
        response = self.permission_tests_get(include_token=True)
        tree = self.assertResponseHTML(response)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_file_view_raw(self.file),
                ui_shortcuts.create_file_download(self.file),
                ui_shortcuts.create_artifact_view(self.artifact),
            ],
        )
        self.assertIn("file", response.context)
        title = self.assertHasElement(tree, "head/title")
        self.assertTextContentEqual(title, f"Debusine - {self.file.path}")

    def test_get_incomplete(self) -> None:
        """Test a get of an incomplete file."""
        self.file.complete = False
        self.file.save()
        response = self.permission_tests_get(include_token=True)
        tree = self.assertResponseHTML(response)
        self.assertEqual(
            response.context["main_ui_shortcuts"],
            [
                ui_shortcuts.create_file_view_raw(self.file),
                ui_shortcuts.create_file_download(self.file),
                ui_shortcuts.create_artifact_view(self.artifact),
            ],
        )
        self.assertIn("file", response.context)
        title = self.assertHasElement(tree, "head/title")
        self.assertTextContentEqual(
            title, f"Debusine - {self.file.path} (incomplete)"
        )


class FileDetailViewRawTests(ViewTestMixin, PermissionTests):
    """Test FileDetailRawView."""

    playground_memory_file_store = False

    user: ClassVar[User]
    token: ClassVar[Token]
    contents: ClassVar[dict[str, bytes]]
    artifact: ClassVar[Artifact]
    file: ClassVar[FileInArtifact]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up the common test fixture."""
        super().setUpTestData()
        cls.user = cls.playground.get_default_user()
        cls.token = cls.playground.create_worker_token()
        cls.contents = {
            "file.md": b"Line 1\nLine 2\n",
        }
        cls.artifact, _ = cls.playground.create_artifact(
            paths=cls.contents,
            create_files=True,
        )
        cls.file = cls.artifact.fileinartifact_set.get(path="file.md")
        cls.playground.create_group_role(
            cls.artifact.workspace, Workspace.Roles.OWNER, users=[cls.user]
        )

    def permission_tests_get(
        self, *, include_token: bool, public_workspace: bool = False
    ) -> TestResponseType:
        """Perform a get request to drive permission tests."""
        if public_workspace:
            self.assertTrue(self.artifact.workspace.public)
        else:
            self.artifact.workspace.public = False
            self.artifact.workspace.save()

        headers: dict[str, Any] = {}
        if include_token:
            headers["HTTP_TOKEN"] = self.token.key
        return self.client.get(
            self.file.get_absolute_url_raw(),
            **headers,
        )

    def test_permissions(self) -> None:
        """Test basic permission enforcement."""
        url = self.file.get_absolute_url_raw()
        self.assertSetsCurrentWorkspace(self.artifact.workspace, url)
        self.assertEnforcesPermission(
            self.artifact.can_display,
            url,
            "get",
        )

    def test_invalid_file_in_artifact_path(self) -> None:
        """Test viewing a file_path that does not exist."""
        response = self.client.get(
            reverse(
                "workspaces:artifacts:file-raw",
                kwargs={
                    "wname": self.artifact.workspace.name,
                    "artifact_id": self.artifact.id,
                    "path": "invalid-path",
                },
            ),
            headers={"token": self.token.key},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"],
            "No FileInArtifact matches the given query.",
        )

    def test_get(self) -> None:
        """Test a simple get."""
        response = self.permission_tests_get(include_token=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.headers["Content-Disposition"],
            'inline; filename="file.md"',
        )


class DownloadPathViewTests(ViewTestMixin, PermissionTests):
    """Tests for the DownloadPathView class."""

    playground_memory_file_store = False

    user: ClassVar[User]
    token: ClassVar[Token]
    path_in_artifact: ClassVar[str]
    file_size: ClassVar[int]
    tree_paths: ClassVar[list[str]]
    artifact: ClassVar[Artifact]
    tree: ClassVar[Artifact]
    files_contents: ClassVar[dict[str, bytes]]
    tree_files_contents: ClassVar[dict[str, bytes]]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up the test fixture."""
        super().setUpTestData()
        cls.user = cls.playground.get_default_user()
        cls.token = cls.playground.create_worker_token()
        cls.path_in_artifact = "README.md"
        cls.file_size = 100
        cls.artifact, cls.files_contents = cls.playground.create_artifact(
            [cls.path_in_artifact],
            files_size=cls.file_size,
            create_files=True,
        )
        cls.tree_paths = [
            "README",
            "doc/README",
            "doc/README2",
            "documentation",
            "src/lib/main.c",
            "src/lib/utils.c",
        ]
        cls.tree, cls.tree_files_contents = cls.playground.create_artifact(
            cls.tree_paths, create_files=True
        )
        cls.playground.create_group_role(
            cls.artifact.workspace, Workspace.Roles.OWNER, users=[cls.user]
        )

    def permission_tests_get(
        self, *, include_token: bool, public_workspace: bool = False
    ) -> TestResponseType:
        """Perform a get request to drive permission tests."""
        if public_workspace:
            self.assertTrue(self.artifact.workspace.public)
        else:
            self.artifact.workspace.public = False
            self.artifact.workspace.save()

        headers: dict[str, Any] = {}
        if include_token:
            headers["HTTP_TOKEN"] = self.token.key
        return self.client.get(
            reverse(
                "workspaces:artifacts:file-download",
                kwargs={
                    "wname": self.artifact.workspace.name,
                    "artifact_id": self.artifact.id,
                    "path": "/",
                },
            ),
            **headers,
        )

    def get_file(
        self,
        *,
        artifact_id: int | None = None,
        path_file: str | None = None,
    ) -> HttpResponseBase:
        """
        Download file specified in the parameters.

        Unless specified: try to download the whole file (by default
        self.path_file and self.artifact.id).
        """
        if artifact_id is None:
            artifact_id = self.artifact.id

        if path_file is None:
            path_file = self.path_in_artifact

        return self.client.get(
            reverse(
                "workspaces:artifacts:file-download",
                kwargs={
                    "wname": self.artifact.workspace.name,
                    "artifact_id": artifact_id,
                    "path": path_file,
                },
            ),
            headers={"token": self.token.key},
        )

    def get_artifact(
        self,
        artifact: Artifact,
        archive: str | None = None,
        subdirectory: str | None = None,
        **get_kwargs: Any,
    ) -> TestResponseType:
        """Request to download an artifact_id."""
        reverse_kwargs: dict[str, Any] = {
            "wname": artifact.workspace.name,
            "artifact_id": artifact.id,
        }
        viewname = "workspaces:artifacts:download"
        if subdirectory is not None:
            viewname = "workspaces:artifacts:file-download"
            reverse_kwargs["path"] = subdirectory

        if archive is not None:
            get_kwargs["archive"] = archive

        return self.client.get(
            reverse(viewname, kwargs=reverse_kwargs),
            get_kwargs,
            headers={"token": self.token.key},
        )

    def assertFileResponse(
        self, response: HttpResponseBase, status_code: int
    ) -> None:
        """Assert that response has the expected headers and content."""
        self.assertEqual(response.status_code, status_code)
        headers = response.headers

        self.assertEqual(headers["Accept-Ranges"], "bytes")

        file_contents = self.files_contents[self.path_in_artifact]
        response_contents = file_contents

        self.assertEqual(headers["Content-Length"], str(len(response_contents)))
        self.assertEqual(
            headers["Content-Range"],
            f"bytes {0}-{self.file_size - 1}/{self.file_size}",
        )

        filename = Path(self.path_in_artifact).name
        self.assertEqual(
            headers["Content-Disposition"], f'attachment; filename="{filename}"'
        )

        assert hasattr(response, "streaming_content")
        self.assertEqual(
            b"".join(response.streaming_content), response_contents
        )

    def assertResponseDownloadsTree(self, response: HttpResponseBase) -> None:
        """Ensure response is a tar download of the self.tree artifact."""
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        assert hasattr(response, "streaming_content")
        response_content = io.BytesIO(b"".join(response.streaming_content))

        tar = tarfile.open(fileobj=response_content, mode="r:gz")

        # Check contents of the tar file
        for path in self.tree_paths:
            reader = tar.extractfile(path)
            assert reader is not None
            self.assertEqual(reader.read(), self.tree_files_contents[path])

        # Check relevant headers
        self.assertEqual(
            response.headers["Content-Type"], "application/octet-stream"
        )
        self.assertEqual(
            response.headers["Content-Disposition"],
            f'attachment; filename="artifact-{self.tree.id}.tar.gz"',
        )
        self.assertEqual(
            response.headers["Last-Modified"],
            http_date(self.tree.created_at.timestamp()),
        )

    def test_permissions(self) -> None:
        """Test basic permission enforcement."""
        url = reverse(
            "workspaces:artifacts:file-download",
            kwargs={
                "wname": self.artifact.workspace.name,
                "artifact_id": self.artifact.id,
                "path": "README",
            },
        )
        self.assertSetsCurrentWorkspace(self.artifact.workspace, url)
        self.assertEnforcesPermission(
            self.artifact.can_display,
            url,
            "get",
        )

    def test_path_url_does_not_end_in_slash(self) -> None:
        """
        URL to download a file does not end in /.

        If ending in / wget or curl -O save the file as index.html
        instead of using Content-Disposition filename.
        """
        url = reverse(
            "workspaces:artifacts:file-download",
            kwargs={"wname": "test", "artifact_id": 10, "path": "package.deb"},
        )
        self.assertFalse(url.endswith("/"))

    def test_get_file(self) -> None:
        """Get return the file."""
        response = self.get_file()
        self.assertFileResponse(response, status.HTTP_200_OK)
        self.assertEqual(
            response.headers["content-type"], "text/markdown; charset=utf-8"
        )

    def test_get_path_artifact_does_not_exist(self) -> None:
        """Get return 404: artifact not found."""
        non_existing_artifact_id = 0

        response = self.get_file(artifact_id=non_existing_artifact_id)

        self.assertContains(
            response,
            f"Artifact {non_existing_artifact_id} does not exist",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_file_file_does_not_exist(self) -> None:
        """Get return 404: artifact found but file not found."""
        file_path_no_exist = "does-not-exist"

        response = self.get_file(path_file=file_path_no_exist)

        self.assertContains(
            response,
            f'Artifact {self.artifact.id} does not have '
            f'any file or directory for "{file_path_no_exist}"',
            status_code=status.HTTP_404_NOT_FOUND,
            html=True,
        )

    def test_get_file_incomplete(self) -> None:
        """Get returns 404 for an incomplete file."""
        self.artifact.fileinartifact_set.update(complete=False)

        response = self.get_file()

        self.assertContains(
            response,
            f'Artifact {self.artifact.id} does not have any file or directory '
            f'for "{self.path_in_artifact}"',
            status_code=status.HTTP_404_NOT_FOUND,
            html=True,
        )

    def test_get_subdirectory_does_not_exist_404(self) -> None:
        """View return HTTP 404 Not Found: no files in the subdirectory."""
        subdirectory = "does-not-exist"
        response = self.get_artifact(self.artifact, "tar.gz", subdirectory)

        self.assertContains(
            response,
            f'Artifact {self.artifact.id} does not have any file or '
            f'directory for "{subdirectory}"',
            status_code=status.HTTP_404_NOT_FOUND,
            html=True,
        )

    def test_get_subdirectory_entirely_incomplete(self) -> None:
        """View returns 404 if all files in the subdirectory are incomplete."""
        self.tree.fileinartifact_set.filter(path__startswith="src/lib/").update(
            complete=False
        )

        subdirectory = "src/lib"
        response = self.get_artifact(self.tree, "tar.gz", subdirectory)

        self.assertContains(
            response,
            f'Artifact {self.tree.id} does not have any file or '
            f'directory for "{subdirectory}"',
            status_code=status.HTTP_404_NOT_FOUND,
            html=True,
        )

    def test_get_subdirectory_only_tar_gz(self) -> None:
        """View return tar.gz file with the files from a subdirectory."""
        subdirectory = "src/lib"
        response = self.get_artifact(self.tree, "tar.gz", subdirectory)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.headers["Content-Disposition"],
            f'attachment; filename="artifact-{self.tree.id}-src_lib.tar.gz"',
        )
        response_content = io.BytesIO(
            b"".join(getattr(response, "streaming_content"))
        )

        tar = tarfile.open(fileobj=response_content, mode="r:gz")

        expected_files = list(
            filter(lambda x: x.startswith(subdirectory + "/"), self.tree_paths)
        )
        self.assertEqual(tar.getnames(), expected_files)

    def test_get_subdirectory_only_excludes_incomplete(self) -> None:
        """Downloading a subdirectory excludes incomplete files."""
        excluded_file = self.tree.fileinartifact_set.get(path="src/lib/utils.c")
        excluded_file.complete = False
        excluded_file.save()

        subdirectory = "src/lib"
        response = self.get_artifact(self.tree, "tar.gz", subdirectory)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.headers["Content-Disposition"],
            f'attachment; filename="artifact-{self.tree.id}-src_lib.tar.gz"',
        )
        response_content = io.BytesIO(
            b"".join(getattr(response, "streaming_content"))
        )

        tar = tarfile.open(fileobj=response_content, mode="r:gz")

        expected_files = [
            path
            for path in self.tree_paths
            if (
                path.startswith(subdirectory + "/")
                and path != excluded_file.path
            )
        ]
        self.assertEqual(tar.getnames(), expected_files)

    def test_get_unsupported_archive_parameter(self) -> None:
        """View return HTTP 400 Bad Request: unsupported archive parameter."""
        archive_format = "tar.xz"
        response = self.get_artifact(self.artifact, archive_format)
        self.assertResponse400(
            response,
            f"Invalid archive parameter: '{archive_format}'. "
            "Supported: auto, tar.gz",
        )

    def test_path_without_archive(self) -> None:
        """Check downloading a path with a missing archive format."""
        response = self.get_artifact(
            self.tree, archive=None, subdirectory="src"
        )
        self.assertResponse400(
            response, "archive argument needed when downloading directories"
        )

    def test_get_artifact_auto_file(self) -> None:
        """Check downloading whole artifact with auto download format."""
        response = self.get_artifact(self.artifact, archive=None)
        self.assertFileResponse(response, status.HTTP_200_OK)
        self.assertEqual(
            response.headers["content-type"], "text/markdown; charset=utf-8"
        )

    def test_get_artifact_auto_only_one_complete_file(self) -> None:
        """Auto download format returns file if only one file is complete."""
        FileInArtifact.objects.create(
            artifact=self.artifact,
            path="incomplete",
            file=self.playground.create_file(b"incomplete"),
            complete=False,
        )
        response = self.get_artifact(self.artifact, archive=None)
        self.assertFileResponse(response, status.HTTP_200_OK)
        self.assertEqual(
            response.headers["content-type"], "text/markdown; charset=utf-8"
        )

    def test_get_artifact_auto_tree(self) -> None:
        """Check downloading whole artifact with auto download format."""
        response = self.get_artifact(self.tree, archive=None)
        self.assertResponseDownloadsTree(response)

    def test_get_artifact_tar_gz(self) -> None:
        """Download a whole artifact as .tar.gz."""
        response = self.get_artifact(self.tree, "tar.gz")
        self.assertResponseDownloadsTree(response)


class DownloadPathViewAuthTests(TestCase):
    """Tests for authorization on the DownloadPathView class."""

    playground_memory_file_store = False

    user: ClassVar[User]
    artifact: ClassVar[Artifact]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up the test fixture."""
        super().setUpTestData()
        cls.user = cls.playground.get_default_user()
        cls.artifact, _ = cls.playground.create_artifact(
            ["README"], files_size=100, create_files=True
        )

    def make_private(self) -> None:
        """Make the artifact workspace private."""
        self.artifact.workspace.public = False
        self.artifact.workspace.save()

    def make_owner(self) -> None:
        """Make the user owner of the workspace."""
        self.playground.create_group_role(
            self.artifact.workspace, Workspace.Roles.OWNER, users=[self.user]
        )

    def get(
        self,
        token: Token | None = None,
        user: User | None = None,
        subpath: bool = False,
    ) -> bool:
        """Call the whoami API view."""
        client = Client()
        if user is not None:
            client.force_login(user)

        headers = {}
        if token:
            headers["token"] = token.key

        reverse_kwargs: dict[str, Any] = {
            "wname": self.artifact.workspace.name,
            "artifact_id": self.artifact.id,
        }

        if subpath:
            viewname = "workspaces:artifacts:file-download"
            reverse_kwargs["path"] = "README"
        else:
            viewname = "workspaces:artifacts:download"

        response = client.get(
            reverse(viewname, kwargs=reverse_kwargs),
            headers=headers,
        )
        return response.status_code == status.HTTP_200_OK

    def assertAllowed(
        self, token: Token | None = None, user: User | None = None
    ) -> None:
        """Ensure the given auth credentials pass."""
        self.assertTrue(self.get(token=token, user=user, subpath=False))
        self.assertTrue(self.get(token=token, user=user, subpath=True))

    def assertDenied(
        self, token: Token | None = None, user: User | None = None
    ) -> None:
        """Ensure the given auth credentials do not pass."""
        self.assertFalse(self.get(token=token, user=user, subpath=False))
        self.assertFalse(self.get(token=token, user=user, subpath=True))

    def test_public_with_bare_token(self) -> None:
        """Try a bare token on a public workspace."""
        token = self.playground.create_bare_token()
        self.assertAllowed(token)

    def test_private_with_bare_token(self) -> None:
        """Try a bare token on a private workspace."""
        token = self.playground.create_bare_token()
        self.make_private()
        self.assertDenied(token)

    def test_public_with_worker_token(self) -> None:
        """Try a worker token on a public workspace."""
        token = self.playground.create_worker_token()
        self.assertAllowed(token)

    def test_private_with_worker_token(self) -> None:
        """Try a worker token on a private workspace."""
        token = self.playground.create_worker_token()
        self.make_private()
        self.assertAllowed(token)

    def test_public_with_user_token(self) -> None:
        """Try a user token on a public workspace."""
        token = self.playground.create_user_token()
        self.assertAllowed(token)

    def test_private_with_user_token(self) -> None:
        """Try a user token on a private workspace."""
        token = self.playground.create_user_token()
        self.make_private()
        self.assertDenied(token)

    def test_private_with_owner_token(self) -> None:
        """Try a owner user token on a private workspace."""
        token = self.playground.create_user_token()
        self.make_private()
        self.make_owner()
        self.assertAllowed(token)

    def test_public_with_token_disabled(self) -> None:
        """Try a disabled Token on a public workspace."""
        token = self.playground.create_user_token(enabled=False)
        self.assertAllowed(token)

    def test_private_with_token_disabled(self) -> None:
        """Try a disabled Token on a public workspace."""
        token = self.playground.create_user_token(enabled=False)
        self.make_private()
        self.assertDenied(token)

    def test_public_with_no_token(self) -> None:
        """Try without a token on a public workspace."""
        self.assertAllowed()

    def test_private_with_no_token(self) -> None:
        """Try without a token on a private workspace."""
        self.make_private()
        self.assertDenied()

    def test_public_with_user(self) -> None:
        """Try session user on a public workspace."""
        self.assertAllowed(user=self.user)

    def test_private_with_user(self) -> None:
        """Try session user on a private workspace."""
        self.make_private()
        self.assertDenied(user=self.user)

    def test_private_with_owner_user(self) -> None:
        """Try session user on an owned private workspace."""
        self.make_private()
        self.make_owner()
        self.assertAllowed(user=self.user)


class CreateArtifactViewTests(TestCase):
    """Tests for CreateArtifactView."""

    scenario = scenarios.DefaultContext()
    playground_memory_file_store = False

    def verify_create_artifact_with_files(
        self, files: list[SimpleUploadedFile]
    ) -> None:
        """
        Test CreateArtifactView via POST to downloads_artifact:create.

        Post the files to create an artifact and verify the created artifact
        and file upload.
        """
        self.client.force_login(self.scenario.user)

        # Create a dummy file for testing
        workspace = self.scenario.workspace
        category = ArtifactCategory.WORK_REQUEST_DEBUG_LOGS

        files_to_upload: SimpleUploadedFile | list[SimpleUploadedFile]
        if len(files) == 1:
            files_to_upload = files[0]
        else:
            files_to_upload = files

        post_data = {
            "category": category,
            "files": files_to_upload,
            "data": "",
        }

        response = self.client.post(
            reverse(
                "workspaces:artifacts:create", kwargs={"wname": workspace.name}
            ),
            post_data,
        )
        self.assertEqual(response.status_code, 302)

        artifact = Artifact.objects.latest("id")

        self.assertRedirects(response, artifact.get_absolute_url())

        # Verify artifact
        self.assertEqual(artifact.created_by, self.scenario.user)
        self.assertEqual(artifact.workspace, workspace)
        self.assertEqual(artifact.category, category)
        self.assertEqual(artifact.data, {})

        # Verify uploaded files
        self.assertEqual(artifact.fileinartifact_set.count(), len(files))

        for file_in_artifact, file_to_upload in zip(
            artifact.fileinartifact_set.all().order_by("id"), files
        ):
            file_backend = workspace.scope.download_file_backend(
                file_in_artifact.file
            )
            with file_backend.get_stream(file_in_artifact.file) as file:
                assert file_to_upload.file is not None
                file_to_upload.file.seek(0)
                content = file_to_upload.file.read()
                self.assertEqual(file.read(), content)
                self.assertEqual(file_in_artifact.path, file_to_upload.name)

            self.assertEqual(file_in_artifact.path, file_to_upload.name)

    def test_create_artifact_one_file(self) -> None:
        """Post to "user:artifact-create" to create an artifact: one file."""
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user],
        )
        file = SimpleUploadedFile("testfile.txt", b"some_file_content")
        self.verify_create_artifact_with_files([file])

    def test_create_artifact_two_files(self) -> None:
        """Post to "user:artifact-create" to create an artifact: two files."""
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user],
        )
        files = [
            SimpleUploadedFile("testfile.txt", b"some_file_content"),
            SimpleUploadedFile("testfile2.txt", b"another_file_content"),
        ]
        self.verify_create_artifact_with_files(files)

    def test_create_work_request_permission_denied(self) -> None:
        """A non-authenticated request cannot get the form (or post)."""
        url = reverse(
            "workspaces:artifacts:create",
            kwargs={"wname": self.scenario.workspace.name},
        )
        for method in ("get", "post"):
            with self.subTest(method):
                response = getattr(self.client, method)(url)
                self.assertEqual(
                    response.status_code, status.HTTP_403_FORBIDDEN
                )
                self.assertEqual(
                    response.context["exception"],
                    "User cannot create artifacts on debusine/System",
                )

    def test_invalid_form_data(self) -> None:
        """Invalid form data returns an error."""
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user],
        )
        url = reverse(
            "workspaces:artifacts:create",
            kwargs={"wname": self.scenario.workspace.name},
        )
        self.client.force_login(self.scenario.user)
        post_data = {
            "category": ArtifactCategory.PACKAGE_BUILD_LOG,
            "workspace": self.scenario.workspace.id,
            "files": [
                SimpleUploadedFile("testfile.txt", b"some_file_content"),
                SimpleUploadedFile("testfile2.txt", b"another_file_content"),
            ],
            "data": yaml.dump(
                {
                    "source": "hello",
                    "version": "1.0-1",
                    "filename": "testfile.txt",
                    "architecture": "amd64",
                }
            ),
        }

        response = self.client.post(url, post_data)
        self.assertContains(
            response,
            "Expected number of files: 1 Actual: 2",
            # HTTP 200 seems dubious, but it's apparently how Django forms
            # behave: https://code.djangoproject.com/ticket/22591
            status_code=status.HTTP_200_OK,
        )


del PermissionTests
