# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the forms."""
import textwrap
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar

from django import forms
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.datastructures import MultiValueDict

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic  # type: ignore

import yaml

from debusine.artifacts import PackageBuildLog
from debusine.artifacts.models import ArtifactCategory, DebianPackageBuildLog
from debusine.db.context import context
from debusine.db.models import Artifact, Group, Token, User, Workspace
from debusine.db.playground import scenarios
from debusine.tasks import BaseTask
from debusine.test.django import TestCase
from debusine.web.forms import (
    ArtifactForm,
    BootstrapMixin,
    DaysField,
    GroupAddUserForm,
    TokenForm,
    WorkRequestForm,
    WorkflowFilterForm,
    WorkspaceChoiceField,
    WorkspaceForm,
)


class BootstrapMixinForm(BootstrapMixin, forms.Form):
    """Class to test BootstrapMixinTestCase."""

    existing_class = "class1"
    char_field = forms.CharField()
    char_field_required = forms.CharField(required=True)
    char_field_required_with_suffix = forms.CharField(
        required=True, label_suffix="something"
    )
    char_field_extra_class = forms.CharField(
        widget=forms.TextInput(attrs={"class": existing_class})
    )
    choice_field = forms.ChoiceField(choices=[("1", "One"), ("2", "Two")])
    choice_field_extra_class = forms.ChoiceField(
        choices=[], widget=forms.Select(attrs={"class": existing_class})
    )
    datetime_field = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"class": existing_class})
    )
    file_field = forms.FileField()

    date_field = forms.DateField(
        widget=forms.DateInput(attrs={"class": existing_class})
    )
    boolean_field = forms.BooleanField(
        widget=forms.CheckboxInput(attrs={"class": existing_class})
    )


class BootstrapMixinTests(TestCase):
    """Tests for BootstrapMixin."""

    form: ClassVar[BootstrapMixinForm]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.form = BootstrapMixinForm()

    def test_charfield_bootstrap_class(self) -> None:
        """CharField has the correct Bootstrap class."""  # noqa: D403
        char_widget_class = self.form.fields["char_field"].widget.attrs.get(
            "class"
        )
        self.assertEqual(char_widget_class, "form-control")

    def test_charfield_bootstrap_class_add(self) -> None:
        """CharField has the correct classes."""  # noqa: D403
        char_widget_class = self.form.fields[
            "char_field_extra_class"
        ].widget.attrs.get("class")
        self.assertEqual(
            char_widget_class,
            f"{BootstrapMixinForm.existing_class} form-control",
        )

    def test_choicefield_bootstrap_class(self) -> None:
        """ChoiceField has the correct Bootstrap class."""  # noqa: D403
        choice_widget_class = self.form.fields["choice_field"].widget.attrs.get(
            "class"
        )
        self.assertEqual(choice_widget_class, "form-select")

    def test_choicefield_bootstrap_class_add(self) -> None:
        """ChoiceField has the correct classes."""  # noqa: D403
        choice_widget_class = self.form.fields[
            "choice_field_extra_class"
        ].widget.attrs.get("class")
        self.assertEqual(
            choice_widget_class,
            f"{BootstrapMixinForm.existing_class} form-select",
        )

    def test_filefield_bootstrap_class(self) -> None:
        """FileField has the correct Bootstrap class."""  # noqa: D403
        file_widget_class = self.form.fields["file_field"].widget.attrs.get(
            "class"
        )
        self.assertEqual(file_widget_class, "form-control")

    def test_datetimefield_bootstrap_class(self) -> None:
        """Test DateField retains its original class."""  # noqa: D403
        date_widget_class = self.form.fields["datetime_field"].widget.attrs.get(
            "class"
        )
        self.assertEqual(
            date_widget_class,
            f"{BootstrapMixinForm.existing_class} form-control",
        )

    def test_datefield_retain_existing_class(self) -> None:
        """
        Test DateField retains its original class.

        DateField is not modified: nothing is added.
        """  # noqa: D403
        date_widget_class = self.form.fields["date_field"].widget.attrs.get(
            "class"
        )
        self.assertEqual(
            date_widget_class, f"{BootstrapMixinForm.existing_class}"
        )

    def test_booleanfield_bootstrap_class(self) -> None:
        """Test BooleanField has form-check-input."""
        boolean_widget_class = self.form.fields[
            "boolean_field"
        ].widget.attrs.get("class")
        self.assertEqual(
            boolean_widget_class,
            f"{BootstrapMixinForm.existing_class} form-check-input",
        )

    def test_asterisk_set_if_required(self) -> None:
        """Test asterisk is set if field is required."""
        char_field_required = self.form.fields["char_field_required"]
        self.assertEqual(char_field_required.label_suffix, " *")

    def test_asterisk_added_if_required(self) -> None:
        """Test asterisk is added if field is required."""
        char_field_required = self.form.fields[
            "char_field_required_with_suffix"
        ]
        self.assertEqual(char_field_required.label_suffix, "something *")


class TokenFormTests(TestCase):
    """Tests for TokenForm."""

    user: ClassVar[User]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize test objects."""
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(
            username="testuser", password="testpass"
        )

    def test_form_initialization(self) -> None:
        """Form initialization set form.user."""
        form = TokenForm(user=self.user)
        self.assertEqual(form.user, self.user)
        self.assertTrue(form.fields["enabled"].initial)

    def test_form_save(self) -> None:
        """Form save the Token."""
        form = TokenForm({"comment": "Test Comment"}, user=self.user)
        self.assertTrue(form.is_valid())

        token = form.save()

        self.assertEqual(token.user, self.user)
        self.assertEqual(token.comment, "Test Comment")

    def test_form_save_commit_false(self) -> None:
        """Form does not save the token: commit=False."""
        form = TokenForm({"comment": "Test Comment"}, user=self.user)
        self.assertTrue(form.is_valid())

        token = form.save(commit=False)

        self.assertEqual(token.user, self.user)
        self.assertEqual(token.comment, "Test Comment")
        # Ensure token is not saved to the database yet
        with self.assertRaises(Token.DoesNotExist):
            Token.objects.get(comment="Test Comment")

    def test_form_validation(self) -> None:
        """Form return an error if the "comment" is not valid: too long."""
        assert Token.comment.field.max_length is not None
        form = TokenForm(
            {"comment": "x" * (Token.comment.field.max_length + 1)},
            user=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("comment", form.errors)

    def test_comment_allowed_empty(self) -> None:
        """Form validates if comment is empty."""
        form = TokenForm({"comment": ""}, user=self.user)
        self.assertTrue(form.is_valid())


class WorkRequestFormTests(TestCase):
    """Tests for WorkRequestForm."""

    user: ClassVar[User]
    workspace: ClassVar[Workspace]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize test objects."""
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(
            username="testuser", password="testpass"
        )
        cls.workspace = Workspace.objects.earliest("id")

    def test_form_initialization(self) -> None:
        """Form initialization set form.user."""
        form = WorkRequestForm(user=self.user, workspace=self.workspace)
        self.assertEqual(form.user, self.user)
        self.assertEqual(form.workspace, self.workspace)

    def test_form_save_no_commit(self) -> None:
        """Save method is called without committing to the DB."""
        task_name = "sbuild"
        task_data = textwrap.dedent(
            """
        build_components:
        - any
        - all
        environment: debian/match:codename=bookworm
        host_architecture: amd64
        input:
            source_artifact: 5
        """  # noqa: E501
        )

        form = WorkRequestForm(
            {
                "task_name": task_name,
                "task_data": task_data,
            },
            user=self.user,
            workspace=self.workspace,
        )
        self.assertTrue(form.is_valid())

        work_request = form.save(commit=False)

        self.assertEqual(work_request.workspace, self.workspace)
        self.assertEqual(work_request.created_by, self.user)
        self.assertEqual(work_request.task_name, task_name)
        self.assertEqual(work_request.task_data, yaml.safe_load(task_data))
        self.assertIsNone(work_request.id)

    def test_form_save_invalid_yaml(self) -> None:
        """Try to save the work request, YAML is invalid."""
        form = WorkRequestForm(
            {
                "task_data": "sbuild",
                "data_yaml": ":",
            },
            user=self.user,
            workspace=self.workspace,
        )

        with self.assertRaises(ValueError):
            # The WorkRequest could not be created: data_yaml is invalid
            form.save(commit=False)

    def test_clean_data_yaml_raise_validation_error(self) -> None:
        """Raise forms.ValidationError: invalid data."""
        form = WorkRequestForm(
            {"task_data": ":"}, user=self.user, workspace=self.workspace
        )

        self.assertFalse(form.is_valid())
        self.assertRegex(str(form.errors["task_data"][0]), "^Invalid YAML")

    def test_clean_task_name_must_be_valid(self) -> None:
        """Raise forms.ValidationError: invalid task name."""
        form = WorkRequestForm(
            {
                "task_name": "does-not-exist",
                "task_data": "",
            },
            user=self.user,
            workspace=self.workspace,
        )

        self.assertFalse(form.is_valid())
        self.assertRegex(
            str(form.errors["task_name"][0]),
            "does-not-exist is not one of the available choices.",
        )

    def test_clean_data_task_data_must_be_dict(self) -> None:
        """Raise forms.ValidationError: task data not a dict."""
        form = WorkRequestForm(
            {
                "task_name": "sbuild",
                "task_data": "[]",
            },
            user=self.user,
            workspace=self.workspace,
        )

        self.assertFalse(form.is_valid())
        self.assertRegex(
            str(form.errors["task_data"][0]),
            "task data must be a dictionary",
        )

    def test_clean_data_task_data_raise_validation_error(self) -> None:
        """Raise forms.ValidationError: invalid task data."""
        task_data_yaml = textwrap.dedent(
            """
        build_components:
        - any
        - all
        host_architecture: amd64
        input:
          source_artifact: 5
        """  # noqa: E501
        )

        form = WorkRequestForm(
            {
                "task_name": "sbuild",
                "task_data": task_data_yaml,
            },
            user=self.user,
            workspace=self.workspace,
        )

        self.assertFalse(form.is_valid())
        self.assertRegex(
            str(form.errors["task_data"][0]),
            r'environment\s+field required \(type=value_error\.missing\)',
        )

    def test_clean_data_yaml_return_dictionary(self) -> None:
        """clean_data_yaml return parsed YAML in a dictionary."""
        task_data = {
            "input": {
                "source_artifact": 1,
            },
            "host_architecture": "amd64",
            "environment": "debian/match:codename=bookworm",
            "build_components": ["all"],
        }
        form = WorkRequestForm(
            {
                "task_data": yaml.safe_dump(task_data),
                "task_name": "sbuild",
            },
            user=self.user,
            workspace=self.workspace,
        )

        form.is_valid()

        self.assertNotIn("task_data", form.errors)

        self.assertEqual(form.cleaned_data["task_data"], task_data)

    def test_clean_task_data_return_empty_dictionary(self) -> None:
        """clean_data_yaml return empty dictionary."""
        task_data = ""
        form = WorkRequestForm(
            {"task_name": "noop", "task_data": task_data},
            user=self.user,
            workspace=self.workspace,
        )

        form.is_valid()
        self.assertEqual(form.cleaned_data["task_data"], {})

    def test_task_name(self) -> None:
        """The field "task_name" is a choice field with the expected names."""
        form = WorkRequestForm(user=self.user, workspace=self.workspace)

        task_names = [
            (name, name)
            for name in sorted(BaseTask.worker_task_names())
            if "internal" not in name
        ]

        self.assertEqual(
            list(form.fields["task_name"].widget.choices), task_names
        )


class ArtifactFormTests(TestCase):
    """Tests for ArtifactForm."""

    user: ClassVar[User]
    workspace: ClassVar[Workspace]
    artifact_category: ClassVar[ArtifactCategory]
    form_data: ClassVar[dict[str, Any]]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize test objects."""
        super().setUpTestData()
        cls.workspace = cls.playground.get_default_workspace()
        cls.user = get_user_model().objects.create_user(
            username="testuser", password="testpass"
        )
        cls.artifact_category = ArtifactCategory.WORK_REQUEST_DEBUG_LOGS
        cls.form_data = {
            "category": cls.artifact_category,
        }

    def test_form_initialization(self) -> None:
        """Form initialization set form.user."""
        form = ArtifactForm(user=self.user, workspace=self.workspace)
        self.assertEqual(form.user, self.user)

    def test_form_save_set_created_by(self) -> None:
        """Form save(commit=False) set created_by."""
        form = ArtifactForm(
            data=self.form_data, user=self.user, workspace=self.workspace
        )
        self.addCleanup(form.cleanup)
        form.is_valid()
        instance = form.save(commit=False)

        self.assertEqual(instance.created_by, self.user)

    @context.disable_permission_checks()
    def test_form_save_commit(self) -> None:
        """Form save(commit=True) set created_by and saves into the DB."""
        category = self.artifact_category

        files_to_upload = {"file1.txt": b"contents1", "file2.txt": b"contents2"}

        simple_uploaded_files = []
        for name, contents in files_to_upload.items():
            simple_uploaded_files.append(SimpleUploadedFile(name, contents))

        form_data = {
            "data": {
                "category": category,
                "data": {},
                "expiration_delay_in_days": 1,
            },
            "files": MultiValueDict({"files": simple_uploaded_files}),
        }

        form = ArtifactForm(
            **form_data, user=self.user, workspace=self.workspace
        )
        self.addCleanup(form.cleanup)

        self.assertTrue(form.is_valid())

        artifact = form.save(commit=True)

        self.assertEqual(Artifact.objects.get(id=artifact.id), artifact)

        self.assertEqual(artifact.category, category)
        self.assertEqual(artifact.workspace, self.workspace)

        self.assertEqual(
            artifact.fileinartifact_set.count(), len(files_to_upload)
        )
        self.assertEqual(artifact.expiration_delay, timedelta(days=1))

        for file_in_artifact, file_uploaded in zip(
            artifact.fileinartifact_set.order_by("id"), files_to_upload
        ):
            self.assertEqual(file_in_artifact.path, file_uploaded)
            self.assertTrue(file_in_artifact.complete)
            fileobj = file_in_artifact.file
            file_backend = self.workspace.scope.upload_file_backend(fileobj)

            with file_backend.get_stream(fileobj) as file:
                self.assertEqual(file.read(), files_to_upload[file_uploaded])

    def test_form_save_no_commit(self) -> None:
        """Form save(commit=False): not saved to the DB."""
        files_to_upload = {"file1.txt": b"contents1", "file2.txt": b"contents2"}

        simple_uploaded_files = []
        for name, contents in files_to_upload.items():
            simple_uploaded_files.append(SimpleUploadedFile(name, contents))

        form_data = {
            "data": {
                "category": self.artifact_category,
                "data": {},
            },
            "files": MultiValueDict({"files": simple_uploaded_files}),
        }

        form = ArtifactForm(
            **form_data, user=self.user, workspace=self.workspace
        )
        self.addCleanup(form.cleanup)

        self.assertTrue(form.is_valid())

        artifact = form.save(commit=False)

        # Returned artifact is not saved
        self.assertIsNone(artifact.id)

        # Nothing is saved
        self.assertEqual(Artifact.objects.count(), 0)

    def test_form_expire_at_past_error(self) -> None:
        """Field expire_at is in the past: return an error."""
        form_data = {
            "data": {
                "category": self.artifact_category,
                "data": {},
                "expiration_delay_in_days": -1,
            },
            "files": {
                "files": [SimpleUploadedFile("file.txt", b"content.txt")]
            },
        }

        form = ArtifactForm(
            **form_data, user=self.user, workspace=self.workspace
        )
        self.addCleanup(form.cleanup)
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors,
            {
                "expiration_delay_in_days": [
                    "Ensure this value is greater than or equal to 0."
                ]
            },
        )

    def test_categories(self) -> None:
        """Check expected category choices."""
        form = ArtifactForm(user=self.user, workspace=self.workspace)
        choices = []
        for category in sorted(ArtifactCategory):
            choices.append((str(category), str(category)))

        assert isinstance(form.fields["category"], forms.ChoiceField)
        self.assertEqual(form.fields["category"].choices, choices)

    def test_file_zero_bytes(self) -> None:
        """The form is valid with a file of zero bytes."""
        category = ArtifactCategory.WORK_REQUEST_DEBUG_LOGS
        file_name = "build.txt"

        form_data = {
            "data": {
                "category": category,
                "data": {},
                "expire_at": None,
            },
            "files": {"files": [SimpleUploadedFile(file_name, b"")]},
        }

        form = ArtifactForm(
            **form_data, user=self.user, workspace=self.workspace
        )
        self.addCleanup(form.cleanup)
        self.assertTrue(form.is_valid())

    def test_files_invalid(self) -> None:
        """
        User tries to create an Artifact with invalid data.

        The data is YAML valid but the LocalArtifact does not validate.
        LocalArtifact checks the data with the contents of the files.
        """
        category = ArtifactCategory.PACKAGE_BUILD_LOG
        data = {
            "source": "test-source",
            "version": "1.2.3",
            "filename": "test",
        }
        file_name = "build.txt"

        # Try to create a PackageBuildLog. The file should end in .build
        # but it ends in .txt: invalid.
        form_data = {
            "data": {
                "category": category,
                "data": data,
                "expire_at": None,
            },
            "files": {"files": [SimpleUploadedFile(file_name, b"content.txt")]},
        }

        form = ArtifactForm(
            **form_data, user=self.user, workspace=self.workspace
        )
        self.addCleanup(form.cleanup)
        self.assertFalse(form.is_valid())

        with self.assertRaises(pydantic.ValidationError) as raised:
            PackageBuildLog(
                category=category,
                data=DebianPackageBuildLog.parse_obj(data),
                files={file_name: Path("not-used.txt")},
            )

        self.assertEqual(
            form.errors, {"files": [str(raised.exception.args[0][0].exc)]}
        )

    def test_data_invalid(self) -> None:
        """
        User tries to create an Artifact with invalid data.

        The data is YAML valid but the LocalArtifact does not validate.
        LocalArtifact checks the data with the contents of the files.
        """
        category = ArtifactCategory.PACKAGE_BUILD_LOG
        data = {
            "source": "test-source",
            "version": "1.2.3",
            "filename": "test",
        }
        file_name = "build.txt"

        # Try to create a PackageBuildLog. The file should end in .build
        # but it ends in .txt: invalid.
        form_data = {
            "data": {
                "category": category,
                "data": data,
                "expire_at": None,
            },
            "files": {"files": [SimpleUploadedFile(file_name, b"content.txt")]},
        }

        form = ArtifactForm(
            **form_data, user=self.user, workspace=self.workspace
        )
        self.addCleanup(form.cleanup)
        self.assertFalse(form.is_valid())

        with self.assertRaises(pydantic.ValidationError) as raised:
            PackageBuildLog(
                category=category,
                data=DebianPackageBuildLog.parse_obj(data),
                files={file_name: Path("not-used.txt")},
            )

        self.assertEqual(
            form.errors, {"files": [str(raised.exception.args[0][0].exc)]}
        )


class WorkspaceChoiceFieldTests(TestCase):
    """Tests for WorkspaceChoiceField."""

    user: ClassVar[User]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize test objects."""
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(
            username="testuser", password="testpass"
        )

    def test_label(self) -> None:
        """The label is the workspace name."""
        workspace_choice_field = WorkspaceChoiceField(user=self.user)

        labels = [
            label for value, label in workspace_choice_field.widget.choices
        ]

        self.assertEqual(
            labels,
            [
                workspace_choice_field.empty_label,
                Workspace.objects.earliest("id").name,
            ],
        )

    def test_order_by(self) -> None:
        """The field's queryset is ordered by name."""
        workspace_choice_field = WorkspaceChoiceField(user=None)

        assert workspace_choice_field.queryset is not None
        self.assertEqual(
            workspace_choice_field.queryset.query.order_by, ("name",)
        )

    def test_choices_no_user(self) -> None:
        """Choices for if no user: only the public workspaces."""
        default_workspace = Workspace.objects.earliest("id")
        default_workspace.public = False
        default_workspace.save()

        with context.disable_permission_checks():
            workspace = self.playground.create_workspace(
                name="It is public", public=True
            )

        workspace_choice_field = WorkspaceChoiceField(user=None)

        # Blank choice and public workspace
        self.assertEqual(
            list(workspace_choice_field.choices),
            [
                ("", workspace_choice_field.empty_label),
                (workspace.id, workspace.name),
            ],
        )

    def test_choices_with_user(self) -> None:
        """Private workspace included in the choices: user has access."""
        workspace = Workspace.objects.earliest("id")
        workspace.public = False
        workspace.save()

        workspace_choice_field = WorkspaceChoiceField(user=self.user)

        self.assertEqual(
            list(workspace_choice_field.choices),
            [
                ("", workspace_choice_field.empty_label),
                (workspace.pk, workspace.name),
            ],
        )


class WorkflowFilterFormTests(TestCase):
    """Tests for WorkflowFilterForm."""

    scenario = scenarios.DefaultContext(set_current=True)

    def setUp(self) -> None:
        """Set up tests."""
        super().setUp()

        self.form = WorkflowFilterForm()

    def test_workflow_templates_field(self) -> None:
        """Test workflow_templates field."""
        self.playground.create_workflow_template(
            "name-1", "noop", workspace=self.scenario.workspace
        )

        self.playground.create_workflow_template(
            "name-2",
            "sbuild",
            workspace=self.playground.create_workspace(
                name="unused", public=False
            ),
        )

        self.form = WorkflowFilterForm()

        self.assertEqual(
            self.form.fields["workflow_templates"].widget.choices,
            [
                ("name-1", "name-1"),
            ],
        )

    def test_statuses_field(self) -> None:
        """Test statuses field."""
        self.assertEqual(
            self.form.fields["statuses"].widget.choices,
            [
                ('pending', 'Pending'),
                (
                    'Running',
                    [
                        ('running__needs_input', 'Needs Input'),
                        ('running__running', 'Running'),
                        ('running__waiting', 'Waiting'),
                        ('running__pending', 'Pending'),
                        ('running__aborted', 'Aborted'),
                        ('running__completed', 'Completed'),
                        ('running__blocked', 'Blocked'),
                        ('running__any', 'Any'),
                    ],
                ),
                ('completed', 'Completed'),
                ('aborted', 'Aborted'),
                ('blocked', 'Blocked'),
            ],
        )

    def test_runtime_statuses_field(self) -> None:
        """Test runtime_statuses field."""
        self.assertEqual(
            self.form.fields["runtime_statuses"].widget.choices,
            [
                ("needs_input", "Needs Input"),
                ("running", "Running"),
                ("waiting", "Waiting"),
                ("pending", "Pending"),
                ("aborted", "Aborted"),
                ("completed", "Completed"),
                ("blocked", "Blocked"),
            ],
        )

    def test_results_field(self) -> None:
        """Test results field."""
        self.assertEqual(
            self.form.fields["results"].widget.choices,
            [
                ("error", "Error"),
                ("failure", "Failure"),
                ("success", "Success"),
            ],
        )

    def test_started_by_field(self) -> None:
        """Test started_by field."""
        # "other-1" created a workflow in another workspace:
        # not listed by started_by field
        other_1 = self.playground.create_user(username="other-1")
        workflow_1 = self.playground.create_workflow(created_by=other_1)
        workflow_1.workspace = self.playground.create_workspace(
            name="new-workspace"
        )
        workflow_1.save()

        # "other-2" has not created any workflow (only a work request):
        # not listed by started_by field
        other_2 = self.playground.create_user(username="other-2")
        self.playground.create_work_request(created_by=other_2)

        # Creates a workflow with self.playground.get_default_user()
        self.playground.create_workflow()

        self.form = WorkflowFilterForm()

        self.assertEqual(
            self.form.fields["started_by"].widget.choices,
            [
                (
                    self.scenario.user.username,
                    self.scenario.user.username,
                )
            ],
        )

    def test_with_failed_work_requests_failed_field(self) -> None:
        """Test with_failed_work_requests field."""
        self.assertIn("with_failed_work_requests", self.form.fields)


class DaysFieldTest(TestCase):
    """Tests for DaysField."""

    def test_prepare_value(self) -> None:
        """Test DaysField.prepare_value."""
        field = DaysField()
        for orig, prepared in [
            ("0", "0"),
            (1, 1),
            (timedelta(days=4), 4),
            (None, None),
        ]:
            with self.subTest(orig=orig):
                self.assertEqual(field.prepare_value(orig), prepared)

    def test_to_python(self) -> None:
        """Test DaysField.to_python."""
        field = DaysField()
        for orig, py in [
            ("0", timedelta(days=0)),
            (1, timedelta(days=1)),
            ("", None),
            (None, None),
        ]:
            with self.subTest(orig=orig):
                self.assertEqual(field.to_python(orig), py)


class WorkspaceFormTests(TestCase):
    """Tests for WorkspaceForm."""

    scenario = scenarios.DefaultContext(set_current=True)

    def test_values_from_instances(self) -> None:
        """Test form field values."""
        form = WorkspaceForm(instance=self.scenario.workspace)
        self.assertTrue(form["public"].initial)
        self.assertIsNone(form["expiration_delay"].initial)
        self.assertEqual(
            form["default_expiration_delay"].initial, timedelta(days=0)
        )

    def test_values_from_data(self) -> None:
        """Test setting form field values."""
        ws = self.scenario.workspace
        form = WorkspaceForm(
            data={
                "public": "false",
                "expiration_delay": "7",
                "default_expiration_delay": "14",
            },
            instance=ws,
        )
        self.assertTrue(form.is_valid())
        form.save()

        ws.refresh_from_db()
        self.assertFalse(ws.public)
        self.assertEqual(ws.expiration_delay, timedelta(days=7))
        self.assertEqual(ws.default_expiration_delay, timedelta(days=14))


class GroupAddUserFormTests(TestCase):
    """Tests for GroupAddUserForm."""

    scenario = scenarios.DefaultContext(set_current=True)

    group: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.group = cls.playground.create_group("test")

    def _form(self, username: Any) -> GroupAddUserForm:
        """Instantiate the form."""
        return GroupAddUserForm(group=self.group, data={"username": username})

    def test_username_required(self) -> None:
        """A valid username passes validation."""
        form = self._form(None)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors, {"username": ["This field is required."]})

    def test_valid_username(self) -> None:
        """A valid username passes validation."""
        form = self._form(self.scenario.user.username)
        self.assertTrue(form.is_valid())
        self.assertEqual(
            form.cleaned_data["username"], self.scenario.user.username
        )

    def test_invalid_username(self) -> None:
        """Error on invalid usernames."""
        form = self._form("does-not-exist")
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors, {"username": ["User does-not-exist does not exist"]}
        )

    def test_user_already_member(self) -> None:
        """Existing members are not accepted."""
        self.group.add_user(self.scenario.user)
        form = self._form(self.scenario.user.username)
        self.assertFalse(form.is_valid())
        self.assertEqual(list(form.errors.keys()), ["username"])
        self.assertEqual(
            form.errors,
            {"username": ["User is already a member of debusine/test"]},
        )
