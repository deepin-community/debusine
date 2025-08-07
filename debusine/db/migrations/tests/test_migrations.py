# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the migrations."""

import hashlib
from datetime import timedelta
from typing import Any, cast

from django.conf import settings
from django.contrib.auth.hashers import is_password_usable
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import StateApps
from django.test import TestCase
from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
)
from debusine.assets import KeyPurpose
from debusine.client.models import LookupChildType
from debusine.db.models import (
    DEFAULT_FILE_STORE_NAME,
    FileStore,
    Group,
    SYSTEM_USER_NAME,
    User,
    WorkRequest,
)
from debusine.db.models.collections import (
    _CollectionItemTypes,
    _CollectionRetainsArtifacts,
)
from debusine.db.tests.utils import _calculate_hash_from_data
from debusine.tasks.models import TaskTypes, WorkerType


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


LATEST_IRREVERSIBLE_MIGRATION = [("db", "0013_expiration_delay")]


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
        migrate_to = [("db", "0120_scope_file_stores_data")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_to)
        apps = executor.loader.project_state(migrate_to).apps

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

    def test_artifact_underscore_names(self) -> None:
        """Artifacts have their data migrated to new names."""
        migrate_from = [("db", "0013_expiration_delay")]
        migrate_to = [("db", "0014_artifact_underscore_names")]

        # Several of the category/data combinations here wouldn't appear in
        # practice, but are here to ensure full coverage of the data
        # migration code.
        old_artifacts = [
            (ArtifactCategory.WORK_REQUEST_DEBUG_LOGS, {}),
            (ArtifactCategory.PACKAGE_BUILD_LOG, {"source": "hello"}),
            (ArtifactCategory.UPLOAD, {"type": "dpkg"}),
            (
                ArtifactCategory.UPLOAD,
                {"type": "dpkg", "changes-fields": {"Source": "hello"}},
            ),
            (
                ArtifactCategory.UPLOAD,
                {"type": "dpkg", "changes-fields": {"Source": "coreutils"}},
            ),
            (
                ArtifactCategory.SOURCE_PACKAGE,
                {
                    "name": "hello",
                    "version": "1",
                    "type": "dpkg",
                    "dsc-fields": {"Source": "hello"},
                },
            ),
            (ArtifactCategory.BINARY_PACKAGE, {"srcpkg-name": "hello"}),
            (
                ArtifactCategory.BINARY_PACKAGE,
                {
                    "srcpkg-name": "hello",
                    "srcpkg-version": "1",
                    "deb-fields": {"Package": "hello"},
                    "deb-control-files": ["control"],
                },
            ),
            (
                ArtifactCategory.BINARY_PACKAGES,
                {
                    "srcpkg-name": "hello",
                    "srcpkg-version": "1",
                    "version": "1",
                    "architecture": "amd64",
                    "packages": ["hello"],
                },
            ),
        ]
        new_artifacts = [
            (ArtifactCategory.WORK_REQUEST_DEBUG_LOGS, {}),
            (ArtifactCategory.PACKAGE_BUILD_LOG, {"source": "hello"}),
            (ArtifactCategory.UPLOAD, {"type": "dpkg"}),
            (
                ArtifactCategory.UPLOAD,
                {"type": "dpkg", "changes_fields": {"Source": "hello"}},
            ),
            (
                ArtifactCategory.UPLOAD,
                {"type": "dpkg", "changes_fields": {"Source": "coreutils"}},
            ),
            (
                ArtifactCategory.SOURCE_PACKAGE,
                {
                    "name": "hello",
                    "version": "1",
                    "type": "dpkg",
                    "dsc_fields": {"Source": "hello"},
                },
            ),
            (ArtifactCategory.BINARY_PACKAGE, {"srcpkg_name": "hello"}),
            (
                ArtifactCategory.BINARY_PACKAGE,
                {
                    "srcpkg_name": "hello",
                    "srcpkg_version": "1",
                    "deb_fields": {"Package": "hello"},
                    "deb_control_files": ["control"],
                },
            ),
            (
                ArtifactCategory.BINARY_PACKAGES,
                {
                    "srcpkg_name": "hello",
                    "srcpkg_version": "1",
                    "version": "1",
                    "architecture": "amd64",
                    "packages": ["hello"],
                },
            ),
        ]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        for category, data in old_artifacts:
            old_apps.get_model("db", "Artifact").objects.create(
                category=category, workspace=workspace, data=data
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        self.assertCountEqual(
            [
                (artifact.category, artifact.data)
                for artifact in new_apps.get_model(
                    "db", "Artifact"
                ).objects.all()
            ],
            new_artifacts,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        self.assertCountEqual(
            [
                (artifact.category, artifact.data)
                for artifact in old_apps.get_model(
                    "db", "Artifact"
                ).objects.all()
            ],
            old_artifacts,
        )

    def assert_work_request_task_data_renamed(
        self,
        migrate_from: str,
        migrate_to: str,
        old_work_requests: list[tuple[str, dict[str, Any]]],
        new_work_requests: list[tuple[str, dict[str, Any]]],
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
        for task_name, task_data in old_work_requests:
            old_apps.get_model("db", "WorkRequest").objects.create(
                created_by_id=self.get_test_user_for_apps(old_apps).id,
                task_name=task_name,
                workspace=workspace,
                task_data=task_data,
            )

        executor.loader.build_graph()
        executor.migrate([("db", migrate_to)])
        new_apps = executor.loader.project_state([("db", migrate_to)]).apps

        self.assertCountEqual(
            [
                (request.task_name, request.task_data)
                for request in new_apps.get_model(
                    "db", "WorkRequest"
                ).objects.all()
            ],
            new_work_requests,
        )

        executor.loader.build_graph()
        executor.migrate([("db", migrate_from)])

        self.assertCountEqual(
            [
                (request.task_name, request.task_data)
                for request in old_apps.get_model(
                    "db", "WorkRequest"
                ).objects.all()
            ],
            old_work_requests,
        )

    def test_autopkgtest_work_request_architecture_rename(self) -> None:
        """Work requests for autopkgtest rename their architecture field."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0018_workrequest_worker_blank",
            migrate_to="0019_autopkgtest_task_data",
            old_work_requests=[
                ("foo", {"architecture": "amd64"}),
                ("autopkgtest", {"architecture": "amd64"}),
            ],
            new_work_requests=[
                ("foo", {"architecture": "amd64"}),
                ("autopkgtest", {"host_architecture": "amd64"}),
            ],
        )

    def test_hash_added(self) -> None:
        """Assert token hash has been added."""
        migrate_from = [
            (
                'db',
                '0022_collectionitem_db_collectionitem_unique_debian_environment',  # noqa: E501
            )
        ]
        migrate_to = [('db', '0025_token_hash_final')]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        Token = old_apps.get_model('db', 'Token')
        old_token = Token.objects.create(
            comment="Test token",
        )
        self.assertIsNotNone(old_token.key)
        with self.assertRaises(AttributeError):
            old_token.hash

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        def _generate_hash(secret: str) -> str:
            return hashlib.sha256(secret.encode()).hexdigest()

        Token = new_apps.get_model('db', 'Token')
        new_token = Token.objects.get(hash=_generate_hash(old_token.key))

        with self.assertRaises(AttributeError):
            new_token.key

        self.assertIsNotNone(new_token)
        self.assertEqual(new_token.hash, _generate_hash(old_token.key))

    def test_hash_migration_irreversible(self) -> None:
        """Assert token hash migration is irreversible if there are tokens."""
        migrate_from = [('db', '0025_token_hash_final')]
        migrate_to = [('db', '0023_token_hash_initial')]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        Token = old_apps.get_model('db', 'Token')
        Token.objects.create(
            comment="Test token",
        )

        with self.assertRaises(IrreversibleError):
            executor.loader.build_graph()
            executor.migrate(migrate_to)

    def test_autopkgtest_work_request_environment_rename(self) -> None:
        """Work requests for autopkgtest rename their environment field."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0034_workrequest_pydantic",
            migrate_to="0035_autopkgtest_extra_environment",
            old_work_requests=[
                ("foo", {"environment": {"FOO": "foo", "BAR": "bar"}}),
                ("autopkgtest", {"environment": {"FOO": "foo", "BAR": "bar"}}),
            ],
            new_work_requests=[
                ("foo", {"environment": {"FOO": "foo", "BAR": "bar"}}),
                (
                    "autopkgtest",
                    {"extra_environment": {"FOO": "foo", "BAR": "bar"}},
                ),
            ],
        )

    def test_fix_task_type(self) -> None:
        """Work requests have their task_type fixed."""
        migrate_from = [("db", "0035_autopkgtest_extra_environment")]
        migrate_to = [("db", "0036_workrequest_fix_task_type")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        for task_type in ("worker", "server", "internal", "workflow"):
            old_apps.get_model("db", "WorkRequest").objects.create(
                workspace=workspace,
                created_by_id=self.get_test_user_for_apps(old_apps).id,
                task_type=task_type,
                task_name="noop",
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        self.assertEqual(
            [
                wr.task_type
                for wr in new_apps.get_model(
                    "db", "WorkRequest"
                ).objects.order_by("id")
            ],
            [
                TaskTypes.WORKER,
                TaskTypes.SERVER,
                TaskTypes.INTERNAL,
                TaskTypes.WORKFLOW,
            ],
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        self.assertEqual(
            [
                wr.task_type
                for wr in new_apps.get_model(
                    "db", "WorkRequest"
                ).objects.order_by("id")
            ],
            ["worker", "server", "internal", "workflow"],
        )

    def test_environment_id_rename(self) -> None:
        """Work requests rename their environment_id field."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0038_alter_workrequest_parent",
            migrate_to="0039_rename_environment_id",
            old_work_requests=[
                ("autopkgtest", {"environment_id": 1}),
                ("lintian", {"environment_id": 1}),
                ("piuparts", {"environment_id": 1}),
                ("sbuild", {"backend": "schroot", "distribution": "bookworm"}),
                ("sbuild", {"backend": "unshare", "environment_id": 1}),
            ],
            new_work_requests=[
                ("autopkgtest", {"environment": 1}),
                ("lintian", {"environment": 1}),
                ("piuparts", {"environment": 1}),
                ("sbuild", {"backend": "schroot", "distribution": "bookworm"}),
                ("sbuild", {"backend": "unshare", "environment": 1}),
            ],
        )

    def test_blhc_artifact_id_rename(self) -> None:
        """Work requests for blhc rename their artifact_id field."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0039_rename_environment_id",
            migrate_to="0040_blhc_lookup",
            old_work_requests=[
                ("foo", {"input": {"artifact_id": 1}}),
                ("blhc", {"input": {}}),
                ("blhc", {"output": {"source_analysis": False}}),
                ("blhc", {"input": {"artifact_id": 1}}),
            ],
            new_work_requests=[
                ("foo", {"input": {"artifact_id": 1}}),
                ("blhc", {"input": {}}),
                ("blhc", {"output": {"source_analysis": False}}),
                ("blhc", {"input": {"artifact": 1}}),
            ],
        )

    def test_piuparts_binary_artifacts_ids_rename(self) -> None:
        """Work requests for piuparts rename binary_artifacts_ids."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0040_blhc_lookup",
            migrate_to="0041_piuparts_lookup",
            old_work_requests=[
                ("foo", {"input": {"binary_artifacts_ids": [1, 2]}}),
                ("piuparts", {"input": {}}),
                ("piuparts", {"backend": "incus-lxc"}),
                ("piuparts", {"input": {"binary_artifacts_ids": [1, 2]}}),
            ],
            new_work_requests=[
                ("foo", {"input": {"binary_artifacts_ids": [1, 2]}}),
                ("piuparts", {"input": {}}),
                ("piuparts", {"backend": "incus-lxc"}),
                ("piuparts", {"input": {"binary_artifacts": [1, 2]}}),
            ],
        )

    def test_piuparts_base_tgz_id_rename(self) -> None:
        """Work requests for piuparts rename base_tgz_id."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0040_blhc_lookup",
            migrate_to="0041_piuparts_lookup",
            old_work_requests=[
                ("foo", {"base_tgz_id": 1}),
                ("piuparts", {"backend": "incus-lxc"}),
                ("piuparts", {"base_tgz_id": 1}),
            ],
            new_work_requests=[
                ("foo", {"base_tgz_id": 1}),
                ("piuparts", {"backend": "incus-lxc"}),
                ("piuparts", {"base_tgz": 1}),
            ],
        )

    def test_create_system_user(self) -> None:
        """A system user is created."""
        migrate_from = [("db", "0042_user_is_system")]
        migrate_to = [("db", "0043_create_system_user")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        self.assertFalse(
            old_apps.get_model("db", "User")
            .objects.filter(username=SYSTEM_USER_NAME)
            .exists()
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        system_user = new_apps.get_model("db", "User").objects.get(
            username=SYSTEM_USER_NAME
        )
        self.assertFalse(is_password_usable(system_user.password))
        self.assertEqual(system_user.first_name, "Debusine")
        self.assertEqual(system_user.last_name, "System")
        self.assertTrue(system_user.is_system)

        executor.loader.build_graph()
        executor.migrate(migrate_from)
        self.assertFalse(
            old_apps.get_model("db", "User")
            .objects.filter(username=SYSTEM_USER_NAME)
            .exists()
        )

    def test_autopkgtest_source_artifact_id_rename(self) -> None:
        """Work requests for autopkgtest rename source_artifact_id."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0044_collection_add_retains_artifacts",
            migrate_to="0045_autopkgtest_lookup",
            old_work_requests=[
                ("foo", {"input": {"source_artifact_id": 1}}),
                ("autopkgtest", {"input": {}}),
                ("autopkgtest", {"host_architecture": "amd64"}),
                ("autopkgtest", {"input": {"source_artifact_id": 1}}),
            ],
            new_work_requests=[
                ("foo", {"input": {"source_artifact_id": 1}}),
                ("autopkgtest", {"input": {}}),
                ("autopkgtest", {"host_architecture": "amd64"}),
                ("autopkgtest", {"input": {"source_artifact": 1}}),
            ],
        )

    def test_autopkgtest_binary_artifacts_ids_rename(self) -> None:
        """Work requests for autopkgtest rename binary_artifacts_ids."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0044_collection_add_retains_artifacts",
            migrate_to="0045_autopkgtest_lookup",
            old_work_requests=[
                ("foo", {"input": {"binary_artifacts_ids": [1, 2]}}),
                ("autopkgtest", {"input": {}}),
                ("autopkgtest", {"host_architecture": "amd64"}),
                ("autopkgtest", {"input": {"binary_artifacts_ids": [1, 2]}}),
            ],
            new_work_requests=[
                ("foo", {"input": {"binary_artifacts_ids": [1, 2]}}),
                ("autopkgtest", {"input": {}}),
                ("autopkgtest", {"host_architecture": "amd64"}),
                ("autopkgtest", {"input": {"binary_artifacts": [1, 2]}}),
            ],
        )

    def test_autopkgtest_context_artifacts_ids_rename(self) -> None:
        """Work requests for autopkgtest rename context_artifacts_ids."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0044_collection_add_retains_artifacts",
            migrate_to="0045_autopkgtest_lookup",
            old_work_requests=[
                ("foo", {"input": {"context_artifacts_ids": [1, 2]}}),
                ("autopkgtest", {"input": {}}),
                ("autopkgtest", {"host_architecture": "amd64"}),
                ("autopkgtest", {"input": {"context_artifacts_ids": [1, 2]}}),
            ],
            new_work_requests=[
                ("foo", {"input": {"context_artifacts_ids": [1, 2]}}),
                ("autopkgtest", {"input": {}}),
                ("autopkgtest", {"host_architecture": "amd64"}),
                ("autopkgtest", {"input": {"context_artifacts": [1, 2]}}),
            ],
        )

    def test_lintian_source_artifact_id_rename(self) -> None:
        """Work requests for lintian rename source_artifact_id."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0045_autopkgtest_lookup",
            migrate_to="0046_lintian_lookup",
            old_work_requests=[
                ("foo", {"input": {"source_artifact_id": 1}}),
                ("lintian", {"input": {}}),
                ("lintian", {"backend": "incus-lxc"}),
                ("lintian", {"input": {"source_artifact_id": 1}}),
            ],
            new_work_requests=[
                ("foo", {"input": {"source_artifact_id": 1}}),
                ("lintian", {"input": {}}),
                ("lintian", {"backend": "incus-lxc"}),
                ("lintian", {"input": {"source_artifact": 1}}),
            ],
        )

    def test_lintian_binary_artifacts_ids_rename(self) -> None:
        """Work requests for lintian rename binary_artifacts_ids."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0045_autopkgtest_lookup",
            migrate_to="0046_lintian_lookup",
            old_work_requests=[
                ("foo", {"input": {"binary_artifacts_ids": [1, 2]}}),
                ("lintian", {"input": {}}),
                ("lintian", {"backend": "incus-lxc"}),
                ("lintian", {"input": {"binary_artifacts_ids": [1, 2]}}),
            ],
            new_work_requests=[
                ("foo", {"input": {"binary_artifacts_ids": [1, 2]}}),
                ("lintian", {"input": {}}),
                ("lintian", {"backend": "incus-lxc"}),
                ("lintian", {"input": {"binary_artifacts": [1, 2]}}),
            ],
        )

    def test_sbuild_source_artifact_id_rename(self) -> None:
        """Work requests for sbuild rename source_artifact_id."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0046_lintian_lookup",
            migrate_to="0047_sbuild_lookup",
            old_work_requests=[
                ("foo", {"input": {"source_artifact_id": 1}}),
                ("sbuild", {"input": {}}),
                ("sbuild", {"host_architecture": "amd64"}),
                ("sbuild", {"input": {"source_artifact_id": 1}}),
            ],
            new_work_requests=[
                ("foo", {"input": {"source_artifact_id": 1}}),
                ("sbuild", {"input": {}}),
                ("sbuild", {"host_architecture": "amd64"}),
                ("sbuild", {"input": {"source_artifact": 1}}),
            ],
        )

    def test_sbuild_extra_binary_artifacts_ids_rename(self) -> None:
        """Work requests for sbuild rename extra_binary_artifact_ids."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0046_lintian_lookup",
            migrate_to="0047_sbuild_lookup",
            old_work_requests=[
                ("foo", {"input": {"extra_binary_artifact_ids": [1, 2]}}),
                ("sbuild", {"input": {}}),
                ("sbuild", {"host_architecture": "amd64"}),
                ("sbuild", {"input": {"extra_binary_artifact_ids": [1, 2]}}),
            ],
            new_work_requests=[
                ("foo", {"input": {"extra_binary_artifact_ids": [1, 2]}}),
                ("sbuild", {"input": {}}),
                ("sbuild", {"host_architecture": "amd64"}),
                ("sbuild", {"input": {"extra_binary_artifacts": [1, 2]}}),
            ],
        )

    def test_updatesuitelintiancollection_base_collection_id_rename(
        self,
    ) -> None:
        """Work Requests for USLC rename base_collection_id."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0048_alter_collection_retains_artifacts",
            migrate_to="0049_update_suite_lintian_collection_lookup",
            old_work_requests=[
                ("foo", {"base_collection_id": 1}),
                ("updatesuitelintiancollection", {"force": True}),
                ("updatesuitelintiancollection", {"base_collection_id": 1}),
            ],
            new_work_requests=[
                ("foo", {"base_collection_id": 1}),
                ("updatesuitelintiancollection", {"force": True}),
                ("updatesuitelintiancollection", {"base_collection": 1}),
            ],
        )

    def test_updatesuitelintiancollection_derived_collection_id_rename(
        self,
    ) -> None:
        """Work requests for USLC rename derived_collection_id."""
        self.assert_work_request_task_data_renamed(
            migrate_from="0048_alter_collection_retains_artifacts",
            migrate_to="0049_update_suite_lintian_collection_lookup",
            old_work_requests=[
                ("foo", {"derived_collection_id": 1}),
                ("updatesuitelintiancollection", {"force": True}),
                ("updatesuitelintiancollection", {"derived_collection_id": 1}),
            ],
            new_work_requests=[
                ("foo", {"derived_collection_id": 1}),
                ("updatesuitelintiancollection", {"force": True}),
                ("updatesuitelintiancollection", {"derived_collection": 1}),
            ],
        )

    def test_collection_retains_artifacts_workflow(self) -> None:
        """The Collection.{retains_artifacts,workflow} migration works."""
        migrate_from = [("db", "0049_update_suite_lintian_collection_lookup")]
        migrate_to = [("db", "0052_collection_workflow_final")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        Collection = old_apps.get_model("db", "Collection")
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        Collection.objects.create(
            name="test",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=workspace,
            retains_artifacts=True,
        )
        workflow = old_apps.get_model("db", "WorkRequest").objects.create(
            workspace=workspace,
            created_by_id=self.get_test_user_for_apps(old_apps).id,
            task_type=TaskTypes.WORKFLOW,
            task_name="noop",
        )
        Collection.objects.create(
            name=f"workflow-{workflow.id}",
            category=CollectionCategory.WORKFLOW_INTERNAL,
            workspace=workspace,
            retains_artifacts=False,
        )
        Collection.objects.create(
            name="test",
            category=CollectionCategory.WORKFLOW_INTERNAL,
            workspace=workspace,
            retains_artifacts=False,
        )
        Collection.objects.create(
            name="test",
            category="debusine:test",
            workspace=workspace,
            retains_artifacts=False,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        self.assertEqual(
            [
                (collection.retains_artifacts, collection.workflow)
                for collection in new_apps.get_model(
                    "db", "Collection"
                ).objects.order_by("id")
            ],
            [
                (_CollectionRetainsArtifacts.ALWAYS, None),
                (
                    _CollectionRetainsArtifacts.WORKFLOW,
                    new_apps.get_model("db", "WorkRequest").objects.get(
                        id=workflow.id
                    ),
                ),
                (_CollectionRetainsArtifacts.NEVER, None),
                (_CollectionRetainsArtifacts.NEVER, None),
            ],
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)
        new_apps = executor.loader.project_state(migrate_to).apps

        self.assertEqual(
            [
                collection.retains_artifacts
                for collection in old_apps.get_model(
                    "db", "Collection"
                ).objects.order_by("id")
            ],
            [True, False, False, False],
        )

    def test_internalnoop_rename(self) -> None:
        """`internalnoop` work requests are renamed to `servernoop`."""
        migrate_from = [("db", "0061_workrequest_supersedes")]
        migrate_to = [("db", "0062_rename_internalnoop")]
        old_work_requests = [
            (TaskTypes.WORKER, "internalnoop", {"result": True}),
            (TaskTypes.SERVER, "noop", {"result": True}),
            (TaskTypes.SERVER, "internalnoop", {"result": True}),
            (TaskTypes.SERVER, "internalnoop", {"result": False}),
        ]
        new_work_requests = [
            (TaskTypes.WORKER, "internalnoop", {"result": True}),
            (TaskTypes.SERVER, "noop", {"result": True}),
            (TaskTypes.SERVER, "servernoop", {"result": True}),
            (TaskTypes.SERVER, "servernoop", {"result": False}),
        ]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        for task_type, task_name, task_data in old_work_requests:
            old_apps.get_model("db", "WorkRequest").objects.create(
                workspace=workspace,
                created_by_id=self.get_test_user_for_apps(old_apps).id,
                task_type=task_type,
                task_name=task_name,
                task_data=task_data,
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        self.assertCountEqual(
            [
                (request.task_type, request.task_name, request.task_data)
                for request in new_apps.get_model(
                    "db", "WorkRequest"
                ).objects.all()
            ],
            new_work_requests,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        self.assertCountEqual(
            [
                (request.task_type, request.task_name, request.task_data)
                for request in old_apps.get_model(
                    "db", "WorkRequest"
                ).objects.all()
            ],
            old_work_requests,
        )

    def test_worker_worker_type(self) -> None:
        """The Worker.internal to Worker.worker_type migration works."""
        migrate_from = [("db", "0062_rename_internalnoop")]
        migrate_to = [("db", "0065_worker_worker_type_final")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps

        def _generate_hash(secret: str) -> str:
            return hashlib.sha256(secret.encode()).hexdigest()

        Worker = old_apps.get_model("db", "Worker")
        Token = old_apps.get_model("db", "Token")
        for i in range(2):
            Worker.objects.create(
                name=f"worker-{i}",
                token=Token.objects.create(hash=_generate_hash(str(i))),
                registered_at=timezone.now(),
            )
        Worker.objects.create(
            name="celery", internal=True, registered_at=timezone.now()
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        self.assertEqual(
            [
                worker.worker_type
                for worker in new_apps.get_model(
                    "db", "Worker"
                ).objects.order_by("id")
            ],
            [WorkerType.EXTERNAL, WorkerType.EXTERNAL, WorkerType.CELERY],
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        self.assertEqual(
            [
                worker.internal
                for worker in old_apps.get_model(
                    "db", "Worker"
                ).objects.order_by("id")
            ],
            [False, False, True],
        )

    def test_collectionitem_parent_category(self) -> None:
        """Populating `CollectionItem.parent_category` works."""
        migrate_from = [("db", "0068_collection_workflow_onetoone")]
        migrate_to = [("db", "0071_collectionitem_parent_category_final")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        collection_test = old_apps.get_model("db", "Collection").objects.create(
            name="test", category=CollectionCategory.TEST, workspace=workspace
        )
        collection_environments = old_apps.get_model(
            "db", "Collection"
        ).objects.create(
            name="test",
            category=CollectionCategory.ENVIRONMENTS,
            workspace=workspace,
        )
        for collection, name, data in (
            (collection_test, "1", {}),
            (collection_test, "2", {}),
            (collection_environments, "3", {"codename": "bookworm"}),
            (collection_environments, "4", {"codename": "trixie"}),
        ):
            old_apps.get_model("db", "CollectionItem").objects.create(
                parent_collection=collection,
                name=name,
                child_type=_CollectionItemTypes.BARE,
                category=BareDataCategory.TEST,
                data=data,
                created_by_user=self.get_test_user_for_apps(old_apps),
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        new_items = new_apps.get_model("db", "CollectionItem").objects.all()
        self.assertEqual(len(new_items), 4)
        for item in new_items:
            self.assertEqual(
                item.parent_category, item.parent_collection.category
            )

        executor.loader.build_graph()
        executor.migrate(
            [("db", "0069_collectionitem_parent_category_initial")]
        )
        pre_data_migration_apps = executor.loader.project_state(migrate_to).apps

        pre_data_migration_items = pre_data_migration_apps.get_model(
            "db", "CollectionItem"
        ).objects.all()
        self.assertEqual(len(pre_data_migration_items), 4)
        for item in pre_data_migration_items:
            self.assertIsNone(item.parent_category)

        executor.loader.build_graph()
        # Just make sure that this doesn't raise an exception.
        executor.migrate(migrate_from)

    def test_fileinartifact_complete(self) -> None:
        """Populating `FileInArtifact.complete` works."""
        migrate_from = [("db", "0073_token_last_seen_at")]
        migrate_to = [("db", "0076_fileinartifact_complete_final")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        old_db_workspace = old_apps.get_model("db", "Workspace")

        # The default workspace already exists.
        default_workspace = old_db_workspace.objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        # Create two other workspaces that share a file store.
        file_store = old_apps.get_model("db", "FileStore").objects.create(
            name="test",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={},
        )
        workspace_1 = old_db_workspace.objects.create(
            default_file_store=file_store, name="test-1"
        )
        workspace_2 = old_db_workspace.objects.create(
            default_file_store=file_store, name="test-2"
        )
        # Create some artifacts and files in each workspace.
        for workspace, path, create_in_store in (
            (default_workspace, "test1", True),
            (default_workspace, "test2", True),
            (default_workspace, "test3", False),
            (workspace_1, "test4", True),
            (workspace_1, "test5", True),
            (workspace_2, "test6", True),
            (workspace_2, "test7", True),
        ):
            artifact = old_apps.get_model("db", "Artifact").objects.create(
                category=ArtifactCategory.TEST, workspace=workspace, data={}
            )
            hash_digest = _calculate_hash_from_data(path.encode())
            file = old_apps.get_model("db", "File").objects.create(
                sha256=hash_digest, size=len(path.encode())
            )
            if create_in_store:
                old_apps.get_model("db", "FileInStore").objects.create(
                    store=workspace.default_file_store, file=file, data={}
                )
            old_apps.get_model("db", "FileInArtifact").objects.create(
                artifact=artifact, path=path, file=file
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        new_fias = new_apps.get_model("db", "FileInArtifact").objects.all()
        self.assertEqual(new_fias.count(), 7)
        self.assertEqual(
            [fia.complete for fia in new_fias],
            [
                # The first two exist in a file store only used by one
                # workspace, and so are complete.
                True,
                True,
                # The third was not uploaded to any store, and so is not
                # complete.
                False,
                # The last four are in file stores that are shared between
                # workspaces, and so cannot safely be considered complete.
                False,
                False,
                False,
                False,
            ],
        )

        executor.loader.build_graph()
        executor.migrate([("db", "0074_fileinartifact_complete_initial")])
        pre_data_migration_apps = executor.loader.project_state(migrate_to).apps

        pre_data_migration_fias = pre_data_migration_apps.get_model(
            "db", "FileInArtifact"
        ).objects.all()
        self.assertEqual(pre_data_migration_fias.count(), 7)
        for fia in pre_data_migration_fias:
            self.assertIsNone(fia.complete)

        executor.loader.build_graph()
        # Just make sure that this doesn't raise an exception.
        executor.migrate(migrate_from)

    def test_sbuild_binnmu_build_components(self) -> None:
        """Updating build_components of sbuild tasks works."""
        migrate_from = [
            ("db", "0078_collectionitem_add_created_removed_by_workflow")
        ]
        migrate_to = [("db", "0079_sbuild_binnmu_build_components")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        old_work_request_model = old_apps.get_model("db", "WorkRequest")
        old_workspace_model = old_apps.get_model("db", "Workspace")
        default_workspace = old_workspace_model.objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        test_user = self.get_test_user_for_apps(old_apps)
        expected_build_components = []
        for build_components, binnmu, expected in (
            (["all", "any"], None, ["all", "any"]),
            (["all"], None, ["all"]),
            (["any"], None, ["any"]),
            (["all", "any"], {"suffix": "+b1"}, ["any"]),
            # Doesn't make any sense, but parseable:
            (["all"], {"suffix": "+b1"}, []),
            (["any"], {"suffix": "+b1"}, ["any"]),
        ):
            task_data: dict[str, Any] = {
                "build_components": build_components,
            }
            if binnmu is not None:
                task_data["binnmu"] = binnmu
            old_work_request_model.objects.create(
                task_name="sbuild",
                created_by_id=test_user.id,
                workspace=default_workspace,
                task_data=task_data,
            )
            expected_build_components.append(expected)

        executor.loader.build_graph()
        executor.migrate(migrate_to)

        new_apps = executor.loader.project_state(migrate_to).apps
        new_work_requests = new_apps.get_model(
            "db", "WorkRequest"
        ).objects.all()
        build_components = []
        for work_request in new_work_requests:
            if work_request.task_data.get("binnmu", None):
                self.assertNotIn(
                    "all", work_request.task_data["build_components"]
                )
            build_components.append(work_request.task_data["build_components"])
        self.assertEqual(build_components, expected_build_components)

        # Just make sure that this doesn't raise an exception.
        executor.migrate(migrate_from)

    def test_scoping_workspaces(self) -> None:
        """Introducing scopes for workspaces."""
        migrate_from = [
            ("db", "0079_sbuild_binnmu_build_components"),
        ]
        migrate_to = [("db", "0084_workspace_namespace_by_scope")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        old_file_store_model = old_apps.get_model("db", "FileStore")
        old_workspace_model = old_apps.get_model("db", "Workspace")
        old_default_workspace = old_workspace_model.objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        old_default_file_store = old_file_store_model.objects.get(
            name=DEFAULT_FILE_STORE_NAME
        )
        old_workspace = old_workspace_model.objects.create(
            name="test-migration",
            default_file_store=old_default_file_store,
        )
        executor.loader.build_graph()
        executor.migrate(migrate_to)

        new_apps = executor.loader.project_state(migrate_to).apps
        scope_model = new_apps.get_model("db", "Scope")
        new_workspace_model = new_apps.get_model("db", "Workspace")
        fallback_scope = scope_model.objects.get(name="debusine")
        new_default_workspace = new_workspace_model.objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        new_workspace = new_workspace_model.objects.get(name="test-migration")

        self.assertEqual(old_default_workspace.pk, new_default_workspace.pk)
        self.assertEqual(old_default_workspace.name, new_default_workspace.name)
        self.assertEqual(old_workspace.pk, new_workspace.pk)
        self.assertEqual(old_workspace.name, new_workspace.name)
        self.assertEqual(new_default_workspace.scope, fallback_scope)
        self.assertEqual(new_workspace.scope, fallback_scope)

        # Make sure that this doesn't raise an exception.
        executor.migrate(migrate_from)

    def test_scoping_workspaces_irreversible(self) -> None:
        """Scoping workspaces is irreversible with actual scopes in use."""
        migrate_from = [
            ("db", "0079_sbuild_binnmu_build_components"),
        ]
        migrate_to = [("db", "0084_workspace_namespace_by_scope")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        FileStore = new_apps.get_model("db", "FileStore")
        Scope = new_apps.get_model('db', 'Scope')
        Workspace = new_apps.get_model('db', 'Workspace')

        custom_scope = Scope.objects.create(name="debian-lts")
        Workspace.objects.create(
            name="test-scope",
            scope=custom_scope,
            default_file_store=FileStore.objects.get(
                name=DEFAULT_FILE_STORE_NAME
            ),
        )

        with self.assertRaisesRegex(
            IrreversibleError,
            "non-fallback scopes are in use:"
            " can't revert to scopeless workspaces",
        ):
            executor.loader.build_graph()
            executor.migrate(migrate_from)

    def test_sign_multiple_unsigned(self) -> None:
        """Single unsigned lookups in Sign are migrated to multiple lookups."""
        migrate_from = [("db", "0090_workspacerole_and_more")]
        migrate_to = [("db", "0091_sign_multiple_unsigned")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        old_work_request_model = old_apps.get_model("db", "WorkRequest")
        default_workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        test_user = self.get_test_user_for_apps(old_apps)
        old_unsigned = [2, "internal@collections/name:unsigned"]
        old_dynamic_task_data = [{"unsigned_id": 2, "key_id": 1}, None]
        new_unsigned = [[2], ["internal@collections/name:unsigned"]]
        new_dynamic_task_data = [{"unsigned_ids": [2], "key_id": 1}, None]
        for unsigned, dynamic_task_data in zip(
            old_unsigned, old_dynamic_task_data
        ):
            old_work_request_model.objects.create(
                task_type=TaskTypes.SIGNING,
                task_name="sign",
                created_by_id=test_user.id,
                workspace=default_workspace,
                task_data={
                    "purpose": KeyPurpose.UEFI,
                    "unsigned": unsigned,
                    "key": 1,
                },
                dynamic_task_data=dynamic_task_data,
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)

        new_apps = executor.loader.project_state(migrate_to).apps
        new_work_requests = new_apps.get_model(
            "db", "WorkRequest"
        ).objects.order_by("id")
        self.assertEqual(
            [
                work_request.task_data["unsigned"]
                for work_request in new_work_requests
            ],
            new_unsigned,
        )
        self.assertEqual(
            [
                work_request.dynamic_task_data
                for work_request in new_work_requests
            ],
            new_dynamic_task_data,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        old_work_requests = old_apps.get_model(
            "db", "WorkRequest"
        ).objects.order_by("id")
        self.assertEqual(
            [
                work_request.task_data["unsigned"]
                for work_request in old_work_requests
            ],
            old_unsigned,
        )
        self.assertEqual(
            [
                work_request.dynamic_task_data
                for work_request in old_work_requests
            ],
            old_dynamic_task_data,
        )

    def test_sign_multiple_unsigned_unsigned_too_complex(self) -> None:
        """Migration is irreversible if task_data.unsigned is too complex."""
        migrate_from = [("db", "0091_sign_multiple_unsigned")]
        migrate_to = [("db", "0090_workspacerole_and_more")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        default_workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        test_user = self.get_test_user_for_apps(old_apps)
        work_request = old_apps.get_model("db", "WorkRequest").objects.create(
            task_type=TaskTypes.SIGNING,
            task_name="sign",
            created_by_id=test_user.id,
            workspace=default_workspace,
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": {
                    "collection": "internal@collections",
                    "child_type": LookupChildType.ARTIFACT,
                    "category": ArtifactCategory.SIGNING_INPUT,
                },
                "key": 1,
            },
        )

        with self.assertRaisesRegex(
            IrreversibleError,
            f"Work request {work_request.id}: {{.*}} cannot be converted to a "
            f"single lookup",
        ):
            executor.loader.build_graph()
            executor.migrate(migrate_to)

    def test_sign_multiple_unsigned_unsigned_ids_too_complex(self) -> None:
        """Migration is irreversible with multiple unsigned_ids."""
        migrate_from = [("db", "0091_sign_multiple_unsigned")]
        migrate_to = [("db", "0090_workspacerole_and_more")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        default_workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        test_user = self.get_test_user_for_apps(old_apps)
        work_request = old_apps.get_model("db", "WorkRequest").objects.create(
            task_type=TaskTypes.SIGNING,
            task_name="sign",
            created_by_id=test_user.id,
            workspace=default_workspace,
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [2],
                "key": 1,
            },
            dynamic_task_data={
                # This should be impossible since task_data.unsigned has
                # exactly one lookup, but it's here for the sake of test
                # coverage.
                "unsigned_ids": [2, 3],
                "key_id": 1,
            },
        )

        with self.assertRaisesRegex(
            IrreversibleError,
            fr"Work request {work_request.id}: \[2, 3\] does not have exactly "
            fr"one element",
        ):
            executor.loader.build_graph()
            executor.migrate(migrate_to)

    def test_default_workspace_public(self) -> None:
        """The default workspace is made public."""
        migrate_from = [("db", "0091_sign_multiple_unsigned")]
        migrate_to = [("db", "0092_default_workspace_public")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        self.assertFalse(
            old_apps.get_model("db", "Workspace")
            .objects.get(
                scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
                name=settings.DEBUSINE_DEFAULT_WORKSPACE,
            )
            .public
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        self.assertTrue(
            new_apps.get_model("db", "Workspace")
            .objects.get(
                scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
                name=settings.DEBUSINE_DEFAULT_WORKSPACE,
            )
            .public
        )

    def test_create_singleton_collections(self) -> None:
        """Singleton collections are created in the default workspace."""
        migrate_from = [
            ("db", "0094_collection_db_collection_name_not_reserved")
        ]
        migrate_to = [("db", "0095_create_singleton_collections")]
        singleton_collection_categories = (
            CollectionCategory.PACKAGE_BUILD_LOGS,
            CollectionCategory.TASK_HISTORY,
        )

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        self.assertQuerySetEqual(
            old_apps.get_model("db", "Collection")
            .objects.filter(
                name="_",
                category__in=singleton_collection_categories,
                workspace__scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
                workspace__name=settings.DEBUSINE_DEFAULT_WORKSPACE,
            )
            .values_list("category", flat=True),
            [],
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        self.assertQuerySetEqual(
            new_apps.get_model("db", "Collection")
            .objects.filter(
                name="_",
                category__in=singleton_collection_categories,
                workspace__scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
                workspace__name=settings.DEBUSINE_DEFAULT_WORKSPACE,
            )
            .values_list("category", flat=True),
            singleton_collection_categories,
            ordered=False,
        )

    def test_delete_dynamic_data_workflows(self) -> None:
        """Dynamic data from workflows is set to None."""
        migrate_from = [("db", "0098_artifact_original_artifact")]
        migrate_to = [("db", "0099_delete_dynamic_data_workflows")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps

        work_request = old_apps.get_model("db", "WorkRequest").objects.create(
            task_type=TaskTypes.WORKFLOW,
            task_name="piuparts",
            created_by_id=self.get_test_user_for_apps(old_apps).id,
            workspace=old_apps.get_model("db", "Workspace").objects.get(
                name=settings.DEBUSINE_DEFAULT_WORKSPACE
            ),
            task_data={
                "purpose": KeyPurpose.UEFI,
                "unsigned": [2],
                "key": 1,
            },
            dynamic_task_data={
                "source_artifact_id": 10,
                "binary_artifacts_ids": [11, 12, 13],
            },
        )

        work_request_id = work_request.id

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        work_request = new_apps.get_model("db", "WorkRequest").objects.get(
            id=work_request_id
        )

        # Migration removed dynamic_task_data
        self.assertIsNone(work_request.dynamic_task_data)

        executor.loader.build_graph()

        # Cannot reverse migrate if some WorkRequest is task_type == WORKFLOW
        with self.assertRaises(IrreversibleError):
            executor.migrate(migrate_from)

        work_request.task_type = TaskTypes.WORKER
        work_request.dynamic_task_data = {"source_artifact_id": 10}
        work_request.save()

        # If no WorkRequest of type WORKFLOW: reverse migration does nothing
        executor.migrate(migrate_from)

        work_request.refresh_from_db()

        self.assertEqual(
            work_request.dynamic_task_data, {"source_artifact_id": 10}
        )

    def test_wait_needs_input(self) -> None:
        """`needs_input` is added to WAIT tasks."""
        migrate_from = [("db", "0104_workspacerole_contributor")]
        migrate_to = [("db", "0105_wait_needs_input")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
            name=settings.DEBUSINE_DEFAULT_WORKSPACE,
        )
        now = timezone.now()

        old_work_requests = [
            (TaskTypes.WORKER, "delay", {}, {}),
            (
                TaskTypes.WAIT,
                "delay",
                {"delay_until": now.isoformat()},
                {"step": "soon"},
            ),
            (
                TaskTypes.WAIT,
                "delay",
                {"delay_until": (now + timedelta(days=1)).isoformat()},
                {"needs_input": True},
            ),
            (TaskTypes.WAIT, "externaldebsign", {"unsigned": 1}, {}),
            (TaskTypes.WAIT, "waitnoop", {}, {}),
        ]
        new_work_requests = [
            # Unchanged: not a WAIT task.
            (TaskTypes.WORKER, "delay", {}, {}),
            (
                TaskTypes.WAIT,
                "delay",
                {"delay_until": now.isoformat()},
                {"needs_input": False, "step": "soon"},
            ),
            # Unchanged: needs_input already set.
            (
                TaskTypes.WAIT,
                "delay",
                {"delay_until": (now + timedelta(days=1)).isoformat()},
                {"needs_input": True},
            ),
            (
                TaskTypes.WAIT,
                "externaldebsign",
                {"unsigned": 1},
                {"needs_input": True},
            ),
            # Unchanged: not "delay" or "externaldebsign".
            (TaskTypes.WAIT, "waitnoop", {}, {}),
        ]

        self.maxDiff = None
        for task_type, task_name, task_data, workflow_data in old_work_requests:
            old_apps.get_model("db", "WorkRequest").objects.create(
                workspace=workspace,
                created_by_id=self.get_test_user_for_apps(old_apps).id,
                task_type=task_type,
                task_name=task_name,
                task_data=task_data,
                workflow_data_json=workflow_data,
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        self.assertQuerySetEqual(
            new_apps.get_model("db", "WorkRequest")
            .objects.order_by("id")
            .values_list(
                "task_type", "task_name", "task_data", "workflow_data_json"
            ),
            new_work_requests,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps

        self.assertQuerySetEqual(
            old_apps.get_model("db", "WorkRequest")
            .objects.order_by("id")
            .values_list(
                "task_type", "task_name", "task_data", "workflow_data_json"
            ),
            old_work_requests,
        )

    def test_fill_scope_label(self) -> None:
        """Scope labels are initialized from scope names."""
        migrate_from = [("db", "0107_add_scope_label_icon")]
        migrate_to = [("db", "0108_fill_scope_label")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        OldScope = old_apps.get_model("db", "Scope")

        old_default_scope = OldScope.objects.create(name="unlabeled")
        self.assertEqual(old_default_scope.label, "")

        old_labeled_scope = OldScope.objects.create(
            name="labeled", label="Labeled Scope"
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        NewScope = new_apps.get_model("db", "Scope")

        self.assertEqual(
            NewScope.objects.get(pk=old_default_scope.pk).label, "Unlabeled"
        )
        self.assertEqual(
            NewScope.objects.get(pk=old_labeled_scope.pk).label, "Labeled Scope"
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        # Backwards migration does not reset labels that do not match
        # automatically generated ones
        self.assertEqual(
            OldScope.objects.get(pk=old_default_scope.pk).label, ""
        )
        self.assertEqual(
            OldScope.objects.get(pk=old_labeled_scope.pk).label, "Labeled Scope"
        )

    def test_workflow_last_activity_at(self) -> None:
        """Test workflow last_activity_at is updated on migration."""
        migrate_from = [("db", "0109_scope_label_required_and_unique")]
        migrate_to = [("db", "0110_workrequest_workflow_last_activity_at")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps

        OldWorkRequest = old_apps.get_model("db", "WorkRequest")

        Workspace = old_apps.get_model("db", "Workspace")
        Scope = old_apps.get_model("db", "Scope")
        FileStore = old_apps.get_model("db", "FileStore")
        User = old_apps.get_model("db", "User")

        workspace = Workspace.objects.create(
            name="Test",
            scope=Scope.objects.first(),
            default_file_store=FileStore.objects.first(),
        )
        user = User.objects.first()

        old_workflow_root = OldWorkRequest.objects.create(
            task_type=TaskTypes.WORKFLOW, workspace=workspace, created_by=user
        )
        OldWorkRequest.objects.create(
            parent=old_workflow_root,
            started_at=timezone.now(),
            workspace=workspace,
            created_by=user,
        )
        OldWorkRequest.objects.create(
            parent=old_workflow_root,
            started_at=timezone.now(),
            completed_at=timezone.now(),
            workspace=workspace,
            created_by=user,
        )
        old_workflow_child_workflow = OldWorkRequest.objects.create(
            parent=old_workflow_root,
            started_at=timezone.now(),
            completed_at=timezone.now(),
            task_type=TaskTypes.WORKFLOW,
            workspace=workspace,
            created_by=user,
        )
        old_workflow_child_child = OldWorkRequest.objects.create(
            parent=old_workflow_child_workflow,
            started_at=timezone.now(),
            completed_at=timezone.now(),
            workspace=workspace,
            created_by=user,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        NewWorkRequest = new_apps.get_model("db", "WorkRequest")

        self.assertEqual(
            NewWorkRequest.objects.get(
                pk=old_workflow_root.pk
            ).workflow_last_activity_at,
            old_workflow_child_child.completed_at,
        )

    def test_workflow_runtime_status(self) -> None:
        """Test workflow_runtime_status is updated at migration."""
        migrate_from = [("db", "0110_workrequest_workflow_last_activity_at")]
        migrate_to = [("db", "0111_workrequest_workflow_runtime_status")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps

        OldWorkRequest = old_apps.get_model("db", "WorkRequest")

        Workspace = old_apps.get_model("db", "Workspace")
        Scope = old_apps.get_model("db", "Scope")
        FileStore = old_apps.get_model("db", "FileStore")
        User = old_apps.get_model("db", "User")

        workspace = Workspace.objects.create(
            name="Test",
            scope=Scope.objects.first(),
            default_file_store=FileStore.objects.first(),
        )
        user = User.objects.first()

        old_workflow_root = OldWorkRequest.objects.create(
            task_type=TaskTypes.WORKFLOW, workspace=workspace, created_by=user
        )
        work_request = OldWorkRequest.objects.create(
            parent=old_workflow_root,
            started_at=timezone.now(),
            workspace=workspace,
            created_by=user,
            status=WorkRequest.Statuses.BLOCKED,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        NewWorkRequest = new_apps.get_model("db", "WorkRequest")

        # Workflow's workflow_runtime_status is BLOCKED
        self.assertEqual(
            NewWorkRequest.objects.get(
                pk=old_workflow_root.pk
            ).workflow_runtime_status,
            WorkRequest.RuntimeStatuses.BLOCKED,
        )

        # The children's workflow_runtime_status is None
        self.assertIsNone(
            NewWorkRequest.objects.get(
                pk=work_request.pk
            ).workflow_runtime_status
        )

    def test_autopkgtest_migrate_to_extra_repositories(self) -> None:
        """`autopkgtest` tasks are migrated to extra_repositories."""
        migrate_from = [("db", "0111_workrequest_workflow_runtime_status")]
        migrate_to = [("db", "0112_workrequest_autopkgtest_extra_repositories")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
            name=settings.DEBUSINE_DEFAULT_WORKSPACE,
        )

        old_work_requests = [
            (
                {
                    "input": {"source_artifact": 1},
                    "extra_apt_sources": [
                        "deb http://example.com/ foo/",
                        "deb http://example.net/ foo bar baz",
                        # Will lose the options in the migration:
                        (
                            "deb [signed-by=/dev/null] http://example.org/ "
                            "foo bar baz"
                        ),
                    ],
                },
            ),
            (
                {
                    "input": {"source_artifact": 2},
                },
            ),
            (
                {
                    "input": {"source_artifact": 3},
                    # Will all be skipped on migration:
                    "extra_apt_sources": [
                        "deb-src http://example.com/deb-src foo bar",
                        "some nonsense",
                    ],
                },
            ),
        ]

        new_work_requests = [
            (
                {
                    "input": {"source_artifact": 1},
                    "extra_repositories": [
                        {
                            "url": "http://example.com/",
                            "suite": "foo/",
                        },
                        {
                            "url": "http://example.net/",
                            "suite": "foo",
                            "components": ["bar", "baz"],
                        },
                        {
                            "url": "http://example.org/",
                            "suite": "foo",
                            "components": ["bar", "baz"],
                        },
                    ],
                },
            ),
            (
                {
                    "input": {"source_artifact": 2},
                },
            ),
            (
                {
                    "input": {"source_artifact": 3},
                },
            ),
        ]

        new_old_work_requests = [
            (
                {
                    "input": {"source_artifact": 1},
                    "extra_apt_sources": [
                        "deb http://example.com/ foo/",
                        "deb http://example.net/ foo bar baz",
                        "deb http://example.org/ foo bar baz",
                    ],
                },
            ),
            (
                {
                    "input": {"source_artifact": 2},
                },
            ),
            (
                {
                    "input": {"source_artifact": 3},
                },
            ),
        ]

        self.maxDiff = None
        for (task_data,) in old_work_requests:
            old_apps.get_model("db", "WorkRequest").objects.create(
                workspace=workspace,
                created_by_id=self.get_test_user_for_apps(old_apps).id,
                task_type=TaskTypes.WORKER,
                task_name="autopkgtest",
                task_data=task_data,
            )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        self.assertQuerySetEqual(
            new_apps.get_model("db", "WorkRequest")
            .objects.order_by("id")
            .values_list("task_data"),
            new_work_requests,
        )

        w = new_apps.get_model("db", "WorkRequest").objects.get(
            task_data__input__source_artifact=1
        )
        # Will be lost
        w.task_data["extra_repositories"][0]["signing_key"] = "public key"
        w.save()

        executor.loader.build_graph()
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps

        self.assertQuerySetEqual(
            old_apps.get_model("db", "WorkRequest")
            .objects.order_by("id")
            .values_list("task_data"),
            new_old_work_requests,
        )

    def test_workrequest_workflow_runtime_status_use_value(self) -> None:
        """Test WorkRequest workflow_runtime_status uses value not UI string."""
        migrate_from = [("db", "0114_user_manager_with_permissions")]
        migrate_to = [
            ("db", "0115_workrequest_workflow_runtime_status_use_value")
        ]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps

        old_work_request = old_apps.get_model(
            "db", "WorkRequest"
        ).objects.create(
            created_by_id=self.get_test_user_for_apps(old_apps).id,
            task_name="foo",
            workspace=old_apps.get_model("db", "Workspace").objects.get(
                name=settings.DEBUSINE_DEFAULT_WORKSPACE
            ),
            workflow_runtime_status="Needs Input",
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        NewWorkRequest = new_apps.get_model("db", "WorkRequest")

        self.assertEqual(
            NewWorkRequest.objects.get(
                pk=old_work_request.pk
            ).workflow_runtime_status,
            "needs_input",
        )

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)

        OldWorkRequest = old_apps.get_model("db", "WorkRequest")

        self.assertEqual(
            OldWorkRequest.objects.get(
                pk=old_work_request.pk
            ).workflow_runtime_status,
            "Needs Input",
        )

    def test_workrequest_fingerprint_migration(self) -> None:
        migrate_from = [("db", "0116_assets")]
        migrate_to = [("db", "0117_signing_work_request_fingerprints")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps

        default_workspace = old_apps.get_model("db", "Workspace").objects.get(
            name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        OldArtifact = old_apps.get_model("db", "Artifact")
        OldWorkRequest = old_apps.get_model("db", "WorkRequest")

        artifacts = [
            OldArtifact.objects.create(
                category="debusine:signing-key",
                workspace=default_workspace,
                data={
                    "fingerprint": fingerprint,
                },
            )
            for fingerprint in ["ABC123", "DEF456"]
        ]

        old_work_requests = [
            # (type, name, data, dynamic_data, parent_index)
            (  # 0:
                TaskTypes.WORKFLOW,
                "debian_pipeline",
                {
                    "upload_key": artifacts[0].id,
                    "make_signed_source_key": f"{artifacts[1].id}@artifacts",
                },
                {},
                None,
            ),
            (  # 1:
                TaskTypes.WORKFLOW,
                "make_signed_source",
                {
                    "key": "something-complex",
                },
                {},
                None,
            ),
            (  # 2:
                TaskTypes.SIGNING,
                "sign",
                {
                    "key": "something-complex",
                },
                {
                    "key_id": artifacts[0].id,
                },
                1,
            ),
            (  # 3:
                TaskTypes.WORKFLOW,
                "package_upload",
                {
                    "key": "something-complex",
                },
                {},
                None,
            ),
            (  # 4:
                TaskTypes.SIGNING,
                "debsign",
                {
                    "key": str(artifacts[1].id),
                },
                {
                    "key_id": artifacts[1].id,
                },
                3,
            ),
            # error cases:
            (  # 5: (invalid key)
                TaskTypes.SIGNING,
                "sign",
                {
                    "key": "something-complex",
                },
                {
                    "key_id": -1,
                },
                None,
            ),
            (  # 6: (no key)
                TaskTypes.SIGNING,
                "sign",
                {
                    "key": "something-complex",
                },
                {},
                None,
            ),
            (  # 7: (childless workflow)
                TaskTypes.WORKFLOW,
                "package_upload",
                {
                    "key": "something-complex",
                },
                {},
                None,
            ),
            (  # 8: (null key)
                TaskTypes.WORKFLOW,
                "package_upload",
                {
                    "key": None,
                },
                {},
                None,
            ),
            (  # 9: (null dynamic data)
                TaskTypes.WORKFLOW,
                "package_upload",
                {
                    "key": "something-complex",
                },
                None,
                None,
            ),
        ]
        old_work_request_ids: list[int] = []

        for i, (
            task_type,
            task_name,
            task_data,
            dynamic_task_data,
            parent_index,
        ) in enumerate(old_work_requests):
            parent_id: int | None = None
            if parent_index:
                parent_id = old_work_request_ids[parent_index]
            work_request = OldWorkRequest.objects.create(
                created_by_id=self.get_test_user_for_apps(old_apps).id,
                task_type=task_type,
                task_name=task_name,
                workspace=default_workspace,
                task_data=task_data,
                dynamic_task_data=dynamic_task_data,
                parent_id=parent_id,
            )
            old_work_request_ids.append(work_request.id)

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        NewWorkRequest = new_apps.get_model("db", "WorkRequest")

        new_work_requests = list(NewWorkRequest.objects.all().order_by("id"))
        self.assertEqual(
            new_work_requests[0].task_data,
            {
                "upload_key": "ABC123",
                "make_signed_source_key": "DEF456",
            },
        )
        self.assertEqual(
            new_work_requests[1].task_data,
            {"key": "ABC123"},
        )
        self.assertEqual(
            new_work_requests[2].task_data,
            {"key": "ABC123"},
        )
        self.assertNotIn(
            "key_id",
            new_work_requests[2].dynamic_task_data,
        )
        self.assertEqual(
            new_work_requests[3].task_data,
            {"key": "DEF456"},
        )
        self.assertEqual(
            new_work_requests[4].task_data,
            {"key": "DEF456"},
        )
        self.assertNotIn(
            "key_id",
            new_work_requests[4].dynamic_task_data,
        )
        # error cases:
        self.assertEqual(
            new_work_requests[5].task_data,
            {"key": "something-complex"},
        )
        self.assertEqual(
            new_work_requests[6].task_data,
            {"key": "something-complex"},
        )
        self.assertEqual(
            new_work_requests[7].task_data,
            {"key": "something-complex"},
        )
        self.assertEqual(
            new_work_requests[8].task_data,
            {"key": None},
        )
        self.assertEqual(
            new_work_requests[9].task_data,
            {"key": "something-complex"},
        )

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)

        new_old_work_requests = list(
            OldWorkRequest.objects.all().order_by("id")
        )

        self.assertEqual(
            new_old_work_requests[0].task_data,
            {
                "upload_key": f"{artifacts[0].id}@artifacts",
                "make_signed_source_key": f"{artifacts[1].id}@artifacts",
            },
        )
        self.assertEqual(
            new_old_work_requests[1].task_data,
            {"key": f"{artifacts[0].id}@artifacts"},
        )
        self.assertEqual(
            new_old_work_requests[2].task_data,
            {"key": f"{artifacts[0].id}@artifacts"},
        )
        self.assertEqual(
            new_old_work_requests[2].dynamic_task_data,
            {"key_id": artifacts[0].id},
        )
        self.assertEqual(
            new_old_work_requests[3].task_data,
            {"key": f"{artifacts[1].id}@artifacts"},
        )
        self.assertEqual(
            new_old_work_requests[4].task_data,
            {"key": f"{artifacts[1].id}@artifacts"},
        )
        self.assertEqual(
            new_old_work_requests[4].dynamic_task_data,
            {"key_id": artifacts[1].id},
        )

    def test_signing_key_asset_migration(self) -> None:
        """`debusine:signing-key` artifacts are migrated to assets."""
        migrate_from = [("db", "0117_signing_work_request_fingerprints")]
        migrate_to = [("db", "0118_signing_key_assets")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        workspace = old_apps.get_model("db", "Workspace").objects.get(
            scope__name=settings.DEBUSINE_DEFAULT_SCOPE,
            name=settings.DEBUSINE_DEFAULT_WORKSPACE,
        )

        # list of (category, data)
        old_artifacts = [
            (
                "debusine:signing-key",
                {
                    "purpose": KeyPurpose.UEFI,
                    "fingerprint": "ABC123",
                    "public_key": "PUBLIC_KEY",
                },
            ),
            (
                "debusine:signing-key",
                {
                    "purpose": KeyPurpose.OPENPGP,
                    "fingerprint": "ABC123456",
                    "public_key": "PUBLIC_KEY",
                },
            ),
            # Duplicate fingerprint, will be ignored
            (
                "debusine:signing-key",
                {
                    "purpose": KeyPurpose.OPENPGP,
                    "fingerprint": "ABC123",
                    "public_key": "PUBLIC_KEY",
                },
            ),
            # Random other artifact
            (
                "debusine:test",
                {},
            ),
        ]
        # The duplicate and test remain undeleted
        # The others keys are re-created
        new_old_artifacts = old_artifacts[2:] + old_artifacts[:2]
        new_assets = [
            (
                {
                    "purpose": KeyPurpose.UEFI,
                    "fingerprint": "ABC123",
                    "public_key": "PUBLIC_KEY",
                    "description": None,
                },
            ),
            (
                {
                    "purpose": KeyPurpose.OPENPGP,
                    "fingerprint": "ABC123456",
                    "public_key": "PUBLIC_KEY",
                    "description": None,
                },
            ),
        ]

        old_artifact_instances = []
        self.maxDiff = None
        for category, data in old_artifacts:
            artifact = old_apps.get_model("db", "Artifact").objects.create(
                workspace=workspace,
                category=category,
                data=data,
            )
            old_artifact_instances.append(artifact)

        old_apps.get_model("db", "ArtifactRelation").objects.create(
            artifact=old_artifact_instances[0],
            target=old_artifact_instances[3],  # debusine:test
            type="relates-to",
        )

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps

        self.assertQuerySetEqual(
            new_apps.get_model("db", "Asset")
            .objects.order_by("id")
            .values_list("data"),
            new_assets,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps

        self.assertQuerySetEqual(
            old_apps.get_model("db", "Artifact")
            .objects.order_by("id")
            .values_list("category", "data"),
            new_old_artifacts,
        )

    def test_scope_file_stores_migration(self) -> None:
        """File stores are migrated from `Workspace` to `Scope`."""
        migrate_from = [("db", "0119_scope_file_stores_initial")]
        migrate_to = [("db", "0120_scope_file_stores_data")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        OldFileStore = old_apps.get_model("db", "FileStore")
        OldScope = old_apps.get_model("db", "Scope")
        OldWorkspace = old_apps.get_model("db", "Workspace")

        scopes = [
            OldScope.objects.create(name=f"scope{i}", label=f"Scope {i}")
            for i in range(4)
        ]
        file_stores = [
            OldFileStore.objects.create(
                name=f"filestore{i}",
                backend=FileStore.BackendChoices.MEMORY,
                configuration={"name": f"filestore{i}"},
            )
            for i in range(3)
        ]
        for scope, workspace_name, default, others in (
            (scopes[0], "workspace0", file_stores[0], file_stores[1:]),
            (scopes[0], "workspace1", file_stores[0], file_stores[1:]),
            (scopes[0], "workspace2", file_stores[0], file_stores[1:]),
            (scopes[1], "workspace0", file_stores[1], [file_stores[0]]),
            (scopes[1], "workspace1", file_stores[1], [file_stores[0]]),
            (scopes[2], "workspace0", file_stores[2], []),
        ):
            workspace = OldWorkspace.objects.create(
                scope=scope,
                name=workspace_name,
                default_file_store=default,
            )
            workspace.other_file_stores.set(others)

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        NewFileStore = new_apps.get_model("db", "FileStore")
        NewScope = new_apps.get_model("db", "Scope")
        NewWorkspace = new_apps.get_model("db", "Workspace")

        self.assertQuerySetEqual(
            NewScope.objects.filter(name__startswith="scope").values_list(
                "name",
                "file_stores__name",
                "filestoreinscope__upload_priority",
                "filestoreinscope__download_priority",
            ),
            [
                ("scope0", "filestore0", 100, 100),
                ("scope0", "filestore1", None, None),
                ("scope0", "filestore2", None, None),
                ("scope1", "filestore1", 100, 100),
                ("scope1", "filestore0", None, None),
                ("scope2", "filestore2", 100, 100),
                ("scope3", None, None, None),
            ],
            ordered=False,
        )

        # Manually overwrite the old workspace file store relations so that
        # we can check that the reverse migration puts them back.
        for workspace in NewWorkspace.objects.all():
            workspace.default_file_store = NewFileStore.objects.get(
                name=DEFAULT_FILE_STORE_NAME
            )
            workspace.other_file_stores.clear()

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        self.assertQuerySetEqual(
            OldScope.objects.filter(name__startswith="scope").values_list(
                "name",
                "workspaces__name",
                "workspaces__default_file_store__name",
                "workspaces__other_file_stores__name",
            ),
            [
                ("scope0", "workspace0", "filestore0", "filestore1"),
                ("scope0", "workspace0", "filestore0", "filestore2"),
                ("scope0", "workspace1", "filestore0", "filestore1"),
                ("scope0", "workspace1", "filestore0", "filestore2"),
                ("scope0", "workspace2", "filestore0", "filestore1"),
                ("scope0", "workspace2", "filestore0", "filestore2"),
                ("scope1", "workspace0", "filestore1", "filestore0"),
                ("scope1", "workspace1", "filestore1", "filestore0"),
                ("scope2", "workspace0", "filestore2", None),
                ("scope3", None, None, None),
            ],
            ordered=False,
        )

    def test_scope_file_stores_migration_mismatched_file_stores(self) -> None:
        """File store migration needs all workspaces in a scope to agree."""
        migrate_from = [("db", "0119_scope_file_stores_initial")]
        migrate_to = [("db", "0120_scope_file_stores_data")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        OldFileStore = old_apps.get_model("db", "FileStore")
        OldScope = old_apps.get_model("db", "Scope")
        OldWorkspace = old_apps.get_model("db", "Workspace")

        scope = OldScope.objects.create(name="scope0", label="Scope 0")
        file_stores = [
            OldFileStore.objects.create(
                name=f"filestore{i}",
                backend=FileStore.BackendChoices.MEMORY,
                configuration={"name": f"filestore{i}"},
            )
            for i in range(3)
        ]
        for workspace_name, default in (
            ("workspace0", file_stores[0]),
            ("workspace1", file_stores[1]),
        ):
            OldWorkspace.objects.create(
                scope=scope, name=workspace_name, default_file_store=default
            )

        executor.loader.build_graph()
        with self.assertRaisesRegex(
            RuntimeError,
            r"^Scope 'scope0' has workspaces with different "
            r"default file stores; make them agree first$",
        ):
            executor.migrate(migrate_to)

        OldWorkspace.objects.filter(scope=scope).delete()
        for workspace_name, default, others in (
            ("workspace0", file_stores[0], [file_stores[1]]),
            ("workspace1", file_stores[0], file_stores[1:]),
        ):
            workspace = OldWorkspace.objects.create(
                scope=scope, name=workspace_name, default_file_store=default
            )
            workspace.other_file_stores.set(others)

        executor.loader.build_graph()
        with self.assertRaisesRegex(
            RuntimeError,
            r"^Scope 'scope0' has workspaces with different "
            r"other file stores; make them agree first$",
        ):
            executor.migrate(migrate_to)

    def test_file_store_total_size(self) -> None:
        """A migration initializes `FileStore.total_size`."""
        migrate_from = [("db", "0128_filestore_add_size_columns")]
        migrate_to = [("db", "0129_filestore_total_size")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        OldFileStore = old_apps.get_model("db", "FileStore")
        OldFile = old_apps.get_model("db", "File")
        OldFileInStore = old_apps.get_model("db", "FileInStore")
        file_stores = [
            OldFileStore.objects.create(
                name=f"filestore{i}",
                backend=FileStore.BackendChoices.MEMORY,
                configuration={"name": f"filestore{i}"},
            )
            for i in range(2)
        ]
        for file_store, length in (
            (file_stores[0], 0),
            (file_stores[0], 4),
            (file_stores[0], 1024),
            (file_stores[1], 16),
            (file_stores[1], 2048),
        ):
            contents = b"x" * length
            hash_digest = _calculate_hash_from_data(contents)
            file = OldFile.objects.create(sha256=hash_digest, size=length)
            OldFileInStore.objects.create(store=file_store, file=file)

        self.assertQuerySetEqual(
            OldFileStore.objects.filter(
                name__startswith="filestore"
            ).values_list("name", "total_size"),
            [("filestore0", None), ("filestore1", None)],
            ordered=False,
        )

        for file_store in file_stores:
            file_store.refresh_from_db()
            self.assertIsNone(file_store.total_size)

        executor.loader.build_graph()
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        NewFileStore = new_apps.get_model("db", "FileStore")

        self.assertQuerySetEqual(
            NewFileStore.objects.filter(
                name__startswith="filestore"
            ).values_list("name", "total_size"),
            [("filestore0", 1028), ("filestore1", 2064)],
            ordered=False,
        )

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        self.assertQuerySetEqual(
            OldFileStore.objects.filter(
                name__startswith="filestore"
            ).values_list("name", "total_size"),
            [("filestore0", None), ("filestore1", None)],
            ordered=False,
        )

    def test_group_user_through(self) -> None:
        """Test converting user-group m2m to through model."""
        migrate_from = [("db", "0135_workerpools")]
        migrate_to = [("db", "0136_add_groupmembership")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        OldScope = old_apps.get_model("db", "Scope")
        OldGroup = old_apps.get_model("db", "Group")
        old_user = self.get_test_user_for_apps(old_apps)
        old_scope = OldScope.objects.create(name="scope")
        old_group = OldGroup.objects.create(scope=old_scope, name="group")
        old_group.users.add(old_user)

        executor.loader.build_graph()
        executor.migrate(migrate_to)

        new_apps = executor.loader.project_state(migrate_to).apps
        NewGroup = new_apps.get_model("db", "Group")
        new_user = self.get_test_user_for_apps(new_apps)
        new_group = NewGroup.objects.get(name="group")
        # user is still a member
        self.assertQuerySetEqual(new_group.users.all(), [new_user])
        membership = new_group.membership.first()
        # we have a through table
        self.assertEqual(membership._meta.label, "db.GroupMembership")
        self.assertEqual(membership.group, new_group)
        self.assertEqual(membership.user, new_user)

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        # user is still a member
        old_group.refresh_from_db()
        self.assertQuerySetEqual(old_group.users.all(), [old_user])

    def test_user_group_roles(self) -> None:
        """Test converting user-group m2m to through model."""
        migrate_from = [("db", "0136_add_groupmembership")]
        migrate_to = [("db", "0137_add_groupmembership_role")]

        executor = MigrationExecutor(
            connection, progress_callback=_migrate_check_constraints
        )
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        OldScope = old_apps.get_model("db", "Scope")
        OldGroup = old_apps.get_model("db", "Group")
        old_user = self.get_test_user_for_apps(old_apps)
        old_scope = OldScope.objects.create(name="scope")
        old_group = OldGroup.objects.create(scope=old_scope, name="group")
        old_group.users.add(old_user)

        executor.loader.build_graph()
        executor.migrate(migrate_to)

        new_apps = executor.loader.project_state(migrate_to).apps
        NewGroup = new_apps.get_model("db", "Group")
        new_user = self.get_test_user_for_apps(new_apps)
        new_group = NewGroup.objects.get(name="group")
        membership = new_group.membership.first()
        self.assertEqual(membership.group, new_group)
        self.assertEqual(membership.user, new_user)
        self.assertEqual(membership.role, Group.Roles.MEMBER)

        executor.loader.build_graph()
        executor.migrate(migrate_from)

        # user is still a member
        old_group.refresh_from_db()
        self.assertQuerySetEqual(old_group.users.all(), [old_user])
