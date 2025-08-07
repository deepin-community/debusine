# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Infrastructure to create test scenarios in the database."""

import tempfile
import textwrap
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from secrets import token_hex
from typing import Any, Literal, TYPE_CHECKING, assert_never, overload

from django.conf import settings
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db.models import F, Model
from django.utils import timezone

from debusine.artifacts import LocalArtifact
from debusine.artifacts.local_artifact import BinaryPackage, SourcePackage
from debusine.artifacts.models import (
    ArtifactCategory,
    ArtifactData,
    BareDataCategory,
    BaseArtifactDataModel,
    CollectionCategory,
    DebianPackageBuildLog,
    DebusineSigningInput,
    DebusineSigningOutput,
    SigningResult,
)
from debusine.artifacts.playground import ArtifactPlayground
from debusine.assets import (
    AWSProviderAccountConfiguration,
    AWSProviderAccountCredentials,
    AWSProviderAccountData,
    AssetCategory,
    BaseAssetDataModel,
    CloudProviderAccountData,
    CloudProvidersType,
    DummyProviderAccountData,
    HetznerProviderAccountConfiguration,
    HetznerProviderAccountCredentials,
    HetznerProviderAccountData,
    KeyPurpose,
    SigningKeyData,
)
from debusine.client.models import model_to_json_serializable_dict
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Asset,
    AssetUsage,
    Collection,
    CollectionItem,
    DEFAULT_FILE_STORE_NAME,
    File,
    FileInArtifact,
    FileStore,
    FileUpload,
    Group,
    Scope,
    Token,
    User,
    WorkRequest,
    Worker,
    WorkerPool,
    WorkflowTemplate,
    Workspace,
)
from debusine.db.models.permissions import get_resource_scope, resolve_role
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.server.collections.lookup import lookup_single
from debusine.server.file_backend.interface import FileBackendInterface
from debusine.server.worker_pools import (
    AWSEC2WorkerPoolSpecification,
    DummyWorkerPoolSpecification,
    HetznerCloudWorkerPoolSpecification,
    WorkerPoolLimits,
    WorkerPoolSpecifications,
)
from debusine.server.workflows.models import (
    BaseWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.tasks.models import (
    BackendType,
    BaseTaskData,
    SbuildBuildComponent,
    SbuildData,
    SbuildInput,
    WorkerType,
)

if TYPE_CHECKING:
    from debusine.db.playground.scenarios import Scenario


@dataclass(kw_only=True)
class UploadArtifacts:
    """The return value for Playground.create_upload_artifacts()."""

    upload: Artifact
    source: Artifact | None
    binaries: list[Artifact] | None


@dataclass(kw_only=True)
class SourceUploadArtifacts(UploadArtifacts):
    """UploadArtifacts for a source-only upload."""

    source: Artifact
    binaries: None


@dataclass(kw_only=True)
class BinaryUploadArtifacts(UploadArtifacts):
    """UploadArtifacts for a binary-only upload."""

    source: None
    binaries: list[Artifact]


@dataclass(kw_only=True)
class MixedUploadArtifacts(UploadArtifacts):
    """UploadArtifacts for a mixed upload."""

    source: Artifact
    binaries: list[Artifact]


class Playground(ArtifactPlayground):
    """Generate data scenarios for the database."""

    def __init__(
        self,
        default_file_store_name: str = DEFAULT_FILE_STORE_NAME,
        default_workspace_name: str = settings.DEBUSINE_DEFAULT_WORKSPACE,
        default_username: str = "playground",
        default_user_password: str | None = None,
        default_user_email: str = "playground@example.org",
        default_user_first_name: str = "Play",
        default_user_last_name: str = "Ground",
    ):
        """Set default values."""
        super().__init__()
        self.default_file_store_name = default_file_store_name
        self.default_workspace_name = default_workspace_name
        self.default_username = default_username
        self.default_user_password = default_user_password
        self.default_user_email = default_user_email
        self.default_user_first_name = default_user_first_name
        self.default_user_last_name = default_user_last_name
        # Registry of scenarios created by this playground.
        #
        # This can be used when instantiating multiple scenarios, allowing one
        # to access previously built ones
        self.scenarios: dict[str, "Scenario"] = {}

    @context.disable_permission_checks()
    def get_default_user(self) -> User:
        """Return the default user for playground methods."""
        try:
            return User.objects.get(username=self.default_username)
        except User.DoesNotExist:
            user = User.objects.create_user(
                username=self.default_username,
                email=self.default_user_email,
                first_name=self.default_user_first_name,
                last_name=self.default_user_last_name,
            )
            if self.default_user_password is not None:
                user.set_password(self.default_user_password)
                user.save()
        return user

    @context.disable_permission_checks()
    def get_default_file_store(self) -> FileStore:
        """Get the default file store used."""
        try:
            return FileStore.objects.get(name=self.default_file_store_name)
        except FileStore.DoesNotExist:
            return FileStore.objects.create(
                name=self.default_file_store_name,
                backend=FileStore.BackendChoices.LOCAL,
                configuration={},
            )

    def get_default_scope(self) -> Scope:
        """Get the default workspace used by create methods."""
        # TODO: when tests pass with scoped workspaces, switch to a differently
        # named default scope for tests, to catch code possibly depending on
        # the fallback scope introduced by the migration
        # return self.get_or_create_scope(name="tests")
        return self.get_or_create_scope(name=settings.DEBUSINE_DEFAULT_SCOPE)

    @context.disable_permission_checks()
    def get_default_workspace(self) -> Workspace:
        """Get the default workspace used by create methods."""
        return self.create_workspace(public=True)

    @context.disable_permission_checks()
    def add_user_permission(
        self, user: User, model: type[Model], codename: str
    ) -> None:
        """Add a permission to a user."""
        user.user_permissions.add(
            Permission.objects.get(
                codename=codename,
                content_type=ContentType.objects.get_for_model(model),
            )
        )

    @context.disable_permission_checks()
    def get_or_create_scope(self, name: str, **kwargs: Any) -> Scope:
        """Create a scope, or return an existing scope."""
        kwargs.setdefault("label", name.capitalize())
        scope, created = Scope.objects.get_or_create(name=name, defaults=kwargs)
        if created:
            scope.file_stores.add(self.get_default_file_store())
        return scope

    @context.disable_permission_checks()
    def create_user(
        self,
        username: str,
    ) -> User:
        """Create a user."""
        email = f"{username}@example.org"
        user = User.objects.create_user(
            username=username,
            email=email,
        )
        return user

    @context.disable_permission_checks()
    def create_group(
        self,
        name: str,
        users: list[User] | None = None,
        scope: Scope | None = None,
        ephemeral: bool = False,
    ) -> Group:
        """Create a group, setting membership to users."""
        if scope is None:
            scope = self.get_default_scope()
        if users is None:
            users = []
        group, created = Group.objects.get_or_create(
            scope=scope, name=name, ephemeral=ephemeral
        )
        group.users.set(users)
        return group

    @context.disable_permission_checks()
    def create_group_role(
        self,
        resource: Model,
        role: str,
        users: list[User] | None = None,
        name: str | None = None,
    ) -> Group:
        """Create a group with the given role on a resource."""
        if name is None:
            name = f"{str(resource).replace('/', '-')}-{role}"
        if users is None:
            users = []

        role = resolve_role(resource, role)
        group_scope = get_resource_scope(resource)
        group = self.create_group(name, users, scope=group_scope)
        roles_model = getattr(resource.__class__, "objects").get_roles_model()
        roles_model.objects.filter(group=group).delete()
        group.assign_role(resource, role)
        return group

    @context.disable_permission_checks()
    def add_user(
        self, group: Group, user: User, role: Group.Roles = Group.Roles.MEMBER
    ) -> None:
        """Add a user to a group."""
        group.add_user(user, role)

    @context.disable_permission_checks()
    def create_bare_token(self, enabled: bool = True, **kwargs: Any) -> Token:
        """
        Return an enabled Token.

        :param with_user: if True it assigns a User to this token.
        """
        token = Token.objects.create(**kwargs)
        if enabled:
            token.enable()
        else:
            token.disable()
        return token

    @context.disable_permission_checks()
    def create_worker_token(
        self, worker: Worker | None = None, enabled: bool = True, **kwargs: Any
    ) -> Token:
        """
        Return a worker token.

        Optionally create the worker if none is supplied.
        """
        token = Token.objects.create(**kwargs)

        if worker is None:
            worker = self.create_worker()
        worker.token = token
        worker.save()

        if enabled:
            token.enable()
        else:
            token.disable()
        return token

    @context.disable_permission_checks()
    def create_worker_activation_token(
        self, worker: Worker | None = None, enabled: bool = True, **kwargs: Any
    ) -> Token:
        """
        Return a worker activation token.

        Optionally create the worker if none is supplied.
        """
        token = Token.objects.create(**kwargs)

        if worker is None:
            worker = self.create_worker()
        worker.activation_token = token
        worker.save()

        if enabled:
            token.enable()
        else:
            token.disable()
        return token

    @context.disable_permission_checks()
    def create_user_token(
        self, user: User | None = None, enabled: bool = True, **kwargs: Any
    ) -> Token:
        """
        Return a user token.

        :param user: user to associate, defaulting to get_default_user()
        """
        if user is None:
            user = self.get_default_user()
        token = Token.objects.create(**kwargs)
        token.user = user
        token.save()
        if enabled:
            token.enable()
        else:
            token.disable()
        return token

    @context.disable_permission_checks()
    def create_workspace(
        self, *, scope: Scope | None = None, **kwargs: Any
    ) -> Workspace:
        """Create a Workspace and return it."""
        if scope is None:
            scope = self.get_default_scope()
        workspace_name = kwargs.pop("name", self.default_workspace_name)
        workspace, _ = Workspace.objects.select_related("scope").get_or_create(
            scope=scope, name=workspace_name, defaults=kwargs
        )

        return workspace

    @context.disable_permission_checks()
    def create_collection(
        self,
        name: str,
        category: CollectionCategory,
        *,
        workspace: Workspace | None = None,
        data: dict[str, Any] | None = None,
    ) -> Collection:
        """Create a collection."""
        return Collection.objects.create(
            name=name,
            category=category,
            workspace=workspace or self.get_default_workspace(),
            data=data or {},
        )

    @context.disable_permission_checks()
    def create_singleton_collection(
        self,
        category: CollectionCategory,
        *,
        workspace: Workspace | None = None,
        data: dict[str, Any] | None = None,
    ) -> Collection:
        """Create a singleton collection."""
        ret, _ = Collection.objects.get_or_create_singleton(
            category=category,
            workspace=workspace or self.get_default_workspace(),
            data=data or {},
        )
        return ret

    @context.disable_permission_checks()
    def create_workflow_template(
        self,
        name: str,
        task_name: str,
        *,
        workspace: Workspace | None = None,
        task_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> WorkflowTemplate:
        """Create a workflow template."""
        return WorkflowTemplate.objects.create(
            name=name,
            workspace=workspace or self.get_default_workspace(),
            task_name=task_name,
            task_data=task_data or {},
            **kwargs,
        )

    @context.disable_permission_checks()
    def create_workflow(
        self,
        task_name: str | WorkflowTemplate = "noop",
        task_data: BaseWorkflowData | dict[str, Any] | None = None,
        parent: WorkRequest | None = None,
        status: WorkRequest.Statuses | None = None,
        created_by: User | None = None,
        validate: bool = True,
    ) -> WorkRequest:
        """Create a workflow."""
        if created_by is None:
            created_by = self.get_default_user()

        if isinstance(task_name, WorkflowTemplate):
            template = task_name
        else:
            workflow_template_name = f"{task_name}-template"
            try:
                template = WorkflowTemplate.objects.get(
                    name=workflow_template_name
                )
            except WorkflowTemplate.DoesNotExist:
                template = self.create_workflow_template(
                    name=workflow_template_name, task_name=task_name
                )

        if isinstance(task_data, BaseWorkflowData):
            task_data = model_to_json_serializable_dict(
                task_data, exclude_unset=True
            )

        with context.local():
            context.reset()
            context.set_scope(template.workspace.scope)
            context.set_user(created_by)
            template.workspace.set_current()
            with context.disable_permission_checks():
                return WorkRequest.objects.create_workflow(
                    template=template,
                    data=task_data or {},
                    parent=parent,
                    status=status,
                    validate=validate,
                )

    @context.disable_permission_checks()
    def create_work_request(
        self,
        mark_running: bool = False,
        assign_new_worker: bool = False,
        result: WorkRequest.Results | None = None,
        expired: bool = False,
        workspace: Workspace | None = None,
        task_name: str = "noop",
        task_data: BaseTaskData | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> WorkRequest:
        """
        Return a new instance of WorkRequest.

        :param mark_running: if True call mark_running() method
        :param assign_new_worker: if True assign worker to the work request
        :param result: if not None call mark_completed(result)
        :param expired: True to create an expired work request
          (created_at: 1 year ago, expiration_delay: 1 day)
        :param kwargs: use them when creating the WorkRequest model
        """
        # FileStore and Workspace are usually created by the migrations
        # When using a TransactionTestCase with async methods
        # the tests don't have access to the created FileStore / Workspace
        # yet (need verification, but they seem to be in a non-committed
        # transaction and the test code is in a different thread because
        # of the implementation of database_sync_to_async
        if workspace is None:
            workspace = self.create_workspace(public=True)

        defaults: dict[str, Any] = {
            "task_name": task_name,
            "workspace": workspace,
        }
        if "created_by" not in kwargs:
            token = self.create_user_token()
            defaults["created_by"] = token.user
        if expired:
            defaults.update(
                created_at=timezone.now() - timedelta(days=365),
                expiration_delay=timedelta(days=1),
            )
        if isinstance(task_data, BaseTaskData):
            defaults["task_data"] = model_to_json_serializable_dict(
                task_data, exclude_unset=True
            )
        else:
            defaults["task_data"] = task_data or {}

        defaults.update(kwargs)

        created_at = defaults.pop("created_at", None)

        work_request = WorkRequest.objects.create(**defaults)

        # created_at is set to now automatically by create via auto_now_add, so
        # if a specific value was requested we need to set it after creation
        if created_at is not None:
            work_request.created_at = created_at
            work_request.save()

        if assign_new_worker:
            work_request.assign_worker(self.create_worker())

        if mark_running:
            work_request.mark_running()

        if result is not None:
            work_request.mark_completed(result)

        if completed_at := kwargs.get("completed_at"):
            work_request.completed_at = completed_at
            work_request.save()

        return work_request

    @context.disable_permission_checks()
    def create_worker(
        self,
        worker_type: WorkerType = WorkerType.EXTERNAL,
        extra_dynamic_metadata: dict[str, Any] | None = None,
        worker_pool: WorkerPool | None = None,
        fqdn: str = "computer.lan",
    ) -> Worker:
        """Return a new Worker."""
        if worker_pool is not None:
            Worker.objects.create_pool_members(worker_pool=worker_pool, count=1)
            worker = Worker.objects.filter(
                worker_type=worker_type,
                worker_pool=worker_pool,
            ).latest("registered_at")
        else:
            worker = Worker.objects.create_with_fqdn(
                fqdn,
                worker_type=worker_type,
                token=self.create_bare_token(),
            )

        dynamic_metadata = {
            "system:cpu_cores": 4,
            "system:worker_type": worker_type,
            "sbuild:version": 1,
        }

        if extra_dynamic_metadata:
            dynamic_metadata.update(extra_dynamic_metadata)

        worker.set_dynamic_metadata(dynamic_metadata)

        if worker_pool:
            worker.instance_created_at = timezone.now()
            worker.save()

        return worker

    @context.disable_permission_checks()
    def create_worker_pool(
        self,
        name: str = "test",
        enabled: bool = True,
        architectures: list[str] | None = None,
        tags: list[str] | None = None,
        provider_account: Asset | None = None,
        specifications: WorkerPoolSpecifications | None = None,
        instance_wide: bool = True,
        ephemeral: bool = False,
        limits: WorkerPoolLimits | None = None,
    ) -> WorkerPool:
        """Return a new WorkerPool."""
        if architectures is None:
            architectures = ["amd64", "i386"]
        if tags is None:
            tags = []
        if provider_account is None:
            provider_account = self.create_cloud_provider_account_asset()
        if specifications is None:
            provider_type = provider_account.data["provider_type"]
            match provider_type:
                case CloudProvidersType.DUMMY:
                    specifications = DummyWorkerPoolSpecification()
                case CloudProvidersType.AWS:
                    specifications = AWSEC2WorkerPoolSpecification(
                        launch_templates=[]
                    )
                case CloudProvidersType.HETZNER:
                    specifications = HetznerCloudWorkerPoolSpecification(
                        server_type="cx22",
                        image_name="debian-12",
                    )
                case _:
                    raise NotImplementedError(f"No support for {provider_type}")
        if limits is None:
            limits = WorkerPoolLimits()
        pool = WorkerPool.objects.create(
            name=name,
            provider_account=provider_account,
            enabled=enabled,
            architectures=architectures,
            tags=tags,
            specifications=specifications.dict(),
            instance_wide=instance_wide,
            ephemeral=ephemeral,
            limits=limits.dict(),
            registered_at=timezone.now(),
        )
        return pool

    @context.disable_permission_checks()
    def create_file(self, contents: bytes = b"test") -> File:
        """
        Create a File model and return the saved fileobj.

        :param contents: used to compute hash digest and size
        """
        hashed = _calculate_hash_from_data(contents)
        file, _ = File.objects.get_or_create(
            hash_digest=hashed, size=len(contents)
        )
        return file

    @context.disable_permission_checks()
    def create_file_in_backend(
        self,
        backend: FileBackendInterface[Any] | None = None,
        contents: bytes = b"test",
    ) -> File:
        """
        Create a temporary file and adds it in the backend.

        :param backend: file backend to add the file in
        :param contents: contents of the file
        """
        if backend is None:
            backend = self.get_default_file_store().get_backend_object()
        with tempfile.NamedTemporaryFile("w+b") as fd:
            fd.write(contents)
            fd.flush()

            return backend.add_file(Path(fd.name))

    @context.disable_permission_checks()
    def create_bare_data_item(
        self,
        parent_collection: Collection,
        name: str,
        *,
        category: BareDataCategory = BareDataCategory.TEST,
        data: BaseArtifactDataModel | dict[str, Any] | None = None,
        created_by_user: User | None = None,
        created_by_workflow: WorkRequest | None = None,
    ) -> CollectionItem:
        """Create a collection item holding bare data."""
        return CollectionItem.objects.create_from_bare_data(
            parent_collection=parent_collection,
            name=name,
            category=category,
            data=data or {},
            created_by_user=created_by_user or self.get_default_user(),
            created_by_workflow=created_by_workflow,
        )

    @context.disable_permission_checks()
    def create_artifact(
        self,
        paths: list[str] | dict[str, bytes] | None = None,
        files_size: int = 100,
        *,
        category: ArtifactCategory = ArtifactCategory.TEST,
        workspace: Workspace | None = None,
        data: ArtifactData | dict[str, Any] | None = None,
        expiration_delay: int | None = None,
        work_request: WorkRequest | None = None,
        created_by: User | None = None,
        create_files: bool = False,
        skip_add_files_in_store: bool = False,
    ) -> tuple[Artifact, dict[str, bytes]]:
        """
        Create an artifact and return tuple with the artifact and files data.

        :param paths: list of paths to create (will contain random data)
        :param files_size: size of the test data
        :param category: this artifact's category (see
          :ref:`artifact-reference`)
        :param data: key-value data for this artifact (see
          :ref:`artifact-reference`)
        :param expiration_delay: set expiration_delay field (in days)
        :param work_request: work request that created this artifact
        :param created_by: set Artifact.created_by to it
        :param create_files: create a file and add it into the LocalFileBackend
        :param skip_add_files_in_store: do not add the files in the store
          (only create the File object in the database)

        This method return a tuple:
        - artifact: Artifact
        - files_contents: Dict[str, bytes] (paths and test data)
        """
        # Import here to avoid a circular loop
        from debusine.test.test_utils import data_generator

        if skip_add_files_in_store and not create_files:
            raise ValueError(
                "skip_add_files_in_store must be False if create_files is False"
            )

        if workspace is None:
            workspace = self.get_default_workspace()

        match data:
            case ArtifactData():
                raw_data = model_to_json_serializable_dict(data)
            case dict():
                raw_data = data
            case None:
                raw_data = {}
            case _ as unreachable:
                assert_never(unreachable)

        artifact = Artifact.objects.create(
            category=category,
            workspace=workspace,
            data=raw_data,
            expiration_delay=(
                timedelta(expiration_delay)
                if expiration_delay is not None
                else None
            ),
            created_by_work_request=work_request,
            created_by=created_by,
        )

        data_gen = data_generator(files_size)

        files_contents = {}
        if isinstance(paths, dict):
            files_contents.update(paths)
        elif paths is None:
            pass
        else:
            for path in paths:
                files_contents[path] = next(data_gen)

        if create_files:
            file_backend = workspace.scope.file_stores.order_by(
                F("filestoreinscope__upload_priority").desc(nulls_last=True)
            )[0].get_backend_object()

            for path, contents in files_contents.items():
                if skip_add_files_in_store:
                    fileobj = self.create_file(contents)
                else:
                    fileobj = self.create_file_in_backend(
                        file_backend, contents
                    )

                FileInArtifact.objects.create(
                    artifact=artifact,
                    path=path,
                    file=fileobj,
                    complete=not skip_add_files_in_store,
                )

        return artifact, files_contents

    @context.disable_permission_checks()
    def create_artifact_from_local(
        self,
        local_artifact: LocalArtifact[Any],
        *,
        workspace: Workspace | None = None,
        expiration_delay: int | None = None,
        work_request: WorkRequest | None = None,
        created_by: User | None = None,
        create_files: bool = False,
        skip_add_files_in_store: bool = False,
    ) -> Artifact:
        """Create an artifact in the database from a `LocalArtifact`."""
        artifact, _ = self.create_artifact(
            paths={
                name: path.read_bytes()
                for name, path in local_artifact.files.items()
            },
            category=local_artifact.category,
            workspace=workspace or self.get_default_workspace(),
            data=local_artifact.data,
            expiration_delay=expiration_delay,
            work_request=work_request,
            created_by=created_by or self.get_default_user(),
            create_files=create_files,
            skip_add_files_in_store=skip_add_files_in_store,
        )
        return artifact

    @context.disable_permission_checks()
    def create_file_upload(self) -> FileUpload:
        """
        Create a new FileUpload object.

        Create the workspace, artifact, file and file_in_artifact associated
        to the file_upload object.
        """
        artifact, _ = self.create_artifact(
            paths=["README"], create_files=True, skip_add_files_in_store=True
        )

        file_in_artifact = artifact.fileinartifact_set.first()
        assert file_in_artifact is not None

        return FileUpload.objects.create(
            file_in_artifact=file_in_artifact,
            path="temp_file_aaaa",
        )

    @context.disable_permission_checks()
    def create_artifact_relation(
        self,
        artifact: Artifact,
        target: Artifact,
        relation_type: ArtifactRelation.Relations = (
            ArtifactRelation.Relations.RELATES_TO
        ),
    ) -> ArtifactRelation:
        """Create an ArtifactRelation."""
        return ArtifactRelation.objects.create(
            artifact=artifact, target=target, type=relation_type
        )

    @context.disable_permission_checks()
    def create_source_artifact(
        self,
        *,
        name: str = "hello",
        version: str = "1.0-1",
        architectures: set[str] | None = None,
        workspace: Workspace | None = None,
        created_by: User | None = None,
        create_files: bool = False,
    ) -> Artifact:
        """Create an artifact for a source package."""
        with tempfile.TemporaryDirectory() as tempdir:
            workdir = Path(tempdir)
            source_package = self.create_source_package(
                workdir, name=name, version=version, architectures=architectures
            )
            return self.create_artifact_from_local(
                source_package,
                workspace=workspace,
                created_by=created_by,
                create_files=create_files,
            )

    def create_minimal_binary_packages_artifact(
        self,
        srcpkg_name: str = "hello",
        srcpkg_version: str = "1.0-1",
        version: str = "1.0-1",
        architecture: str = "all",
    ) -> Artifact:
        """Create a minimal `debian:binary-packages` artifact."""
        artifact, _ = self.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGES,
            data={
                "srcpkg_name": srcpkg_name,
                "srcpkg_version": srcpkg_version,
                "version": version,
                "architecture": architecture,
                "packages": [],
            },
        )
        return artifact

    def create_minimal_binary_package_artifact(
        self,
        srcpkg_name: str = "hello",
        srcpkg_version: str = "1.0-1",
        package: str = "hello",
        version: str = "1.0-1",
        architecture: str = "all",
    ) -> Artifact:
        """Create a minimal `debian:binary-package` artifact."""
        artifact, _ = self.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": srcpkg_name,
                "srcpkg_version": srcpkg_version,
                "deb_fields": {
                    "Architecture": architecture,
                    "Package": package,
                    "Version": version,
                },
                "deb_control_files": [],
            },
        )
        return artifact

    @overload
    def create_upload_artifacts(
        self,
        *,
        src_name: str = ...,
        version: str = ...,
        source: Literal[True] = True,
        binary: Literal[False],
        binaries: list[str] | None = ...,
        workspace: Workspace | None = ...,
        work_request: WorkRequest | None = None,
        created_by: User | None = ...,
    ) -> SourceUploadArtifacts: ...

    @overload
    def create_upload_artifacts(
        self,
        *,
        src_name: str = ...,
        version: str = ...,
        source: Literal[False],
        binary: Literal[True] = True,
        binaries: list[str] | None = ...,
        workspace: Workspace | None = ...,
        work_request: WorkRequest | None = None,
        created_by: User | None = ...,
    ) -> BinaryUploadArtifacts: ...

    @overload
    def create_upload_artifacts(
        self,
        *,
        src_name: str = ...,
        version: str = ...,
        source: Literal[True] = True,
        binary: Literal[True] = True,
        binaries: list[str] | None = ...,
        workspace: Workspace | None = ...,
        work_request: WorkRequest | None = None,
        created_by: User | None = ...,
    ) -> MixedUploadArtifacts: ...

    @context.disable_permission_checks()
    def create_upload_artifacts(
        self,
        *,
        src_name: str = "hello",
        version: str = "1.0-1",
        source: bool = True,
        binary: bool = True,
        binaries: list[str] | None = None,
        workspace: Workspace | None = None,
        work_request: WorkRequest | None = None,
        created_by: User | None = None,
        create_files: bool = False,
    ) -> UploadArtifacts:
        """Create a set of artifacts for a package upload."""
        if binaries is None:
            binaries = [src_name]
        local_artifacts: dict[str, list[LocalArtifact[Any]]] = {
            "source": [],
            "binaries": [],
        }
        artifacts: dict[str, list[Artifact]] = {}
        with tempfile.TemporaryDirectory() as tempdir:
            workdir = Path(tempdir)
            upload = self.create_upload(
                workdir,
                src_name=src_name,
                version=version,
                source=source,
                binary=binary,
                binaries=binaries,
            )
            local_artifacts["upload"] = [upload]
            if source:
                source_package = SourcePackage.create(
                    name=src_name,
                    version=version,
                    files=[
                        file
                        for file in upload.files.values()
                        if file.name.endswith((".dsc", ".tar.gz", ".tar.xz"))
                    ],
                )
                local_artifacts["source"] = [source_package]
            if binary:
                binary_packages: list[LocalArtifact[Any]] = [
                    BinaryPackage.create(file=file)
                    for file in upload.files.values()
                    if file.suffix == ".deb"
                ]
                local_artifacts["binaries"] = binary_packages

            for key, values in local_artifacts.items():
                artifacts[key] = [
                    self.create_artifact_from_local(
                        local_artifact,
                        workspace=workspace,
                        work_request=work_request,
                        created_by=created_by,
                        create_files=create_files,
                    )
                    for local_artifact in values
                ]

        if source:
            self.create_artifact_relation(
                artifact=artifacts["upload"][0],
                target=artifacts["source"][0],
                relation_type=ArtifactRelation.Relations.EXTENDS,
            )
        for binary_artifact in artifacts["binaries"]:
            self.create_artifact_relation(
                artifact=artifacts["upload"][0],
                target=binary_artifact,
                relation_type=ArtifactRelation.Relations.EXTENDS,
            )

        return UploadArtifacts(
            upload=artifacts["upload"][0],
            source=artifacts["source"][0] if source else None,
            binaries=artifacts["binaries"] or None,
        )

    @context.disable_permission_checks()
    def create_build_log_artifact(
        self,
        *,
        source: str = "hello",
        version: str = "1.0-1",
        build_arch: str = "amd64",
        workspace: Workspace | None = None,
        work_request: WorkRequest | None = None,
        created_by: User | None = None,
        contents: bytes | None = None,
        skip_add_files_in_store: bool = False,
    ) -> Artifact:
        """Create an artifact for a build log."""
        filename = f"{source}_{version}_{build_arch}.buildlog"
        if contents is None:
            contents = "\n".join(
                f"Line {lineno} of {filename}" for lineno in range(1, 11)
            ).encode()
        if created_by is None:
            if work_request is not None:
                created_by = work_request.created_by
            else:
                created_by = self.get_default_user()
        data = DebianPackageBuildLog(
            source=source, version=version, filename=filename
        )
        artifact, _ = self.create_artifact(
            paths={filename: contents},
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            workspace=workspace or self.get_default_workspace(),
            data=data.dict(),
            work_request=work_request,
            created_by=created_by,
            create_files=True,
            skip_add_files_in_store=skip_add_files_in_store,
        )
        return artifact

    def create_signing_input_artifact(
        self,
        binary_package_name: str | None = "hello",
        trusted_certs: list[str] | None = None,
        workspace: Workspace | None = None,
        work_request: WorkRequest | None = None,
    ) -> Artifact:
        """Create a minimal `debusine:signing-input` artifact."""
        artifact, _ = self.create_artifact(
            category=ArtifactCategory.SIGNING_INPUT,
            data=DebusineSigningInput(
                trusted_certs=trusted_certs,
                binary_package_name=binary_package_name,
            ),
            workspace=workspace,
            work_request=work_request,
        )
        return artifact

    def create_signing_output_artifact(
        self,
        purpose: KeyPurpose,
        fingerprint: str,
        results: list[SigningResult] | None = None,
        binary_package_name: str | None = None,
        workspace: Workspace | None = None,
        work_request: WorkRequest | None = None,
    ) -> Artifact:
        """Create a minimal `debusine:signing-output` artifact."""
        artifact, _ = self.create_artifact(
            category=ArtifactCategory.SIGNING_OUTPUT,
            data=DebusineSigningOutput(
                purpose=purpose,
                fingerprint=fingerprint,
                results=results or [],
                binary_package_name=binary_package_name,
            ),
            workspace=workspace,
            work_request=work_request,
        )
        return artifact

    @context.disable_permission_checks()
    def create_asset(
        self,
        category: AssetCategory,
        data: BaseAssetDataModel | dict[str, Any],
        workspace: Workspace | None = None,
        created_by: User | None = None,
        created_by_work_request: WorkRequest | None = None,
    ) -> Asset:
        """Create an Asset."""
        if created_by is None:
            created_by = self.get_default_user()
        return Asset.objects.create(
            category=category,
            workspace=workspace,
            data=data.dict() if isinstance(data, BaseAssetDataModel) else data,
            created_by=created_by,
            created_by_work_request=created_by_work_request,
        )

    def create_signing_key_asset(
        self,
        purpose: KeyPurpose = KeyPurpose.OPENPGP,
        fingerprint: str | None = None,
        public_key: str | None = None,
        description: str | None = "Test Key",
        workspace: Workspace | None = None,
        created_by: User | None = None,
        created_by_work_request: WorkRequest | None = None,
    ) -> Asset:
        """Create a debusine:signing-key asset."""
        if fingerprint is None:
            fingerprint = token_hex()
        if public_key is None:
            public_key = textwrap.dedent(
                f"""\
            -----BEGIN PGP PUBLIC KEY BLOCK-----

            Comment: This isn't a valid key.

            {token_hex()}
            {token_hex()}
            {token_hex()}

            -----END PGP PUBLIC KEY BLOCK-----
            """
            )
        if workspace is None:
            workspace = self.get_default_workspace()
        return self.create_asset(
            category=AssetCategory.SIGNING_KEY,
            data=SigningKeyData(
                purpose=purpose,
                fingerprint=fingerprint,
                public_key=public_key,
                description=description,
            ),
            workspace=workspace,
            created_by=created_by,
            created_by_work_request=created_by_work_request,
        )

    @context.disable_permission_checks()
    def create_cloud_provider_account_asset(
        self,
        data: CloudProviderAccountData | None = None,
        cloud_provider: CloudProvidersType | None = None,
        name: str | None = None,
        workspace: Workspace | None = None,
        created_by: User | None = None,
        created_by_work_request: WorkRequest | None = None,
    ) -> Asset:
        """Create a debusine:cloud-provider-account asset."""
        if data is None:
            if name is None:
                name = "test"
            match cloud_provider:
                case CloudProvidersType.AWS:
                    data = AWSProviderAccountData(
                        name=name,
                        configuration=AWSProviderAccountConfiguration(
                            region_name="test-region"
                        ),
                        credentials=AWSProviderAccountCredentials(
                            access_key_id="access-key",
                            secret_access_key="secret-key",
                        ),
                    )
                case CloudProvidersType.HETZNER:
                    data = HetznerProviderAccountData(
                        name=name,
                        configuration=HetznerProviderAccountConfiguration(
                            region_name="test-region",
                        ),
                        credentials=HetznerProviderAccountCredentials(
                            api_token="api-token",
                        ),
                    )
                case _:
                    data = DummyProviderAccountData(name=name)
        return self.create_asset(
            category=AssetCategory.CLOUD_PROVIDER_ACCOUNT,
            data=data,
            workspace=workspace,
            created_by=created_by,
            created_by_work_request=created_by_work_request,
        )

    @context.disable_permission_checks()
    def create_asset_usage(
        self,
        resource: Asset,
        workspace: Workspace | None = None,
    ) -> AssetUsage:
        """Create an AssetUsage model."""
        if workspace is None:
            workspace = self.get_default_workspace()
        return AssetUsage.objects.create(asset=resource, workspace=workspace)

    @context.disable_permission_checks()
    def create_debian_environments_collection(
        self, name: str = "debian", *, workspace: Workspace | None = None
    ) -> Collection:
        """Create a debian:environments collection."""
        if workspace is None:
            workspace = self.get_default_workspace()
        collection, _ = Collection.objects.get_or_create(
            name=name,
            category=CollectionCategory.ENVIRONMENTS,
            workspace=workspace,
        )
        return collection

    @context.disable_permission_checks()
    def create_debian_environment(  # noqa: C901
        self,
        *,
        environment: Artifact | None = None,
        category: ArtifactCategory = ArtifactCategory.SYSTEM_TARBALL,
        codename: str = "bookworm",
        architecture: str = "amd64",
        variant: str | None = None,
        collection: Collection | None = None,
        user: User | None = None,
        variables: dict[str, Any] | None = None,
        workspace: Workspace | None = None,
        vendor: str = "Debian",
        mirror: str = "https://deb.debian.org",
        pkglist: list[str] | None = None,
        with_init: bool = True,
        with_dev: bool = True,
        create_files: bool = False,
    ) -> CollectionItem:
        """Create a debian build environment."""
        if workspace is None:
            workspace = self.get_default_workspace()
        if collection is None:
            collection = self.create_debian_environments_collection(
                workspace=workspace
            )
        if user is None:
            user = self.get_default_user()
        if variant is None and variables is not None:
            variant = variables.get("variant")
        if environment is None:
            # Try looking up an existing environment
            lookup_string = (
                f"{collection.name}@{collection.category}"
                f"/match:codename={codename}"
                f":architecture={architecture}"
            )
            if category == ArtifactCategory.SYSTEM_TARBALL:
                lookup_string += ":format=tarball"
            else:
                lookup_string += ":format=image"
            if variant is not None:
                lookup_string += f":variant={variant}"
            try:
                lookup_result = lookup_single(
                    lookup_string, workspace, user=user
                )
                assert lookup_result.collection_item is not None
                return lookup_result.collection_item
            except KeyError:
                pass
        if environment is None:
            # Lookup failed: create it
            data = {
                "filename": "test",
                "vendor": vendor,
                "mirror": mirror,
                "pkglist": pkglist or [],
                "codename": codename,
                "architecture": architecture,
                "variant": variant,
                "with_init": with_init,
                "with_dev": with_dev,
            }
            environment, _ = self.create_artifact(
                category=category,
                data=data,
                workspace=workspace,
                create_files=create_files,
            )
        if variables is None:
            variables = {"backend": "unshare", "variant": variant}

        try:
            return CollectionItem.active_objects.get(
                parent_collection=collection,
                artifact=environment,
                data__contains=variables,
            )
        except CollectionItem.DoesNotExist:
            pass

        manager = collection.manager
        item = manager.add_artifact(environment, user=user, variables=variables)
        return item

    @context.disable_permission_checks()
    def create_sbuild_work_request(
        self,
        *,
        source: Artifact,
        architecture: str = "all",
        environment: Artifact,
        workflow: WorkRequest | None = None,
        workspace: Workspace | None = None,
    ) -> WorkRequest:
        """Create a sbuild work request."""
        task_data = SbuildData(
            input=SbuildInput(source_artifact=source.pk),
            host_architecture=architecture,
            environment=environment.pk,
            backend=BackendType.UNSHARE,
            build_components=[
                (
                    SbuildBuildComponent.ALL
                    if architecture == "all"
                    else SbuildBuildComponent.ANY
                )
            ],
        )
        if workflow:
            return workflow.create_child(
                task_name="sbuild",
                task_data=task_data,
                workflow_data=WorkRequestWorkflowData(
                    display_name=f"Build {architecture}",
                    step=f"build-{architecture}",
                ),
            )
        else:
            assert task_data.host_architecture != "all", (
                "For an Sbuild task (not workflow): host_architecture must "
                "be a real architecture (e.g. amd64)"
            )
            return self.create_work_request(
                task_name="sbuild",
                task_data=task_data,
                workspace=workspace or self.get_default_workspace(),
            )

    @context.disable_permission_checks()
    def simulate_package_build(
        self,
        source: Artifact,
        *,
        architecture: str = "all",
        workflow: WorkRequest | None = None,
        environment: Artifact | None = None,
        worker: Worker | None = None,
    ) -> WorkRequest:
        """Generate database objects as if a package build happened."""
        workspace = source.workspace
        name = source.data["name"]
        version = source.data["version"]

        if environment is None:
            environment_item = self.create_debian_environment()
            assert environment_item.artifact is not None
            environment = environment_item.artifact

        build_arch = environment.data["architecture"]

        # Create sbuild work request
        work_request = self.create_sbuild_work_request(
            source=source,
            architecture=architecture,
            environment=environment,
            workflow=workflow,
            workspace=workspace,
        )
        if worker is None:
            worker = self.create_worker()

        # Assign a worker
        work_request.assign_worker(worker)
        work_request.mark_running()

        with tempfile.TemporaryDirectory() as tempdir:
            workdir = Path(tempdir)

            # Add a build log
            buildlog_artifact = self.create_build_log_artifact(
                source=name,
                version=version,
                build_arch=build_arch,
                workspace=workspace,
                work_request=work_request,
            )
            self.create_artifact_relation(
                buildlog_artifact, source, ArtifactRelation.Relations.RELATES_TO
            )

            # Add the .deb
            binarypackage = self.create_binary_package(
                workdir, name=name, version=version, architecture=architecture
            )
            binarypackage_artifact = self.create_artifact_from_local(
                binarypackage,
                workspace=workspace,
                work_request=work_request,
                created_by=work_request.created_by,
                create_files=True,
            )
            self.create_artifact_relation(
                binarypackage_artifact,
                source,
                ArtifactRelation.Relations.BUILT_USING,
            )
            self.create_artifact_relation(
                buildlog_artifact,
                binarypackage_artifact,
                ArtifactRelation.Relations.RELATES_TO,
            )

            # TODO: create the .changes file / Upload artifact
            # TODO:     adding the .dsc, the sources, the .deb
            # TODO: self.create_artifact_relation(
            # TODO:     upload,
            # TODO:     binarypackage_artifact,
            # TODO:     ArtifactRelation.Relations.EXTENDS,
            # TODO: )
            # TODO: self.create_artifact_relation(
            # TODO:     upload,
            # TODO:     binarypackage_artifact,
            # TODO:     ArtifactRelation.Relations.RELATES_TO,
            # TODO: )

            # Complete the task
            work_request.mark_completed(WorkRequest.Results.SUCCESS)

        return work_request

    @context.disable_permission_checks()
    def build_scenario(
        self,
        scenario: "Scenario",
        /,
        scenario_name: str | None = None,
        set_current: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Build a test scenario.

        Keyword arguments are forwarded to the scenario constructor.

        :param scenario: scenario instance to build
        :param scenario_name: if set, register the result in self.scenarios
        :param set_current: set to True to set the current context from the
                            scenario
        :return: the scenario instance
        """
        with context.local():
            scenario.build(self)
        if scenario_name is not None:
            self.scenarios[scenario_name] = scenario
        if set_current and scenario.needs_set_current:
            scenario.set_current()
