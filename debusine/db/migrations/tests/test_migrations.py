# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the migrations."""

from typing import Any, cast

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import StateApps
from django.test import TestCase

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    TaskTypes,
)
from debusine.db.models import DEFAULT_FILE_STORE_NAME, FileStore, User


def _migrate_check_constraints(action: str, *args: Any, **kwargs: Any) -> None:
    """
    Check constraints before and after (un)applying each migration.

    When applying migrations in tests, each migration only ends with a
    savepoint rather than a full commit, so deferred constraints aren't
    immediately checked.  As a result, we need to explicitly check
    constraints to avoid "cannot ALTER TABLE ... because it has pending
    trigger events" errors in some cases.
    """
    if action in {
        "apply_start",
        "apply_success",
        "unapply_start",
        "unapply_success",
    }:
        connection.check_constraints()


LATEST_IRREVERSIBLE_MIGRATION = [("db", "0001_squashed_0_11_0")]


class MigrationTests(TestCase):
    """Test migrations."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # It seems to be faster to apply most of the migrations forwards
        # rather than backwards.  Migrate back as far as we can before
        # running this test suite.
        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(LATEST_IRREVERSIBLE_MIGRATION)

    @classmethod
    def tearDownClass(cls) -> None:
        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDownClass()

    @staticmethod
    def get_test_user_for_apps(apps: StateApps) -> User:
        """
        Return a test user.

        We may be testing a migration, so use the provided application state.
        """
        user_model = apps.get_model("db", "User")
        try:
            user = user_model.objects.get(username="usertest")
        except user_model.DoesNotExist:
            user = user_model.objects.create_user(
                username="usertest",
                password="userpassword",
                email="usertest@example.org",
            )
        return cast(User, user)

    def test_default_store_created(self) -> None:
        """Assert Default FileStore has been created."""
        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(LATEST_IRREVERSIBLE_MIGRATION)
        apps = executor.loader.project_state(LATEST_IRREVERSIBLE_MIGRATION).apps

        default_file_store = apps.get_model("db", "FileStore").objects.get(
            name=DEFAULT_FILE_STORE_NAME
        )

        self.assertEqual(
            default_file_store.backend, FileStore.BackendChoices.LOCAL
        )

        self.assertEqual(default_file_store.configuration, {})

    def test_system_workspace_created(self) -> None:
        """Assert System Workspace has been created."""
        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(LATEST_IRREVERSIBLE_MIGRATION)
        apps = executor.loader.project_state(LATEST_IRREVERSIBLE_MIGRATION).apps

        workspace = apps.get_model("db", "Workspace").objects.get(
            scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
            name=settings.DEBUSINE_DEFAULT_WORKSPACE,
        )
        default_file_store = apps.get_model("db", "FileStore").objects.get(
            name=DEFAULT_FILE_STORE_NAME
        )
        self.assertQuerySetEqual(
            workspace.scope.file_stores.all(), [default_file_store]
        )

    def assert_work_request_task_data_renamed(
        self,
        migrate_from: str,
        migrate_to: str,
        old_work_requests: list[tuple[TaskTypes, str, dict[str, Any]]],
        new_work_requests: list[tuple[TaskTypes, str, dict[str, Any]]],
    ) -> None:
        """Assert that migrations rename task data in work requests."""
        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate([("db", migrate_from)])
        old_apps = executor.loader.project_state([("db", migrate_from)]).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        for task_type, task_name, task_data in old_work_requests:
            old_apps.get_model("db", "WorkRequest").objects.create(
                created_by_id=self.get_test_user_for_apps(old_apps).id,
                task_type=task_type,
                task_name=task_name,
                workspace=workspace,
                task_data=task_data,
            )

        executor.loader.build_graph()
        executor.migrate([("db", migrate_to)])
        new_apps = executor.loader.project_state([("db", migrate_to)]).apps

        self.assertCountEqual(
            new_apps.get_model("db", "WorkRequest").objects.values_list(
                "task_type", "task_name", "task_data"
            ),
            new_work_requests,
        )

        executor.loader.build_graph()
        executor.migrate([("db", migrate_from)])

        self.assertCountEqual(
            old_apps.get_model("db", "WorkRequest").objects.values_list(
                "task_type", "task_name", "task_data"
            ),
            old_work_requests,
        )

    def assert_workflow_template_task_data_renamed(
        self,
        migrate_from: str,
        migrate_to: str,
        old_workflow_templates: list[tuple[str, dict[str, Any]]],
        new_workflow_templates: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """Assert that migrations rename task data in workflow templates."""
        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate([("db", migrate_from)])
        old_apps = executor.loader.project_state([("db", migrate_from)]).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        for i, (task_name, task_data) in enumerate(old_workflow_templates):
            old_apps.get_model("db", "WorkflowTemplate").objects.create(
                name=f"rename-test-{i}",
                workspace=workspace,
                task_name=task_name,
                task_data=task_data,
            )

        executor.loader.build_graph()
        executor.migrate([("db", migrate_to)])
        new_apps = executor.loader.project_state([("db", migrate_to)]).apps

        self.assertCountEqual(
            new_apps.get_model("db", "WorkflowTemplate").objects.values_list(
                "name", "task_name", "task_data"
            ),
            [
                (f"rename-test-{i}", task_name, task_data)
                for i, (task_name, task_data) in enumerate(
                    new_workflow_templates
                )
            ],
        )

        executor.loader.build_graph()
        executor.migrate([("db", migrate_from)])

        self.assertCountEqual(
            old_apps.get_model("db", "WorkflowTemplate").objects.values_list(
                "name", "task_name", "task_data"
            ),
            [
                (f"rename-test-{i}", task_name, task_data)
                for i, (task_name, task_data) in enumerate(
                    old_workflow_templates
                )
            ],
        )

    def test_artifact_system_components(self) -> None:
        """Test adding components to debian:system-tarball artifacts."""
        migrate_from = [("db", "0003_add_workspace_role_viewer")]
        migrate_to = [("db", "0004_system_image_components")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        OldArtifact = old_apps.get_model("db", "Artifact")
        OldWorkRequest = old_apps.get_model("db", "WorkRequest")
        wr_unrelated = OldWorkRequest.objects.create(
            created_by_id=self.get_test_user_for_apps(old_apps).id,
            task_name="noop",
            workspace=workspace,
            task_data={},
        )
        wr_without_components = OldWorkRequest.objects.create(
            created_by_id=self.get_test_user_for_apps(old_apps).id,
            task_name="mmdebstrap",
            workspace=workspace,
            task_data={
                "bootstrap_repositories": [
                    {
                        "mirror": "http://deb.debian.org/debian",
                        "suite": "unstable",
                    },
                ],
            },
        )
        wr_with_components = OldWorkRequest.objects.create(
            created_by_id=self.get_test_user_for_apps(old_apps).id,
            task_name="mmdebstrap",
            workspace=workspace,
            task_data={
                "bootstrap_repositories": [
                    {
                        "mirror": "http://deb.debian.org/debian",
                        "suite": "unstable",
                        "components": ["main", "contrib"],
                    },
                ],
            },
        )

        for data, created_by_work_request in (
            ({"components": ["foo"]}, None),
            ({}, None),
            ({}, wr_unrelated),
            ({}, wr_without_components),
            ({}, wr_with_components),
        ):
            OldArtifact.objects.create(
                category=ArtifactCategory.SYSTEM_TARBALL,
                data=data,
                created_by_work_request=created_by_work_request,
                workspace=workspace,
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)

        new_apps = executor.loader.project_state(migrate_to).apps
        NewArtifact = new_apps.get_model("db", "Artifact")

        self.assertQuerySetEqual(
            NewArtifact.objects.order_by("id").values_list(
                "data__components", flat=True
            ),
            [
                ["foo"],  # already specified
                [],
                [],
                [],
                ["main", "contrib"],  # looked up from components
            ],
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        self.assertFalse(
            OldArtifact.objects.filter(data__components__isnull=False).exists()
        )

    def test_create_archive(self) -> None:
        """``debian:archive`` is created in the default workspace."""
        migrate_from = [("db", "0009_add_archive_singleton_collection")]
        migrate_to = [("db", "0010_add_archive_singleton_collection_data")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        OldWorkspace = old_apps.get_model("db", "Workspace")
        OldCollection = old_apps.get_model("db", "Collection")
        self.assertQuerySetEqual(
            OldCollection.objects.filter(
                name="_",
                category=CollectionCategory.ARCHIVE,
                workspace__scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
                workspace__name=settings.DEBUSINE_DEFAULT_WORKSPACE,
            ),
            [],
        )

        default_workspace = OldWorkspace.objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        other_workspace = OldWorkspace.objects.create(
            scope=default_workspace.scope, name="other"
        )
        OldCollection.objects.create(
            name="bookworm",
            category=CollectionCategory.SUITE,
            workspace=default_workspace,
        )
        OldCollection.objects.create(
            name="other",
            category=CollectionCategory.SUITE,
            workspace=other_workspace,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        new_archive = new_apps.get_model("db", "Collection").objects.get(
            name="_",
            category=CollectionCategory.ARCHIVE,
            workspace__scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
            workspace__name=settings.DEBUSINE_DEFAULT_WORKSPACE,
        )
        self.assertQuerySetEqual(
            new_archive.child_items.values_list(
                "name", "parent_category", "category", "collection__name"
            ),
            [
                (
                    "bookworm",
                    CollectionCategory.ARCHIVE,
                    CollectionCategory.SUITE,
                    "bookworm",
                )
            ],
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        self.assertQuerySetEqual(
            OldCollection.objects.filter(
                name="_",
                category=CollectionCategory.ARCHIVE,
                workspace__scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
                workspace__name=settings.DEBUSINE_DEFAULT_WORKSPACE,
            ),
            [],
        )

    def test_qa_suite_rename(self) -> None:
        """Various workflow data fields are renamed to ``qa_suite``."""
        self.assert_work_request_task_data_renamed(
            migrate_from=(
                "0012_alter_collectionitem_created_by_workflow_and_more"
            ),
            migrate_to="0013_qa_suite",
            old_work_requests=[
                (
                    TaskTypes.WORKFLOW,
                    "foo",
                    {
                        "suite_collection": "bookworm@debian:suite",
                        "reverse_dependencies_autopkgtest_suite": (
                            "bookworm@debian:suite"
                        ),
                    },
                ),
                (
                    TaskTypes.WORKFLOW,
                    "reverse_dependencies_autopkgtest",
                    {"suite_collection": "bookworm@debian:suite"},
                ),
                (
                    TaskTypes.WORKFLOW,
                    "qa",
                    {
                        "reverse_dependencies_autopkgtest_suite": (
                            "bookworm@debian:suite"
                        )
                    },
                ),
                (
                    TaskTypes.WORKFLOW,
                    "debian_pipeline",
                    {
                        "reverse_dependencies_autopkgtest_suite": (
                            "bookworm@debian:suite"
                        )
                    },
                ),
            ],
            new_work_requests=[
                (
                    TaskTypes.WORKFLOW,
                    "foo",
                    {
                        "suite_collection": "bookworm@debian:suite",
                        "reverse_dependencies_autopkgtest_suite": (
                            "bookworm@debian:suite"
                        ),
                    },
                ),
                (
                    TaskTypes.WORKFLOW,
                    "reverse_dependencies_autopkgtest",
                    {"qa_suite": "bookworm@debian:suite"},
                ),
                (
                    TaskTypes.WORKFLOW,
                    "qa",
                    {"qa_suite": "bookworm@debian:suite"},
                ),
                (
                    TaskTypes.WORKFLOW,
                    "debian_pipeline",
                    {"qa_suite": "bookworm@debian:suite"},
                ),
            ],
        )
        self.assert_workflow_template_task_data_renamed(
            migrate_from=(
                "0012_alter_collectionitem_created_by_workflow_and_more"
            ),
            migrate_to="0013_qa_suite",
            old_workflow_templates=[
                (
                    "foo",
                    {
                        "suite_collection": "bookworm@debian:suite",
                        "reverse_dependencies_autopkgtest_suite": (
                            "bookworm@debian:suite"
                        ),
                    },
                ),
                (
                    "reverse_dependencies_autopkgtest",
                    {"suite_collection": "bookworm@debian:suite"},
                ),
                (
                    "qa",
                    {
                        "reverse_dependencies_autopkgtest_suite": (
                            "bookworm@debian:suite"
                        )
                    },
                ),
                (
                    "debian_pipeline",
                    {
                        "reverse_dependencies_autopkgtest_suite": (
                            "bookworm@debian:suite"
                        )
                    },
                ),
            ],
            new_workflow_templates=[
                (
                    "foo",
                    {
                        "suite_collection": "bookworm@debian:suite",
                        "reverse_dependencies_autopkgtest_suite": (
                            "bookworm@debian:suite"
                        ),
                    },
                ),
                (
                    "reverse_dependencies_autopkgtest",
                    {"qa_suite": "bookworm@debian:suite"},
                ),
                ("qa", {"qa_suite": "bookworm@debian:suite"}),
                ("debian_pipeline", {"qa_suite": "bookworm@debian:suite"}),
            ],
        )
