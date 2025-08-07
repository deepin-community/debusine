# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the task to copy items into target collections."""

from collections.abc import Sequence
from typing import Any, ClassVar

from django.conf import settings

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
)
from debusine.client.models import LookupChildType
from debusine.db.context import context
from debusine.db.models import (
    Collection,
    CollectionItem,
    FileStore,
    Scope,
    User,
    WorkRequest,
    Workspace,
)
from debusine.server.collections.lookup import LookupResult, lookup_multiple
from debusine.server.tasks import CopyCollectionItems
from debusine.server.tasks.copy_collection_items import CannotCopy
from debusine.server.tasks.models import (
    CopyCollectionItemsCopies,
    CopyCollectionItemsData,
)
from debusine.tasks.models import LookupMultiple, TaskTypes, WorkerType
from debusine.test.django import TestCase


class CopyCollectionItemsTests(TestCase):
    """Tests for :py:class:`CopyCollectionItems`."""

    user: ClassVar[User]
    source_scope: ClassVar[Scope]
    source_workspace: ClassVar[Workspace]
    source_build_logs: ClassVar[Collection]
    target_scope: ClassVar[Scope]
    target_workspace: ClassVar[Workspace]
    target_build_logs: ClassVar[Collection]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.user = cls.playground.get_default_user()
        cls.source_scope = cls.playground.get_or_create_scope(
            name="source", label="Source"
        )
        cls.source_workspace = cls.playground.create_workspace(
            scope=cls.source_scope, name="source", public=True
        )
        cls.source_build_logs = cls.playground.create_singleton_collection(
            CollectionCategory.PACKAGE_BUILD_LOGS,
            workspace=cls.source_workspace,
        )
        cls.target_scope = cls.playground.get_or_create_scope(
            name="target", label="Target"
        )
        cls.target_workspace = cls.playground.create_workspace(
            scope=cls.target_scope, name="target", public=True
        )
        cls.target_build_logs = cls.playground.create_singleton_collection(
            CollectionCategory.PACKAGE_BUILD_LOGS,
            workspace=cls.target_workspace,
        )

    @context.disable_permission_checks()
    def create_build_log_placeholder(self) -> CollectionItem:
        """Create a bare data collection item with a build log placeholder."""
        return self.playground.create_bare_data_item(
            self.source_build_logs,
            "debian_bookworm_amd64_hello_1.0-1_1",
            category=BareDataCategory.PACKAGE_BUILD_LOG,
            data={
                "work_request_id": 1,
                "vendor": "debian",
                "codename": "bookworm",
                "architecture": "amd64",
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0-1",
            },
        )

    @context.disable_permission_checks()
    def create_build_log(
        self,
        work_request: WorkRequest | None = None,
        build_log_contents: bytes | None = None,
        skip_add_files_in_store: bool = False,
        extra_data: dict[str, Any] | None = None,
    ) -> CollectionItem:
        """Create an artifact collection item with a build log."""
        if work_request is None:
            work_request = self.playground.create_work_request(
                workspace=self.source_workspace
            )
        artifact = self.playground.create_build_log_artifact(
            source="hello",
            version="1.0-1",
            build_arch="amd64",
            workspace=self.source_workspace,
            work_request=work_request,
            contents=build_log_contents,
            skip_add_files_in_store=skip_add_files_in_store,
        )
        if extra_data is not None:
            artifact.data |= extra_data
            artifact.save()
        artifact_item_data = {
            "work_request_id": work_request.id,
            "vendor": "debian",
            "codename": "bookworm",
            "architecture": "amd64",
        }
        return self.source_build_logs.manager.add_artifact(
            artifact, user=self.user, variables=artifact_item_data
        )

    def lookup_source_items(
        self, lookup: dict[str, Any]
    ) -> Sequence[LookupResult]:
        """Look up source items in preparation for copying them."""
        return lookup_multiple(
            LookupMultiple.parse_obj(
                {
                    "collection": f"_@{CollectionCategory.PACKAGE_BUILD_LOGS}",
                    **lookup,
                }
            ),
            self.source_workspace,
            user=self.user,
            expect_type=LookupChildType.ANY,
        )

    def test_copy_item_bare(self) -> None:
        """Copy a bare data item."""
        bare_item = self.create_build_log_placeholder()
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.BARE}
        )
        self.assertEqual(len(source_items), 1)

        CopyCollectionItems.copy_item(
            source_items[0], self.target_build_logs, user=self.user
        )

        self.assertTrue(
            self.target_build_logs.child_items.filter(
                name="debian_bookworm_amd64_hello_1.0-1_1",
                child_type=CollectionItem.Types.BARE,
                category=BareDataCategory.PACKAGE_BUILD_LOG,
                collection__isnull=True,
                artifact__isnull=True,
                data=bare_item.data,
                removed_at__isnull=True,
            ).exists()
        )

    def test_copy_item_bare_needs_unembargo(self) -> None:
        """Copying bare data from private to public needs unembargo=True."""
        self.create_build_log_placeholder()
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.BARE}
        )
        self.assertEqual(len(source_items), 1)
        self.source_workspace.public = False
        self.source_workspace.save()

        with self.assertRaisesRegex(
            CannotCopy,
            f"Copying from {self.source_workspace} to {self.target_workspace} "
            f"requires unembargo=True",
        ):
            CopyCollectionItems.copy_item(
                source_items[0], self.target_build_logs, user=self.user
            )

    def test_copy_item_bare_with_unembargo(self) -> None:
        """Copying bare data from private to public can unembargo it."""
        bare_item = self.create_build_log_placeholder()
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.BARE}
        )
        self.assertEqual(len(source_items), 1)
        self.source_workspace.public = False
        self.source_workspace.save()

        CopyCollectionItems.copy_item(
            source_items[0],
            self.target_build_logs,
            user=self.user,
            unembargo=True,
        )

        self.assertTrue(
            self.target_build_logs.child_items.filter(
                name="debian_bookworm_amd64_hello_1.0-1_1",
                child_type=CollectionItem.Types.BARE,
                category=BareDataCategory.PACKAGE_BUILD_LOG,
                collection__isnull=True,
                artifact__isnull=True,
                data=bare_item.data,
                removed_at__isnull=True,
            ).exists()
        )

    def test_copy_item_bare_variables(self) -> None:
        """Pass additional variables while copying a bare item."""
        bare_item = self.create_build_log_placeholder()
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.BARE}
        )
        self.assertEqual(len(source_items), 1)

        CopyCollectionItems.copy_item(
            source_items[0],
            self.target_build_logs,
            user=self.user,
            variables={"additional": "foo"},
        )

        self.assertTrue(
            self.target_build_logs.child_items.filter(
                name="debian_bookworm_amd64_hello_1.0-1_1",
                child_type=CollectionItem.Types.BARE,
                category=BareDataCategory.PACKAGE_BUILD_LOG,
                collection__isnull=True,
                artifact__isnull=True,
                data={**bare_item.data, "additional": "foo"},
                removed_at__isnull=True,
            ).exists()
        )

    @context.disable_permission_checks()
    def test_copy_item_artifact(self) -> None:
        """Copy an artifact item."""
        work_request = self.playground.create_work_request(
            workspace=self.source_workspace
        )
        artifact_item = self.create_build_log(work_request=work_request)
        artifact = artifact_item.artifact
        assert artifact is not None
        build_log_file = artifact.files.get()
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.ARTIFACT}
        )
        self.assertEqual(len(source_items), 1)
        source_file_store = self.source_scope.download_file_stores(
            build_log_file
        ).get()

        CopyCollectionItems.copy_item(
            source_items[0], self.target_build_logs, user=self.user
        )

        copied_collection_item = self.target_build_logs.child_items.get(
            name=f"debian_bookworm_amd64_hello_1.0-1_{work_request.id}",
            child_type=CollectionItem.Types.ARTIFACT,
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            collection__isnull=True,
            data={
                **artifact_item.data,
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0-1",
            },
            removed_at__isnull=True,
        )
        # The artifact was copied.
        copied_artifact = copied_collection_item.artifact
        assert copied_artifact is not None
        self.assertNotEqual(copied_artifact, artifact)
        self.assertEqual(copied_artifact.category, artifact.category)
        self.assertEqual(copied_artifact.workspace, self.target_workspace)
        self.assertEqual(copied_artifact.data, artifact.data)
        self.assertEqual(
            copied_artifact.expiration_delay,
            artifact.expiration_delay,
        )
        self.assertNotEqual(copied_artifact.created_at, artifact.created_at)
        self.assertEqual(copied_artifact.created_by, artifact.created_by)
        self.assertEqual(
            copied_artifact.created_by_work_request,
            artifact.created_by_work_request,
        )
        self.assertEqual(copied_artifact.original_artifact, artifact)
        self.assertEqual(copied_artifact.files.get(), build_log_file)
        # The file in the copied artifact is in the same store.
        target_file_store = self.target_scope.upload_file_stores(
            build_log_file
        ).get()
        self.assertEqual(source_file_store, target_file_store)

    def test_copy_item_artifact_needs_unembargo(self) -> None:
        """Copying an artifact from private to public needs unembargo=True."""
        work_request = self.playground.create_work_request(
            workspace=self.source_workspace
        )
        self.create_build_log(work_request=work_request)
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.ARTIFACT}
        )
        self.assertEqual(len(source_items), 1)
        self.source_workspace.public = False
        self.source_workspace.save()

        with self.assertRaisesRegex(
            CannotCopy,
            f"Copying from {self.source_workspace} to {self.target_workspace} "
            f"requires unembargo=True",
        ):
            CopyCollectionItems.copy_item(
                source_items[0], self.target_build_logs, user=self.user
            )

    @context.disable_permission_checks()
    def test_copy_item_artifact_with_unembargo(self) -> None:
        """Copying an artifact from private to public can unembargo it."""
        work_request = self.playground.create_work_request(
            workspace=self.source_workspace
        )
        artifact_item = self.create_build_log(work_request=work_request)
        artifact = artifact_item.artifact
        assert artifact is not None
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.ARTIFACT}
        )
        self.assertEqual(len(source_items), 1)
        self.source_workspace.public = False
        self.source_workspace.save()

        CopyCollectionItems.copy_item(
            source_items[0],
            self.target_build_logs,
            user=self.user,
            unembargo=True,
        )

        copied_collection_item = self.target_build_logs.child_items.get(
            name=f"debian_bookworm_amd64_hello_1.0-1_{work_request.id}",
            child_type=CollectionItem.Types.ARTIFACT,
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            collection__isnull=True,
            data={
                **artifact_item.data,
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0-1",
            },
            removed_at__isnull=True,
        )
        # The artifact was copied.
        copied_artifact = copied_collection_item.artifact
        assert copied_artifact is not None
        self.assertEqual(copied_artifact.workspace, self.target_workspace)

    @context.disable_permission_checks()
    def test_copy_item_artifact_variables(self) -> None:
        """Pass additional variables while copying an artifact."""
        self.create_build_log(extra_data={"nested": {"foo": "bar"}})
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.ARTIFACT}
        )
        self.assertEqual(len(source_items), 1)
        target_collection = self.playground.create_collection(
            name="test",
            category=CollectionCategory.WORKFLOW_INTERNAL,
            workspace=self.target_workspace,
        )

        CopyCollectionItems.copy_item(
            source_items[0],
            target_collection,
            user=self.user,
            name_template="{vendor}_{nested_foo}",
            variables={"$nested_foo": "nested.foo"},
        )

        self.assertTrue(
            target_collection.child_items.filter(
                name="debian_bar",
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.PACKAGE_BUILD_LOG,
                collection__isnull=True,
                data={},
                removed_at__isnull=True,
            ).exists()
        )

    def test_copy_item_artifact_failed_to_expand_variables(self) -> None:
        """Fail if the given variables cannot be expanded."""
        self.create_build_log()
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.ARTIFACT}
        )
        self.assertEqual(len(source_items), 1)

        with self.assertRaisesRegex(
            CannotCopy, r"Cannot expand variables: \{'\$key': 'nonexistent'\}"
        ):
            CopyCollectionItems.copy_item(
                source_items[0],
                self.target_build_logs,
                user=self.user,
                variables={"$key": "nonexistent"},
            )

    @context.disable_permission_checks()
    def test_copy_item_artifact_incomplete_file(self) -> None:
        """We cannot copy artifacts with incomplete files."""
        artifact_item = self.create_build_log(skip_add_files_in_store=True)
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.ARTIFACT}
        )
        self.assertEqual(len(source_items), 1)

        with self.assertRaisesRegex(
            CannotCopy,
            f"Cannot copy incomplete 'hello_1.0-1_amd64.buildlog' from "
            f"artifact ID {artifact_item.artifact_id}",
        ):
            CopyCollectionItems.copy_item(
                source_items[0], self.target_build_logs, user=self.user
            )

    @context.disable_permission_checks()
    def test_copy_item_artifact_not_in_any_file_store(self) -> None:
        """We cannot copy artifacts that are not in any file store."""
        artifact_item = self.create_build_log()
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.ARTIFACT}
        )
        self.assertEqual(len(source_items), 1)
        self.source_scope.file_stores.get().files.clear()

        with self.assertRaisesRegex(
            CannotCopy,
            f"Cannot copy 'hello_1.0-1_amd64.buildlog' from artifact ID "
            f"{artifact_item.artifact_id}: not in any available file store",
        ):
            CopyCollectionItems.copy_item(
                source_items[0], self.target_build_logs, user=self.user
            )

    @context.disable_permission_checks()
    def test_copy_item_artifact_different_file_store(self) -> None:
        """
        Copy artifact file contents from a different file store.

        We copy between scopes in order to exercise the case where the
        download and upload file stores are different.
        """
        file_store = FileStore.objects.create(
            name="test",
            backend=FileStore.BackendChoices.LOCAL,
            configuration={"base_directory": settings.DEBUSINE_STORE_DIRECTORY},
        )
        self.source_scope.file_stores.set([file_store])
        work_request = self.playground.create_work_request(
            workspace=self.source_workspace
        )
        build_log_contents = b"a build log\n"
        artifact_item = self.create_build_log(
            work_request=work_request, build_log_contents=build_log_contents
        )
        artifact = artifact_item.artifact
        assert artifact is not None
        build_log_file = artifact.files.get()
        source_items = self.lookup_source_items(
            {"child_type": LookupChildType.ARTIFACT}
        )
        self.assertEqual(len(source_items), 1)
        source_file_store = self.source_scope.download_file_stores(
            build_log_file
        ).get()

        CopyCollectionItems.copy_item(
            source_items[0], self.target_build_logs, user=self.user
        )

        copied_collection_item = self.target_build_logs.child_items.get(
            name=f"debian_bookworm_amd64_hello_1.0-1_{work_request.id}",
            child_type=CollectionItem.Types.ARTIFACT,
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            collection__isnull=True,
            data={
                **artifact_item.data,
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0-1",
            },
            removed_at__isnull=True,
        )
        copied_artifact = copied_collection_item.artifact
        assert copied_artifact is not None
        self.assertNotEqual(copied_artifact, artifact)
        self.assertQuerySetEqual(copied_artifact.files.all(), [build_log_file])
        # The file in the copied artifact is in a different store.
        target_file_store = self.target_scope.upload_file_stores(
            build_log_file
        ).get()
        self.assertNotEqual(source_file_store, target_file_store)
        # The file contents were copied.
        with (
            source_file_store.get_backend_object().get_stream(
                build_log_file
            ) as source_file,
            target_file_store.get_backend_object().get_stream(
                build_log_file
            ) as target_file,
        ):
            self.assertEqual(target_file.read(), source_file.read())

    @context.disable_permission_checks()
    def test_execute(self) -> None:
        """The task copies all the given items."""
        # Partially simulate a package build in a workflow, and add the
        # resulting artifacts to appropriate source collections as starting
        # points for copies.
        template = self.playground.create_workflow_template(
            name="test", task_name="noop", workspace=self.source_workspace
        )
        workflow = self.playground.create_workflow(template, task_data={})
        assert workflow.internal_collection is not None
        source_package = self.playground.create_source_artifact(
            workspace=self.source_workspace
        )
        sbuild = self.playground.simulate_package_build(
            source_package, workflow=workflow
        )
        workflow.internal_collection.manager.add_artifact(
            sbuild.artifact_set.get(category=ArtifactCategory.BINARY_PACKAGE),
            user=sbuild.created_by,
            workflow=workflow,
            name="binary-amd64",
        )
        self.source_build_logs.manager.add_artifact(
            sbuild.artifact_set.get(
                category=ArtifactCategory.PACKAGE_BUILD_LOG
            ),
            user=sbuild.created_by,
            workflow=workflow,
            variables={
                "work_request_id": sbuild.id,
                "vendor": "debian",
                "codename": "bookworm",
                "architecture": "amd64",
            },
        )

        # Make a target suite and a task to perform the copies.
        target_suite = self.playground.create_collection(
            name="trixie",
            category=CollectionCategory.SUITE,
            workspace=self.target_workspace,
        )
        binary_package_lookup = LookupMultiple.parse_obj(
            {
                "collection": "internal@collections",
                "category": ArtifactCategory.BINARY_PACKAGE,
            }
        )
        copy_collection_items = workflow.create_child(
            task_type=TaskTypes.SERVER,
            task_name="copycollectionitems",
            task_data=CopyCollectionItemsData(
                copies=[
                    CopyCollectionItemsCopies(
                        source_items=binary_package_lookup,
                        target_collection=target_suite.id,
                        variables={
                            "component": "main",
                            "section": "python",
                            "priority": "optional",
                        },
                    ),
                    CopyCollectionItemsCopies(
                        source_items=LookupMultiple.parse_obj(
                            {
                                "collection": (
                                    f"_@{CollectionCategory.PACKAGE_BUILD_LOGS}"
                                ),
                                "lookup__same_work_request": (
                                    binary_package_lookup
                                ),
                            }
                        ),
                        target_collection=self.target_build_logs.id,
                    ),
                ]
            ),
        )
        worker = self.playground.create_worker(worker_type=WorkerType.CELERY)
        copy_collection_items.assign_worker(worker)
        task = copy_collection_items.get_task()
        assert isinstance(task, CopyCollectionItems)
        task.set_work_request(copy_collection_items)

        self.assertTrue(task.execute())

        # The target collections contain the requested items.
        self.assertTrue(
            target_suite.child_items.filter(
                name="hello_1.0-1_all",
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.BINARY_PACKAGE,
                artifact__isnull=False,
                data={
                    "srcpkg_name": "hello",
                    "srcpkg_version": "1.0-1",
                    "package": "hello",
                    "version": "1.0-1",
                    "architecture": "all",
                    "component": "main",
                    "section": "python",
                    "priority": "optional",
                },
                removed_at__isnull=True,
            ).exists()
        )
        self.assertTrue(
            self.target_build_logs.child_items.filter(
                name=f"debian_bookworm_amd64_hello_1.0-1_{sbuild.id}",
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.PACKAGE_BUILD_LOG,
                artifact__isnull=False,
                data__work_request_id=sbuild.id,
                removed_at__isnull=True,
            ).exists()
        )

    def test_get_label(self) -> None:
        """Test get_label."""
        task = CopyCollectionItems(
            {
                "copies": [
                    {
                        "source_items": {"collection": 1},
                        "target_collection": "bookworm@debian:suite",
                    },
                    {
                        "source_items": {"collection": 1},
                        "target_collection": "trixie@debian:suite",
                    },
                ]
            }
        )

        self.assertEqual(
            task.get_label(),
            "copy to bookworm@debian:suite, trixie@debian:suite",
        )
