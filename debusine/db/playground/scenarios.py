# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Inheritable test scenarios."""

import contextlib
import textwrap
from collections.abc import Generator
from functools import cached_property
from typing import TYPE_CHECKING

from django.db.models import Model

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebianSourcePackage,
    DebianUpload,
)
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Collection,
    CollectionItem,
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
from debusine.db.models.auth import Identity
from debusine.tasks import DebDiff
from debusine.tasks.models import WorkerType

if TYPE_CHECKING:
    from debusine.db.playground.playground import Playground


class Scenario:
    """Base for inheritable test scenarios."""

    # Implementation notes:
    #
    # Database is rolled back for each test method after build() is called:
    #
    # * objects created at build() time remain valid in the database (and are
    #   reset to their build() state)
    # * objects created in other methods will become invalid at the end of the
    #   test method
    #
    # This means no @cached_property and other internally cached values that
    # are not created in build().
    #
    # See #626 for details.

    playground: "Playground"

    def __init__(self, *, set_current: bool = False) -> None:
        """Store scenario arguments."""
        self.needs_set_current = set_current

    def build(self, playground: "Playground") -> None:
        """
        Build the scenario.

        This is run by Playground with permission tests disabled and in a local
        context that will be restored at the end of the build.
        """
        self.playground = playground

    def set_current(self) -> None:
        """Set the current user and workspace from the scenario."""
        pass


class DefaultScopeUser(Scenario):
    """
    Quick access to the default scope and user.

    Optionally sets them in context.
    """

    scope: Scope
    user: User

    def build(self, playground: "Playground") -> None:
        """Build the scenario."""
        super().build(playground)
        self.scope = self.playground.get_default_scope()
        self.user = self.playground.get_default_user()

    def set_current(self) -> None:
        """Set the current user and workspace from the scenario."""
        super().set_current()
        context.set_scope(self.scope)
        context.set_user(self.user)

    def create_user_token(self) -> Token:
        """Create a user token for self.user."""
        return self.playground.create_user_token(user=self.user)

    @cached_property
    def scope_owners(self) -> Group:
        """Return the group of scope owners."""
        return self.playground.create_group_role(self.scope, Scope.Roles.OWNER)

    @contextlib.contextmanager
    def assign_role(
        self, resource: Model, *roles: str
    ) -> Generator[None, None, None]:
        """Temporarily assign roles to the scenario user on the resource."""
        with self.playground.assign_role(resource, self.user, *roles):
            yield


class DefaultScopeUserAPI(DefaultScopeUser):
    """DefaultScopeUser, plus a user token."""

    user_token: Token

    def build(self, playground: "Playground") -> None:
        """Build the scenario."""
        super().build(playground)
        self.user_token = self.create_user_token()


class DefaultContext(DefaultScopeUser):
    """
    Quick access to the default scope, user and workspace.

    Optionally sets them in context.
    """

    workspace: Workspace

    def build(self, playground: "Playground") -> None:
        """Build the scenario."""
        super().build(playground)
        self.workspace = self.playground.get_default_workspace()

    def set_current(self) -> None:
        """Set the current user and workspace from the scenario."""
        super().set_current()
        self.workspace.set_current()

    @cached_property
    def workspace_owners(self) -> Group:
        """Return the group of workspace owners."""
        return self.playground.create_group_role(
            self.workspace, Workspace.Roles.OWNER
        )


class DefaultContextAPI(DefaultScopeUserAPI, DefaultContext):
    """DefaultContext, plus a user token."""


class UIPlayground(DefaultContext):
    """
    Base scenario for UI tests.

    This gives a default password to the test user.
    """

    suite: Collection
    env_amd64: Artifact
    env_s390x: Artifact
    source_hello: Artifact
    source_dpkg: Artifact
    source_udev: Artifact
    template_sbuild: WorkflowTemplate
    template_noop: WorkflowTemplate
    worker_pool: WorkerPool
    worker_static: Worker
    worker_celery: Worker
    worker_in_pool: Worker
    worker_in_pool_busy: Worker

    def build(self, playground: "Playground") -> None:
        """Build the scenario."""
        super().build(playground)
        self.set_current()

        # Set a password for the test user
        self.user.set_password("playground")
        self.user.save()

        asset = self.playground.create_signing_key_asset()
        self.playground.create_asset_usage(asset)

        # Pretend the user comes from Salsa
        Identity.objects.create(
            user=self.user,
            issuer="salsa",
            subject="playground@debian.example.org",
            claims={"test": True},
        )

        other_user = User.objects.create_user(
            username="playground-other", first_name="Other", last_name="User"
        )

        # Make them owners of the playground workspace...
        role_group = self.playground.create_group_role(
            self.workspace, Workspace.Roles.OWNER, users=[self.user]
        )
        # ...and admin of the owners group
        role_group.set_user_role(self.user, Group.Roles.ADMIN)

        # Create a second group with both users in it
        other_group = Group.objects.create(name="Playground", scope=self.scope)
        other_group.add_user(self.user)
        other_group.add_user(other_user)

        # Create a worker pool and some workers
        self.worker_pool = self.playground.create_worker_pool("playground")

        self.worker_static = self.playground.create_worker(
            fqdn="playground.lan"
        )
        self.worker_celery = self.playground.create_worker(
            worker_type=WorkerType.CELERY, fqdn="playground.lan"
        )
        self.worker_in_pool = self.playground.create_worker(
            fqdn="playground.lan", worker_pool=self.worker_pool
        )
        self.worker_in_pool_busy = self.playground.create_worker(
            fqdn="playground.lan", worker_pool=self.worker_pool
        )

        # Create a Debian scope, to test multi-scope UI elements
        self.playground.get_or_create_scope(
            "debian", label="Debian", icon="web/icons/debian-openlogo-nd.svg"
        )

        # Create a sbuild workflow template
        self.template_sbuild = self.playground.create_workflow_template(
            name="Build package", task_name="sbuild", task_data={}
        )

        # Create a debian:suite collection
        self.suite = self.playground.create_collection(
            workspace=self.workspace,
            name="play_bookworm",
            category=CollectionCategory.SUITE,
            data={
                "may_reuse_versions": False,
                "release_fields": {
                    "Suite": "stable",
                    "Codename": "bookworm",
                },
                "components": [
                    "main",
                    "contrib",
                    "non-free-firmware",
                    "non-free",
                ],
                "architectures": [
                    "all",
                    "amd64",
                    "arm64",
                    "armel",
                    "armhf",
                    "i386",
                    "mips64el",
                    "mipsel",
                    "ppc64el",
                    "s390x",
                ],
            },
        )

        # Create debian environments to simulate builds
        item = self.playground.create_debian_environment(architecture="amd64")
        assert item.artifact is not None
        self.env_amd64 = item.artifact
        item = self.playground.create_debian_environment(architecture="s390x")
        assert item.artifact is not None
        self.env_s390x = item.artifact

        # Create the source packages
        self.source_hello = self.playground.create_source_artifact(
            name="hello", version="1.0-1", create_files=True
        )
        self.source_dpkg = self.playground.create_source_artifact(
            name="dpkg",
            binaries=["dpkg", "dpkg-doc"],
            version="1.21.22",
            create_files=True,
        )
        self.source_udev = self.playground.create_source_artifact(
            name="udev", version="252.26-1~deb12u2", create_files=True
        )

        # Populate the debian:suite collection with artifacts
        wr = self.playground.simulate_package_build(
            self.source_hello, architecture="amd64", worker=self.worker_static
        )
        self.add_results_to_suite(wr)
        wr = self.playground.simulate_package_build(
            self.source_dpkg, architecture="amd64", worker=self.worker_static
        )
        self.add_results_to_suite(wr)
        wr = self.playground.simulate_package_build(
            self.source_dpkg, architecture="armhf", worker=self.worker_static
        )
        self.add_results_to_suite(wr)
        wr = self.playground.simulate_package_build(
            self.source_dpkg,
            binary_name="dpkg-doc",
            architecture="all",
            host_architecture="amd64",
            worker=self.worker_static,
        )
        self.add_results_to_suite(wr)

        # Create a variety of work requests
        wr = self.playground.create_work_request(
            status=WorkRequest.Statuses.COMPLETED,
            result=WorkRequest.Results.SUCCESS,
            task_name="noop",
        )
        wr.task_data = {"result": False}
        wr.configured_task_data = {"result": True}
        wr.save()
        self.playground.create_work_request(
            status=WorkRequest.Statuses.COMPLETED,
            result=WorkRequest.Results.FAILURE,
            task_name="noop",
        )
        self.playground.create_work_request(
            status=WorkRequest.Statuses.COMPLETED,
            result=WorkRequest.Results.ERROR,
            task_name="noop",
        )
        self.playground.create_work_request(
            status=WorkRequest.Statuses.ABORTED,
            task_name="noop",
        )

        wr = self.playground.create_work_request(task_name="noop")
        wr.assign_worker(self.worker_in_pool_busy)
        wr.mark_running()

        self.simulate_sbuild_workflow(self.template_sbuild, self.source_udev)

        # Create a binary DebDiff artifact and its required relation
        debdiff_report = textwrap.dedent(
            """
            some debdiff text header"

            Files in second .deb but not in first
            -------------------------------------
            -rw-r--r--  root/root   /etc/simplemonitor/monitor.ini
            -rw-r--r--  root/root   DEBIAN/conffiles

            Files in first .deb but not in second
            ------------------------------------
            -rw-r--r--  root/root   /usr/lib/python3/dist-packages/pyaarlo
            -rw-r--r--  root/root   /usr/share/doc/python3-pyaarlo/READ.gz

            Control files: lines which differ (wdiff format)
            ------------------------------------------------
            Description: [-one description-] {+another description+}
            Homepage: [-https://github.com/twrecked/pyaarlo-]
            {+https://simplemonitor.readthedocs.io+}"""
        ).encode("utf-8")

        artifact, _ = playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: debdiff_report},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.deb", "new": "hello-2.deb"},
        )

        target, _ = playground.create_artifact(
            category=ArtifactCategory.UPLOAD,
            data=DebianUpload(
                type="dpkg",
                changes_fields={
                    "Architecture": "amd64",
                    "Files": [{"name": "hello_1.0_amd64.deb"}],
                },
            ),
        )

        ArtifactRelation.objects.create(
            artifact=artifact,
            target=target,
            type=ArtifactRelation.Relations.RELATES_TO,
        )

        # Create a source DebDiff artifact and its required relation
        debdiff_report = textwrap.dedent(
            """\
            diff -u -N original/added.txt new/added.txt
            --- original/added.txt	1970-01-01 01:00:00.000000000 +0100
            +++ new/added.txt	2025-03-26 12:42:27.672906377 +0000
            @@ -0,0 +1 @@
            +new file
            diff -u -N original/changed.txt new/changed.txt
            --- original/changed.txt	2025-03-26 12:41:44.191924503 +0000
            +++ new/changed.txt	2025-03-26 12:42:21.945798421 +0000
            @@ -1,3 +1,2 @@
             1
             2
            -3
            diff -u -N original/removed.txt new/removed.txt
            --- original/removed.txt	2025-03-26 12:42:37.607381416 +0000
            +++ new/removed.txt	1970-01-01 01:00:00.000000000 +0100
            @@ -1 +0,0 @@
            -removed file
        """
        ).encode("utf-8")

        artifact, _ = self.playground.create_artifact(
            paths={DebDiff.CAPTURE_OUTPUT_FILENAME: debdiff_report},
            category=ArtifactCategory.DEBDIFF,
            create_files=True,
            data={"original": "hello-1.dsc", "new": "hello-2.dsc"},
        )

        target, _ = playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data=DebianSourcePackage(
                name="hello",
                version="1.0",
                type="dpkg",
                dsc_fields={},
            ),
        )
        ArtifactRelation.objects.create(
            artifact=artifact,
            target=target,
            type=ArtifactRelation.Relations.RELATES_TO,
        )

        # Create a variety of workflows

        self.template_noop = playground.create_workflow_template(
            name="Dummy",
            task_name="noop",
        )

        # 3 completed workflow
        for i in range(3):
            w = self.playground.create_workflow(task_name=self.template_noop)
            w.mark_running()
            w.mark_completed(WorkRequest.Results.SUCCESS)

        # 2 running workflows
        for i in range(2):
            w = self.playground.create_workflow(
                task_name=self.template_noop,
            )
            w.mark_running()

        # 1 needs input workflow
        workflow_needs_input = self.playground.create_workflow(
            task_name=self.template_noop,
        )
        workflow_needs_input.mark_running()
        workflow_needs_input.workflow_runtime_status = (
            WorkRequest.RuntimeStatuses.NEEDS_INPUT
        )
        workflow_needs_input.save()

        # Create and populate the default task-configuration collection
        try:
            task_config_collection = self.workspace.collections.get(
                name="default", category=CollectionCategory.TASK_CONFIGURATION
            )
        except Collection.DoesNotExist:
            task_config_collection = self.playground.create_collection(
                "default",
                CollectionCategory.TASK_CONFIGURATION,
                workspace=self.workspace,
            )
        task_config_collection.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=self.user,
            data={
                "template": "reduce-parallelism",
                "override_values": {"build_options": "parallel=2"},
                "comment": "reduce parallelism to avoid crashing workers",
            },
        )
        task_config_collection.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=self.user,
            data={
                "task_type": "Worker",
                "task_name": "sbuild",
                "subject": "openjdk-17",
                "use_templates": ["reduce-parallelism"],
                "comment": "reduce parallelism for openjdk-17",
            },
        )
        task_config_collection.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=self.user,
            data={
                "task_type": "Workflow",
                "task_name": "debian_pipeline",
                "subject": "u-boot",
                "default_values": {
                    "architectures_allowlist": ["arm64", "armhf"],
                },
                "comment": "only build on ARM by default",
            },
        )

    def add_results_to_suite(
        self,
        work_request: WorkRequest,
    ) -> None:
        """
        Add the work request artifacts to the debian:suite collection.

        :param suite: the target suite, or self.suite by default
        """
        suite = self.suite

        source = Artifact.objects.get(
            pk=work_request.task_data["input"]["source_artifact"]
        )

        if (
            not CollectionItem.objects.active()
            .filter(parent_collection=suite, artifact=source)
            .exists()
        ):
            suite.manager.add_artifact(
                source,
                user=self.user,
                variables={"component": "main", "section": "devel"},
            )
        for binary in Artifact.objects.filter(
            created_by_work_request=work_request,
            category=ArtifactCategory.BINARY_PACKAGE,
        ):
            suite.manager.add_artifact(
                binary,
                user=self.user,
                variables={
                    "component": "main",
                    "section": "devel",
                    "priority": "optional",
                },
            )

    def simulate_sbuild_workflow(
        self,
        template: WorkflowTemplate,
        source: Artifact,
    ) -> WorkRequest:
        """Simulate a sbuild workflow."""
        workflow = WorkRequest.objects.create_workflow(
            template=template,
            data={
                "input": {
                    "source_artifact": source.pk,
                },
                "backend": "unshare",
                "target_distribution": "debian:bookworm",
                "architectures": ["all", "amd64", "s390x"],
            },
        )
        workflow.mark_running()
        workflow.save()

        # A successful build
        self.playground.simulate_package_build(
            source,
            workflow=workflow,
            architecture="amd64",
            worker=self.worker_static,
        )

        # A failed build
        wr_s390x = self.playground.create_sbuild_work_request(
            source=source,
            architecture="s390x",
            environment=self.env_s390x,
            workflow=workflow,
        )
        wr_s390x.mark_pending()
        wr_s390x.mark_running()
        wr_s390x.mark_completed(WorkRequest.Results.FAILURE)
        wr_s390x.save()

        # Retrying succeeded
        new_wr_s390x = wr_s390x.retry()
        new_wr_s390x.mark_pending()
        new_wr_s390x.mark_running()
        new_wr_s390x.mark_completed(WorkRequest.Results.SUCCESS)
        new_wr_s390x.save()

        return workflow
