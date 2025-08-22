# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine Web forms."""
import functools
import shutil
import tempfile
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, Generic, TYPE_CHECKING, TypeVar, cast

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.forms import (
    BaseForm,
    BooleanField,
    CharField,
    ChoiceField,
    ClearableFileInput,
    DateTimeField,
    Field,
    FileField,
    Form,
    IntegerField,
    ModelChoiceField,
    ModelForm,
    MultipleChoiceField,
    Textarea,
)

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic  # type: ignore

import yaml

# from debusine.client import LocalArtifact
from debusine.artifacts import LocalArtifact
from debusine.artifacts.models import BareDataCategory, TaskTypes
from debusine.db.models import (
    Artifact,
    Collection,
    CollectionItem,
    File,
    FileInArtifact,
    Group,
    Token,
    User,
    WorkRequest,
    Workspace,
)
from debusine.tasks import BaseTask

if TYPE_CHECKING:
    ModelFormBase = ModelForm
    ModelChoiceFieldBase = ModelChoiceField
else:
    # Django's ModelForm and ModelChoiceField don't support generic types at
    # run-time yet.
    class _ModelFormBase:
        def __class_getitem__(*args):
            return ModelForm

    class _ModelChoiceFieldBase:
        def __class_getitem__(*args):
            return ModelChoiceField

    ModelFormBase = _ModelFormBase
    ModelChoiceFieldBase = _ModelChoiceFieldBase


_KT = TypeVar("_KT")
_VT = TypeVar("_VT")


class DictWithCallback(dict[_KT, _VT], Generic[_KT, _VT]):
    r"""
    Dictionary that when setting a value it calls a method to process it.

    Usage: d = DictWithCallback(callback, \*dict_args, \*\*dict_kwargs)

    when doing:
    d["key"] = value

    Before setting the value it calls "callback" which can change, in place,
    the value. Then sets the value to the dictionary.

    When accessing the value (d["key"]) it return the value as it was modified
    by the callback.
    """

    def __init__(
        self, callback: Callable[[_VT], None], *args: Any, **kwargs: Any
    ) -> None:
        """Create the object."""
        self._callback = callback
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: _KT, value: _VT) -> None:
        """Call self._callback(value) and set the value."""
        self._callback(value)
        super().__setitem__(key, value)


class BootstrapMixin:
    """
    Mixin that adjusts the CSS classes of form fields with Bootstrap's UI.

    This mixin is intended to be used in combination with Django's form classes.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the mixin."""
        super().__init__(*args, **kwargs)

        self._adjust_bootstrap_classes_all_fields()

        self.fields: dict[str, Field] = DictWithCallback(
            self._adjust_bootstrap_for_field, self.fields
        )

    @staticmethod
    def _adjust_bootstrap_for_field(field: Field) -> None:
        """Adjust the CSS class for a field."""
        existing_class = field.widget.attrs.get("class", "")
        bootstrap_class = None

        if field.required:
            suffix = " *"
            if not (field.label_suffix or "").endswith(suffix):
                field.label_suffix = (field.label_suffix or "") + suffix

        if isinstance(field, ChoiceField):
            bootstrap_class = "form-select"
        elif isinstance(
            field, (CharField, DateTimeField, DaysField, FileField)
        ):
            bootstrap_class = "form-control"
        elif isinstance(field, BooleanField):
            bootstrap_class = "form-check-input"

        if bootstrap_class and bootstrap_class not in existing_class.split():
            field.widget.attrs["class"] = (
                f"{existing_class} {bootstrap_class}".strip()
            )

    def _adjust_bootstrap_classes_all_fields(self) -> None:
        """Adjust the CSS classes of form fields to be Bootstrap-compatible."""
        for field in self.fields.values():
            self._adjust_bootstrap_for_field(field)


class TokenForm(BootstrapMixin, ModelFormBase[Token]):
    """Form for creating or editing a token."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize TokenForm."""
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

        if not self.instance.pk:
            # New instance (not loaded from the DB). Set defaults
            self.fields["enabled"].initial = True

    def save(self, commit: bool = True) -> Token:
        """Save TokenForm."""
        instance = super().save(commit=False)

        instance.user = self.user

        if commit:
            instance.save()

        return instance

    class Meta:
        model = Token
        fields = ["comment", "enabled"]

        labels = {
            "comment": "Comment",
        }


class WorkspaceChoiceField(ModelChoiceFieldBase[Workspace]):
    """ChoiceField for the workspaces: set the label and order by name."""

    def __init__(
        self, user: User | AnonymousUser | None, *args: Any, **kwargs: Any
    ) -> None:
        """Set the queryset."""
        kwargs["queryset"] = Workspace.objects.order_by("name")

        if user is None:
            # Non-authenticated users can list only public workspaces
            kwargs["queryset"] = kwargs["queryset"].filter(public=True)

        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj: Workspace) -> str:
        """Return name of the workspace."""
        return obj.name


class YamlMixin:
    """
    Mixin that handles fields and validate/convert from YAML to a dict.

    Usage:
    In the class inheriting from YamlMixin:

    yaml_fields = ["task_data", "some_other_field"]
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """For each self.yaml_fields: create the CharField."""
        super().__init__(*args, **kwargs)

        assert isinstance(self, BaseForm)
        assert hasattr(self, "yaml_fields")
        for name in self.yaml_fields:
            self.fields[name] = CharField(
                widget=Textarea,
                required=False,
                # This might need to change if it's possible to edit
                # a form loading data from the database.
                initial="",
            )

            self.__setattr__(
                f"clean_{name}", functools.partial(self._clean_data_yaml, name)
            )

    def _clean_data_yaml(self, field_name: str) -> Any:
        """Return object representing the YAML input."""
        data_yaml = cast(BaseForm, self).cleaned_data[field_name]

        try:
            task_data = yaml.safe_load(data_yaml)
        except yaml.YAMLError as exc:
            raise ValidationError(f"Invalid YAML: {exc}")

        return {} if task_data is None else task_data


class WorkRequestForm(YamlMixin, BootstrapMixin, ModelFormBase[WorkRequest]):
    """Form for creating a Work Request."""

    yaml_fields = ["task_data"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize WorkRequestForm."""
        self.user = kwargs.pop("user")
        self.workspace = kwargs.pop("workspace")
        super().__init__(*args, **kwargs)

        tasks = BaseTask.task_names(TaskTypes.WORKER)
        self.fields["task_name"] = ChoiceField(
            choices=lambda: [(name, name) for name in sorted(tasks)]
        )

    def save(self, commit: bool = True) -> WorkRequest:
        """Save the work request."""
        instance = super().save(commit=False)
        instance.created_by = self.user
        instance.workspace = self.workspace
        if commit:
            instance.save()
        return instance

    class Meta:
        model = WorkRequest
        fields = ["task_name", "task_data"]


class WorkRequestUnblockForm(BootstrapMixin, Form):
    """Form for reviewing a work request awaiting manual approval."""

    # Django defaults to 10 rows, which is a bit much.  Just make it clear
    # that we accept multi-line input.
    notes = CharField(
        required=False, empty_value=None, widget=Textarea(attrs={"rows": 3})
    )


class MultipleFileInput(ClearableFileInput):
    """ClearableFileInput allowing to select multiple files."""

    allow_multiple_selected = True


class MultipleFileField(FileField):
    """
    FileField using the widget MultipleFileInput.

    Implementation as suggested by Django documentation:
    https://docs.djangoproject.com/en/4.2/topics/http/file-uploads/#uploading-multiple-files
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize object: use MultipleFileInput() as a widget."""
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs, allow_empty_file=True)

    def clean(
        self, data: list[Any] | tuple[Any, ...], initial: Any = None
    ) -> list[Any]:
        """Call super().clean() for each file."""  # noqa: D402
        single_file_clean = super().clean
        return [single_file_clean(file, initial) for file in data]


class ArtifactForm(BootstrapMixin, YamlMixin, ModelFormBase[Artifact]):
    """Form for creating artifacts."""

    # Deliberately incompatible with BaseForm.files.
    files = MultipleFileField()  # type: ignore[assignment]
    category = ChoiceField()
    expiration_delay_in_days = IntegerField(
        min_value=0, initial=None, required=False
    )

    yaml_fields = ["data"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize object."""
        self.user = kwargs.pop("user")
        self.workspace = kwargs.pop("workspace")

        super().__init__(*args, **kwargs)

        assert isinstance(self.fields["category"], ChoiceField)
        self.fields["category"].choices = [
            (artifact_category, artifact_category)
            for artifact_category in sorted(LocalArtifact.artifact_categories())
        ]

        # Populated on clean(), used on save()
        self._local_files: dict[str, Path] = {}
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = (
            None
        )

    @transaction.atomic
    def save(self, commit: bool = True) -> Artifact:
        """Create the artifact."""
        instance = super().save(commit=False)
        instance.created_by = self.user
        instance.workspace = self.workspace
        if self.cleaned_data["expiration_delay_in_days"] is not None:
            instance.expiration_delay = timedelta(
                days=self.cleaned_data["expiration_delay_in_days"]
            )

        if commit:
            instance.save()

            for file in self.cleaned_data["files"]:
                local_file_path = self._local_files[file.name]
                # Add file to the store
                file_obj = File.from_local_path(local_file_path)

                file_backend = instance.workspace.scope.upload_file_backend(
                    file_obj
                )
                file_backend.add_file(local_file_path, fileobj=file_obj)

                # Add file to the artifact
                FileInArtifact.objects.create(
                    artifact=instance,
                    path=file.name,
                    file=file_obj,
                    complete=True,
                )
                instance.files.add(file_obj)

        return instance

    def clean(self) -> dict[str, Any]:
        """
        Create a LocalArtifact model and validate it.

        :raise ValidationError: if the LocalArtifact model is not valid.
        """
        cleaned_data = super().clean()
        assert cleaned_data is not None

        artifact_category = cleaned_data["category"]

        SubLocalArtifact = LocalArtifact.class_from_category(artifact_category)

        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="debusine-form-artifact"
        )
        self._local_files = {}
        for file in cleaned_data["files"]:
            file_path = Path(self._temporary_directory.name) / file.name

            with file_path.open("wb") as local_file:
                shutil.copyfileobj(file.file, local_file)
            file.file.close()

            self._local_files[file.name] = file_path

        # If adding any new fields in this LocalArtifact fields,
        # make sure that the form has a field with the same name.
        # If not, adjust the code handling the ValidationError.
        sub_local_artifact_kwargs = {
            "category": artifact_category,
            "data": cleaned_data.get("data"),
            "files": self._local_files,
        }

        try:
            SubLocalArtifact(**sub_local_artifact_kwargs)
        except pydantic.ValidationError as exc:
            for error in exc.errors():
                field_name = error["loc"][0]
                # This assumes that the fields that can raise ValidationErrors
                # in the LocalArtifact have a field in the form with the same
                # name. If some day there are fields with different names
                # need to add a mapping or add errors via
                # self.add_error(None, ...) which adds the errors on the
                # top of the form.
                self.add_error(str(field_name), error["msg"])

        return cleaned_data

    def cleanup(self) -> None:
        """Clean up resources."""
        if self._temporary_directory is not None:  # pragma: no cover
            self._temporary_directory.cleanup()
            self._temporary_directory = None

    class Meta:
        model = Artifact
        fields = ["category", "files", "data"]


class CollectionSearchForm(BootstrapMixin, Form):
    """Form for collection search fields."""

    category = ChoiceField(required=False)
    name = CharField(required=False)
    historical = BooleanField(required=False)

    def __init__(self, *args: Any, instance: Collection, **kwargs: Any) -> None:
        """Initialize category choices from the database."""
        super().__init__(*args, **kwargs)
        self.instance = instance
        choices = [("", "All")]
        choices.extend(
            (name, name)
            for name in CollectionItem.objects.filter(
                parent_collection=self.instance
            )
            .values_list("category", flat=True)
            .distinct()
            .order_by("category")
        )
        cast(ChoiceField, self.fields["category"]).choices = choices


class WorkflowFilterForm(Form):
    """Form for filtering workflows."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize form."""
        super().__init__(*args, **kwargs)

        statuses: list[tuple[str, str | list[tuple[str, str]]]] = []
        for status_value, status_label in WorkRequest.Statuses.choices:
            if status_value == WorkRequest.Statuses.RUNNING:
                running_options: list[tuple[str, str]] = []
                for (
                    runtime_value,
                    runtime_label,
                ) in WorkRequest.RuntimeStatuses.choices:
                    running_options.append(
                        (f"running__{runtime_value}", runtime_label)
                    )

                # Add "Any" for any Running status.
                running_options.append(("running__any", "Any"))
                statuses.append(("Running", running_options))
            else:
                statuses.append((status_value, status_label))

        self.fields["statuses"] = MultipleChoiceField(
            choices=statuses,
            required=False,
        )

        runtime_statuses_choices = [
            (status.value, status.label)
            for status in WorkRequest.RuntimeStatuses
        ]

        self.fields["runtime_statuses"] = MultipleChoiceField(
            choices=runtime_statuses_choices,
            required=False,
        )

        self.fields["results"] = MultipleChoiceField(
            choices=sorted(
                choice for choice in WorkRequest.Results.choices if choice[0]
            ),
            required=False,
        )

        self.fields["with_failed_work_requests"] = BooleanField(
            required=False,
        )


class DaysField(IntegerField):
    """Edit a timedelta as a number of days."""

    def prepare_value(self, value: Any) -> Any:
        """Convert a timedelta to days."""
        if isinstance(value, timedelta):
            return super().prepare_value(value.days)
        return super().prepare_value(value)

    def to_python(self, value: Any) -> Any:
        """Convert days to timedelta."""
        res = super().to_python(value)
        if res is not None:
            return timedelta(days=res)
        return res


class WorkspaceForm(BootstrapMixin, ModelFormBase[Workspace]):
    """Form for configuring a workspace."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Set help texts for the fields."""
        super().__init__(*args, **kwargs)

        self.fields["expiration_delay"].help_text = (
            "If unset, workspace is permanent. Otherwise, the workspace gets "
            "removed after the given period of inactivity (expressed as a "
            "number of days after the completion of the last work request)"
        )
        self.fields["default_expiration_delay"].help_text = (
            "Number of days that new artifacts and work requests are kept "
            "in the workspace before being expired (0 means no expiration)"
        )

    class Meta:
        model = Workspace
        fields = ["public", "expiration_delay", "default_expiration_delay"]
        field_classes = {
            "expiration_delay": DaysField,
            "default_expiration_delay": DaysField,
        }


class GroupAddUserForm(BootstrapMixin, Form):
    """Form for adding a user to a group."""

    username = CharField(required=True)

    def __init__(self, group: Group, *args: Any, **kwargs: Any) -> None:
        """Set the group the user would be added to."""
        self.group = group
        super().__init__(*args, **kwargs)

    def clean(self) -> dict[str, Any]:
        """
        Validate username.

        The user needs to exist and not be already a member of the group.
        """
        cleaned_data = super().clean()
        assert cleaned_data is not None

        if "username" not in cleaned_data:
            return cleaned_data

        try:
            user = User.objects.get(username=cleaned_data["username"])
        except User.DoesNotExist:
            self.add_error(
                "username", f"User {cleaned_data['username']} does not exist"
            )
            return cleaned_data

        if self.group.users.filter(pk=user.pk).exists():
            self.add_error(
                "username", f"User is already a member of {self.group}"
            )

        return cleaned_data


class TaskConfigurationInspectorForm(BootstrapMixin, Form):
    """Form for looking up task configurations."""

    task = ChoiceField()
    subject = ChoiceField(required=False)
    context = ChoiceField(required=False)

    def __init__(
        self, *args: Any, collection: Collection, **kwargs: Any
    ) -> None:
        """Initialize choices from the database."""
        super().__init__(*args, **kwargs)
        children = collection.child_items.filter(
            Q(data__template=None) | ~Q(data__has_key="template"),
            category=BareDataCategory.TASK_CONFIGURATION,
        )

        task_choices = []
        for ttype, tname in (
            children.order_by("data__task_type", "data__task_name")
            .values_list("data__task_type", "data__task_name")
            .distinct()
        ):
            task_choices.append((f"{ttype}:{tname}", f"{ttype}:{tname}"))
        cast(ChoiceField, self.fields["task"]).choices = task_choices

        subject_choices = [("", "(unspecified)")]
        for subject in (
            children.order_by("data__subject")
            .values_list("data__subject", flat=True)
            .distinct()
        ):
            if subject is not None:
                subject_choices.append((subject, subject))
        cast(ChoiceField, self.fields["subject"]).choices = subject_choices

        context_choices = [("", "(unspecified)")]
        for context in (
            children.order_by("data__context")
            .values_list("data__context", flat=True)
            .distinct()
        ):
            if context is not None:
                context_choices.append((context, context))
        cast(ChoiceField, self.fields["context"]).choices = context_choices
