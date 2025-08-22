# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the database playground."""

import django.test
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.utils import timezone

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    DebianBinaryPackage,
    DebianBinaryPackages,
    DebianPackageBuildLog,
    DebianSourcePackage,
    DebianUpload,
    DebusineSigningInput,
    DebusineSigningOutput,
    SigningResult,
    TaskTypes,
)
from debusine.assets import (
    AWSProviderAccountConfiguration,
    AWSProviderAccountCredentials,
    AWSProviderAccountData,
    AssetCategory,
    CloudProvidersType,
    DummyProviderAccountData,
    KeyPurpose,
    SigningKeyData,
)
from debusine.db.context import context
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Asset,
    Collection,
    DEFAULT_FILE_STORE_NAME,
    FileInArtifact,
    FileStore,
    Scope,
    User,
    WorkRequest,
)
from debusine.db.models.scopes import ScopeRole
from debusine.db.playground import scenarios
from debusine.server.worker_pools.models import (
    AWSEC2WorkerPoolSpecification,
    DummyWorkerPoolSpecification,
    HetznerCloudWorkerPoolSpecification,
    WorkerPoolLimits,
)
from debusine.server.workflows.models import WorkRequestWorkflowData
from debusine.tasks.models import (
    ActionTypes,
    BackendType,
    SbuildBuildComponent,
    SbuildDynamicData,
)
from debusine.test import TestCase
from debusine.test.playground import Playground


class PlaygroundTest(django.test.TestCase, TestCase):
    """Test playground functions."""

    def assert_artifact_relations(
        self,
        artifact: Artifact,
        targets: list[tuple[Artifact, ArtifactRelation.Relations]],
    ) -> None:
        """Check that an artifact has the given set of relations."""
        actual: list[tuple[Artifact, ArtifactRelation.Relations]] = []
        for relation in ArtifactRelation.objects.filter(artifact=artifact):
            actual.append(
                (relation.target, ArtifactRelation.Relations(relation.type))
            )
        self.assertEqual(
            sorted(actual, key=lambda x: (x[0].pk, x[1])),
            sorted(targets, key=lambda x: (x[0].pk, x[1])),
        )

    def test_defaults(self) -> None:
        """Check default playground configuration."""
        with Playground() as playground:
            user = playground.get_default_user()
            self.assertEqual(user.username, "playground")

            file_store = playground.get_default_file_store()
            self.assertEqual(file_store.name, DEFAULT_FILE_STORE_NAME)
            self.assertEqual(
                file_store.backend, FileStore.BackendChoices.MEMORY
            )

            workspace = playground.get_default_workspace()
            self.assertEqual(workspace.scope.file_stores.get(), file_store)
            self.assertEqual(
                workspace.name, settings.DEBUSINE_DEFAULT_WORKSPACE
            )

    def test_user_password(self) -> None:
        """Check that the default user password is set when requested."""
        playground = Playground(
            default_username="test1", default_user_email="test1@example.org"
        )
        user = playground.get_default_user()
        self.assertEqual(user.username, "test1")
        self.assertFalse(user.has_usable_password())

        playground = Playground(
            default_username="test2",
            default_user_password="test",
            default_user_email="test2@example.org",
        )
        user = playground.get_default_user()
        self.assertEqual(user.username, "test2")
        self.assertTrue(user.has_usable_password())
        self.assertTrue(check_password("test", user.password))

    def test_create_workspace_scope(self) -> None:
        """Test setting scope on created workspaces."""
        playground = Playground()
        testscope = playground.get_or_create_scope("testscope")

        ws1 = playground.create_workspace()
        self.assertEqual(playground.create_workspace(), ws1)

        ws2 = playground.create_workspace(scope=testscope)
        self.assertEqual(playground.create_workspace(scope=testscope), ws2)
        self.assertNotEqual(ws1, ws2)

    def test_create_worker_without_pool(self) -> None:
        playground = Playground()
        worker = playground.create_worker()
        self.assertIsNone(worker.worker_pool)
        self.assertIsNone(worker.instance_created_at)

    def test_create_worker_with_pool(self) -> None:
        playground = Playground()
        worker_pool = playground.create_worker_pool()
        worker = playground.create_worker(worker_pool=worker_pool)
        self.assertEqual(worker.worker_pool, worker_pool)
        self.assertIsNotNone(worker.instance_created_at)

    def test_create_worker_pool_explicit(self) -> None:
        playground = Playground()
        provider_account = playground.create_cloud_provider_account_asset()
        specifications = AWSEC2WorkerPoolSpecification(launch_templates=[])
        limits = WorkerPoolLimits(max_active_instances=42)
        pool = playground.create_worker_pool(
            name="foo",
            enabled=False,
            architectures=["amd64"],
            tags=["a"],
            provider_account=provider_account,
            specifications=specifications,
            instance_wide=False,
            ephemeral=True,
            limits=limits,
        )
        self.assertEqual(pool.name, "foo")
        self.assertFalse(pool.enabled)
        self.assertEqual(pool.architectures, ["amd64"])
        self.assertEqual(pool.tags, ["a"])
        self.assertEqual(pool.provider_account, provider_account)
        self.assertEqual(pool.specifications, specifications.dict())
        self.assertFalse(pool.instance_wide)
        self.assertTrue(pool.ephemeral)
        self.assertEqual(pool.limits, limits.dict())

    def test_create_worker_pool_defaults(self) -> None:
        playground = Playground()
        pool = playground.create_worker_pool()
        self.assertEqual(pool.name, "test")
        self.assertTrue(pool.enabled)
        self.assertEqual(pool.architectures, ["amd64", "i386"])
        self.assertEqual(pool.tags, [])
        self.assertIsNotNone(pool.provider_account, Asset)
        self.assertEqual(
            pool.specifications,
            DummyWorkerPoolSpecification().dict(),
        )
        self.assertTrue(pool.instance_wide)
        self.assertFalse(pool.ephemeral)
        self.assertEqual(pool.limits, WorkerPoolLimits().dict())

    def test_create_worker_pool_aws(self) -> None:
        playground = Playground()
        pool = playground.create_worker_pool(
            provider_account=playground.create_cloud_provider_account_asset(
                cloud_provider=CloudProvidersType.AWS
            ),
        )
        self.assertEqual(
            pool.specifications,
            AWSEC2WorkerPoolSpecification(launch_templates=[]).dict(),
        )

    def test_create_worker_pool_hetzner(self) -> None:
        playground = Playground()
        pool = playground.create_worker_pool(
            provider_account=playground.create_cloud_provider_account_asset(
                cloud_provider=CloudProvidersType.HETZNER
            ),
        )
        self.assertEqual(
            pool.specifications,
            HetznerCloudWorkerPoolSpecification(
                server_type="cx22",
                image_name="debian-12",
            ).dict(),
        )

    def test_create_worker_pool_unknown_provider(self) -> None:
        playground = Playground()
        provider_account = playground.create_cloud_provider_account_asset()
        provider_account.data = {"provider_type": "unknown"}
        with context.disable_permission_checks():
            provider_account.save()

        with self.assertRaisesRegex(
            NotImplementedError, r"No support for unknown"
        ):
            playground.create_worker_pool(provider_account=provider_account)

    def test_create_file_in_backend(self) -> None:
        """create_file_in_backend() returns a File and writes contents to it."""
        playground = self.enterContext(Playground(memory_file_store=False))
        file_backend = FileStore.default().get_backend_object()
        contents = b"some-test-data"

        fileobj = playground.create_file_in_backend(file_backend, contents)

        self.assertIsInstance(fileobj.id, int)
        local_path = file_backend.get_local_path(fileobj)
        assert local_path is not None
        self.assertEqual(local_path.read_bytes(), contents)

    def test_create_source_artifact(self) -> None:
        """Test creating a source artifact."""
        playground = Playground()
        source = playground.create_source_artifact()
        self.assertEqual(source.category, ArtifactCategory.SOURCE_PACKAGE)
        self.assertEqual(source.workspace, playground.get_default_workspace())
        self.assertEqual(source.files.count(), 0)
        self.assertEqual(source.created_by, playground.get_default_user())
        self.assertIsNone(source.created_by_work_request)

        artifact = DebianSourcePackage(**source.data)
        self.assert_source_artifact_equal(artifact, "hello", "1.0-1")

    def test_create_minimal_binary_packages_artifact(self) -> None:
        """Test creating a binary_packages artifact."""
        playground = Playground()
        bp = playground.create_minimal_binary_packages_artifact(
            "hello", "1.0-1", "1.0-1", "amd64"
        )
        self.assertEqual(bp.category, ArtifactCategory.BINARY_PACKAGES)
        self.assertEqual(bp.workspace, playground.get_default_workspace())
        self.assertEqual(bp.files.count(), 0)
        self.assertIsNone(bp.created_by)
        self.assertIsNone(bp.created_by_work_request)

        artifact = DebianBinaryPackages(**bp.data)
        self.assertEqual(artifact.srcpkg_name, "hello")
        self.assertEqual(artifact.srcpkg_version, "1.0-1")
        self.assertEqual(artifact.version, "1.0-1")
        self.assertEqual(artifact.architecture, "amd64")
        self.assertEqual(artifact.packages, [])

    def test_create_minimal_binary_package_artifact(self) -> None:
        """Test creating a binary_package artifact."""
        playground = Playground()
        bp = playground.create_minimal_binary_package_artifact(
            "hello", "1.0-1", "hello", "1.0-1", "amd64"
        )
        self.assertEqual(bp.category, ArtifactCategory.BINARY_PACKAGE)
        self.assertEqual(bp.workspace, playground.get_default_workspace())
        self.assertEqual(bp.files.count(), 0)
        self.assertIsNone(bp.created_by)
        self.assertIsNone(bp.created_by_work_request)

        artifact = DebianBinaryPackage(**bp.data)
        self.assertEqual(artifact.srcpkg_name, "hello")
        self.assertEqual(artifact.srcpkg_version, "1.0-1")
        self.assertEqual(artifact.deb_control_files, [])
        self.assertEqual(
            artifact.deb_fields,
            {
                "Architecture": "amd64",
                "Package": "hello",
                "Version": "1.0-1",
            },
        )

    def test_create_source_artifact_with_files(self) -> None:
        """Test creating a source artifact with its files."""
        playground = Playground()
        source = playground.create_source_artifact(create_files=True)
        files = sorted(
            FileInArtifact.objects.filter(artifact=source),
            key=lambda f: f.path,
        )
        self.assertEqual(len(files), 3)
        self.assertEqual(files[0].path, "hello_1.0-1.debian.tar.xz")
        self.assertEqual(files[1].path, "hello_1.0-1.dsc")
        self.assertEqual(files[2].path, "hello_1.0.orig.tar.gz")

    def test_create_upload_artifacts(self) -> None:
        """Test creating a set of upload artifacts."""
        playground = Playground()
        upload_artifacts = playground.create_upload_artifacts(binaries=["bin1"])
        upload = upload_artifacts.upload
        self.assertEqual(upload.category, ArtifactCategory.UPLOAD)
        self.assertEqual(upload.workspace, playground.get_default_workspace())
        self.assertEqual(upload.files.count(), 0)
        self.assertEqual(upload.created_by, playground.get_default_user())
        self.assertIsNone(upload.created_by_work_request)

        DebianUpload(**upload.data)

        source = upload_artifacts.source
        self.assertIsNotNone(source)
        self.assertEqual(source.category, ArtifactCategory.SOURCE_PACKAGE)
        self.assertEqual(source.workspace, playground.get_default_workspace())
        self.assertEqual(source.files.count(), 0)
        self.assertEqual(source.created_by, playground.get_default_user())
        self.assertIsNone(source.created_by_work_request)
        self.assertTrue(
            ArtifactRelation.objects.filter(
                artifact=upload,
                target=source,
                type=ArtifactRelation.Relations.EXTENDS,
            ).exists()
        )

        binaries = upload_artifacts.binaries
        self.assertIsNotNone(binaries)
        self.assertEqual(len(binaries), 1)
        binary = binaries[0]
        self.assertEqual(binary.category, ArtifactCategory.BINARY_PACKAGE)
        self.assertEqual(binary.workspace, playground.get_default_workspace())
        self.assertEqual(binary.files.count(), 0)
        self.assertEqual(binary.created_by, playground.get_default_user())
        self.assertIsNone(binary.created_by_work_request)

        self.assertTrue(
            ArtifactRelation.objects.filter(
                artifact=upload,
                target=binary,
                type=ArtifactRelation.Relations.EXTENDS,
            ).exists()
        )

    def test_create_upload_artifacts_no_binaries(self) -> None:
        """Test creating a set of upload source artifacts."""
        playground = Playground()
        upload_artifacts = playground.create_upload_artifacts(binary=False)
        upload = upload_artifacts.upload
        self.assertEqual(upload.category, ArtifactCategory.UPLOAD)
        self.assertEqual(upload.workspace, playground.get_default_workspace())
        self.assertEqual(upload.files.count(), 0)
        self.assertEqual(upload.created_by, playground.get_default_user())
        self.assertIsNone(upload.created_by_work_request)

        DebianUpload(**upload.data)

        source = upload_artifacts.source
        self.assertIsNotNone(source)
        self.assertEqual(source.category, ArtifactCategory.SOURCE_PACKAGE)
        self.assertEqual(source.workspace, playground.get_default_workspace())
        self.assertEqual(source.files.count(), 0)
        self.assertEqual(source.created_by, playground.get_default_user())
        self.assertIsNone(source.created_by_work_request)
        self.assertTrue(
            ArtifactRelation.objects.filter(
                artifact=upload,
                target=source,
                type=ArtifactRelation.Relations.EXTENDS,
            ).exists()
        )

        self.assertIsNone(upload_artifacts.binaries)

    def test_create_upload_artifacts_binary_only(self) -> None:
        """Test creating a set of upload binary artifacts."""
        playground = Playground()
        upload_artifacts = playground.create_upload_artifacts(
            source=False, binaries=["foo"]
        )
        upload = upload_artifacts.upload
        self.assertEqual(upload.category, ArtifactCategory.UPLOAD)
        self.assertEqual(upload.workspace, playground.get_default_workspace())
        self.assertEqual(upload.files.count(), 0)
        self.assertEqual(upload.created_by, playground.get_default_user())
        self.assertIsNone(upload.created_by_work_request)

        DebianUpload(**upload.data)

        self.assertIsNone(upload_artifacts.source)

        binaries = upload_artifacts.binaries
        self.assertIsNotNone(binaries)
        self.assertEqual(len(binaries), 1)
        binary = binaries[0]
        self.assertEqual(binary.category, ArtifactCategory.BINARY_PACKAGE)
        self.assertEqual(binary.workspace, playground.get_default_workspace())
        self.assertEqual(binary.files.count(), 0)
        self.assertEqual(binary.created_by, playground.get_default_user())
        self.assertIsNone(binary.created_by_work_request)

        self.assertTrue(
            ArtifactRelation.objects.filter(
                artifact=upload,
                target=binary,
                type=ArtifactRelation.Relations.EXTENDS,
            ).exists()
        )

    def test_create_build_log_artifact(self) -> None:
        """Test creating a build log artifact."""
        playground = Playground()
        buildlog = playground.create_build_log_artifact()
        self.assertEqual(buildlog.category, ArtifactCategory.PACKAGE_BUILD_LOG)
        self.assertEqual(buildlog.workspace, playground.get_default_workspace())
        self.assertEqual(buildlog.files.count(), 1)
        self.assertEqual(buildlog.created_by, playground.get_default_user())
        self.assertIsNone(buildlog.created_by_work_request)

        artifact = DebianPackageBuildLog(**buildlog.data)
        self.assertEqual(artifact.source, "hello")
        self.assertEqual(artifact.version, "1.0-1")
        self.assertEqual(artifact.filename, "hello_1.0-1_amd64.buildlog")

        file = buildlog.files.first()
        assert file is not None
        file_backend = buildlog.workspace.scope.download_file_backend(file)
        with file_backend.get_stream(file) as fd:
            self.assertEqual(
                fd.read().splitlines(keepends=True)[3].decode(),
                "Line 4 of hello_1.0-1_amd64.buildlog\n",
            )

    def test_create_build_log_artifact_custom(self) -> None:
        """Test creating a build log artifact with custom arguments."""
        test_contents = b"test contents"
        playground = Playground()
        user = User.objects.create_user(
            username="custom", email="custom@example.org"
        )
        work_request = playground.create_work_request(created_by=user)
        buildlog = playground.create_build_log_artifact(
            source="test",
            version="2.0",
            build_arch="arm64",
            work_request=work_request,
            contents=test_contents,
        )
        self.assertEqual(buildlog.category, ArtifactCategory.PACKAGE_BUILD_LOG)
        self.assertEqual(buildlog.workspace, playground.get_default_workspace())
        self.assertEqual(buildlog.files.count(), 1)
        self.assertEqual(buildlog.created_by, user)
        self.assertEqual(buildlog.created_by_work_request, work_request)

        artifact = DebianPackageBuildLog(**buildlog.data)
        self.assertEqual(artifact.source, "test")
        self.assertEqual(artifact.version, "2.0")
        self.assertEqual(artifact.filename, "test_2.0_arm64.buildlog")

        file = buildlog.files.first()
        assert file is not None
        file_backend = buildlog.workspace.scope.download_file_backend(file)
        with file_backend.get_stream(file) as fd:
            self.assertEqual(fd.read(), test_contents)

    def test_create_build_log_artifact_custom_user(self) -> None:
        """Test creating a build log artifact with custom user."""
        playground = Playground()
        user = User.objects.create_user(
            username="custom", email="custom@example.org"
        )
        work_request = playground.create_work_request()
        buildlog = playground.create_build_log_artifact(
            work_request=work_request,
            created_by=user,
        )
        self.assertEqual(buildlog.created_by, user)
        self.assertEqual(buildlog.created_by_work_request, work_request)

    def test_create_signing_input_artifact(self) -> None:
        playground = Playground()
        bp = playground.create_signing_input_artifact("hello")
        self.assertEqual(bp.category, ArtifactCategory.SIGNING_INPUT)
        self.assertEqual(bp.workspace, playground.get_default_workspace())
        self.assertEqual(bp.files.count(), 0)
        self.assertIsNone(bp.created_by)
        self.assertIsNone(bp.created_by_work_request)

        artifact = DebusineSigningInput(**bp.data)
        self.assertEqual(artifact.binary_package_name, "hello")
        self.assertIsNone(artifact.trusted_certs)

    def test_create_signing_output_artifact(self) -> None:
        playground = Playground()
        bp = playground.create_signing_output_artifact(
            purpose=KeyPurpose.UEFI,
            fingerprint="ABC123",
            results=[SigningResult(file="test", output_file="test.sig")],
            binary_package_name="hello",
        )
        self.assertEqual(bp.category, ArtifactCategory.SIGNING_OUTPUT)
        self.assertEqual(bp.workspace, playground.get_default_workspace())
        self.assertEqual(bp.files.count(), 0)
        self.assertIsNone(bp.created_by)
        self.assertIsNone(bp.created_by_work_request)

        artifact = DebusineSigningOutput(**bp.data)
        self.assertEqual(artifact.purpose, KeyPurpose.UEFI)
        self.assertEqual(artifact.fingerprint, "ABC123")
        self.assertEqual(
            artifact.results,
            [SigningResult(file="test", output_file="test.sig")],
        )
        self.assertEqual(artifact.binary_package_name, "hello")

    def test_create_signing_key_asset_defaults(self) -> None:
        """Test create_signing_key_asset with defaults."""
        playground = Playground()
        asset = playground.create_signing_key_asset()
        self.assertEqual(asset.category, AssetCategory.SIGNING_KEY)
        self.assertEqual(asset.workspace, playground.get_default_workspace())
        self.assertEqual(asset.created_by, playground.get_default_user())
        self.assertIsNone(asset.created_by_work_request)
        data_model = asset.data_model
        assert isinstance(data_model, SigningKeyData)
        self.assertEqual(data_model.purpose, KeyPurpose.OPENPGP)
        self.assertGreater(len(data_model.fingerprint), 8)
        self.assertTrue(
            data_model.public_key.startswith(
                "-----BEGIN PGP PUBLIC KEY BLOCK-----"
            )
        )
        self.assertEqual(data_model.description, "Test Key")

    def test_create_signing_key_asset_custom(self) -> None:
        """Test create_signing_key_asset with specified features."""
        playground = Playground()
        workspace = playground.create_workspace()
        user = playground.create_user(username="test")
        asset = playground.create_signing_key_asset(
            purpose=KeyPurpose.UEFI,
            fingerprint="ABCDEF",
            public_key="PUBLIC KEY",
            description="Custom Asset",
            workspace=workspace,
            created_by=user,
        )
        self.assertEqual(asset.workspace, workspace)
        self.assertEqual(asset.created_by, user)
        self.assertIsNone(asset.created_by_work_request)
        data_model = asset.data_model
        assert isinstance(data_model, SigningKeyData)
        self.assertEqual(data_model.purpose, KeyPurpose.UEFI)
        self.assertEqual(data_model.fingerprint, "ABCDEF")
        self.assertEqual(data_model.public_key, "PUBLIC KEY")
        self.assertEqual(data_model.description, "Custom Asset")

    def test_create_cloud_provider_account_asset_defaults(self) -> None:
        playground = Playground()
        asset = playground.create_cloud_provider_account_asset()
        self.assertEqual(
            asset.data, DummyProviderAccountData(name="test").dict()
        )

    def test_create_cloud_provider_account_asset_custom_data(self) -> None:
        playground = Playground()
        data = DummyProviderAccountData(name="test", secret="custom-secret")
        asset = playground.create_cloud_provider_account_asset(data=data)
        self.assertEqual(asset.data, data.dict())

    def test_create_cloud_provider_account_asset_aws(self) -> None:
        playground = Playground()
        asset = playground.create_cloud_provider_account_asset(
            name="aws-test", cloud_provider=CloudProvidersType.AWS
        )
        self.assertEqual(
            asset.data,
            AWSProviderAccountData(
                name="aws-test",
                configuration=AWSProviderAccountConfiguration(
                    region_name="test-region"
                ),
                credentials=AWSProviderAccountCredentials(
                    access_key_id="access-key",
                    secret_access_key="secret-key",
                ),
            ).dict(),
        )

    def test_create_asset_usage_defaults(self) -> None:
        """Test create_asset_usage with default workspace."""
        playground = Playground()
        asset = playground.create_signing_key_asset()
        asset_usage = playground.create_asset_usage(resource=asset)
        self.assertEqual(asset_usage.asset, asset)
        self.assertEqual(
            asset_usage.workspace, playground.get_default_workspace()
        )

    def test_create_asset_usage_custom(self) -> None:
        """Test create_asset_usage with specified workspace."""
        playground = Playground()
        asset = playground.create_signing_key_asset()
        workspace = playground.create_workspace()
        asset_usage = playground.create_asset_usage(
            resource=asset, workspace=workspace
        )
        self.assertEqual(asset_usage.asset, asset)
        self.assertEqual(asset_usage.workspace, workspace)

    def test_create_debian_env_collection_defaults(self) -> None:
        """Test create_debian_environments_collection."""
        playground = Playground()
        env = playground.create_debian_environments_collection()
        self.assertEqual(env.name, "debian")
        self.assertEqual(env.category, CollectionCategory.ENVIRONMENTS)
        self.assertEqual(env.workspace, playground.get_default_workspace())

    def test_create_debian_env_collection_custom(self) -> None:
        """Test create_debian_environments_collection."""
        playground = Playground()
        workspace = playground.create_workspace(name="custom")
        env = playground.create_debian_environments_collection(
            name="ubuntu", workspace=workspace
        )
        self.assertEqual(env.name, "ubuntu")
        self.assertEqual(env.category, CollectionCategory.ENVIRONMENTS)
        self.assertEqual(env.workspace, workspace)

    def test_create_debian_env_defaults(self) -> None:
        """Test create_debian_environment."""
        playground = Playground()
        env_item = playground.create_debian_environment()
        self.assertEqual(env_item.category, ArtifactCategory.SYSTEM_TARBALL)
        self.assertIsNotNone(env_item.artifact)
        self.assertEqual(
            env_item.parent_collection,
            playground.create_debian_environments_collection(),
        )
        self.assertEqual(
            env_item.created_by_user, playground.get_default_user()
        )
        self.assertEqual(
            env_item.data,
            {
                'architecture': 'amd64',
                'backend': 'unshare',
                'codename': 'bookworm',
                'variant': None,
            },
        )

        env = env_item.artifact
        assert env is not None
        self.assertEqual(env.category, ArtifactCategory.SYSTEM_TARBALL)
        self.assertEqual(env.workspace, playground.get_default_workspace())
        self.assertEqual(
            env.data,
            {
                "architecture": "amd64",
                "codename": "bookworm",
                "components": ["main"],
                'filename': 'test',
                'mirror': 'https://deb.debian.org',
                'pkglist': [],
                'variant': None,
                'vendor': 'Debian',
                "with_dev": True,
                'with_init': True,
            },
        )

    def test_create_debian_env_custom(self) -> None:
        """Test create_debian_environment with custom args."""
        playground = Playground()
        workspace = playground.create_workspace(name="custom")
        collection = playground.create_debian_environments_collection(
            workspace=workspace
        )
        user = User.objects.create_user(
            username="custom", email="custom@example.org"
        )
        env_item = playground.create_debian_environment(
            workspace=workspace, variant="apt", collection=collection, user=user
        )
        self.assertEqual(env_item.category, ArtifactCategory.SYSTEM_TARBALL)
        self.assertIsNotNone(env_item.artifact)
        self.assertEqual(env_item.parent_collection, collection)
        self.assertEqual(env_item.created_by_user, user)
        self.assertEqual(
            env_item.data,
            {
                'architecture': 'amd64',
                'backend': 'unshare',
                'codename': 'bookworm',
                'variant': 'apt',
            },
        )

        env = env_item.artifact
        assert env is not None
        self.assertEqual(env.category, ArtifactCategory.SYSTEM_TARBALL)
        self.assertEqual(env.workspace, workspace)
        self.assertEqual(
            env.data,
            {
                "architecture": "amd64",
                "codename": "bookworm",
                "components": ["main"],
                'filename': 'test',
                'mirror': 'https://deb.debian.org',
                'pkglist': [],
                "variant": "apt",
                'vendor': 'Debian',
                "with_dev": True,
                'with_init': True,
            },
        )

    def test_create_debian_env_reuse(self) -> None:
        """Test object reuse of create_debian_environment."""
        playground = Playground()
        env_item1 = playground.create_debian_environment()
        env_item2 = playground.create_debian_environment()
        self.assertEqual(env_item1, env_item2)

        env_item2 = playground.create_debian_environment(
            environment=env_item1.artifact
        )
        self.assertEqual(env_item1, env_item2)

    def test_create_debian_env_image(self) -> None:
        """Test create_debian_environment for images."""
        playground = Playground()
        env_item = playground.create_debian_environment(
            category=ArtifactCategory.SYSTEM_IMAGE
        )
        self.assertEqual(env_item.category, ArtifactCategory.SYSTEM_IMAGE)
        self.assertIsNotNone(env_item.artifact)
        self.assertEqual(
            env_item.parent_collection,
            playground.create_debian_environments_collection(),
        )
        self.assertEqual(
            env_item.data,
            {
                'architecture': 'amd64',
                'backend': 'unshare',
                'codename': 'bookworm',
                'variant': None,
            },
        )

        env = env_item.artifact
        assert env is not None
        self.assertEqual(env.category, ArtifactCategory.SYSTEM_IMAGE)
        self.assertEqual(env.workspace, playground.get_default_workspace())
        self.assertEqual(
            env.data,
            {
                "architecture": "amd64",
                "codename": "bookworm",
                "components": ["main"],
                'filename': 'test',
                'mirror': 'https://deb.debian.org',
                'pkglist': [],
                'variant': None,
                'vendor': 'Debian',
                "with_dev": True,
                'with_init': True,
            },
        )

    def test_create_sbuild_work_request(self) -> None:
        """Test creating a sbuild work request."""
        playground = Playground()
        source = playground.create_source_artifact()
        environment_item = playground.create_debian_environment()
        assert environment_item.artifact is not None
        wr = playground.create_sbuild_work_request(
            source=source,
            environment=environment_item.artifact,
            architecture="amd64",
        )

        self.assertEqual(wr.workspace, playground.get_default_workspace())
        self.assertEqual(wr.created_by, playground.get_default_user())
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        self.assertEqual(wr.result, WorkRequest.Results.NONE)
        self.assertIsNone(wr.worker)
        self.assertEqual(wr.task_type, TaskTypes.WORKER)
        self.assertEqual(wr.task_name, "sbuild")
        self.assertEqual(
            wr.task_data,
            {
                'backend': BackendType.UNSHARE,
                'build_components': [SbuildBuildComponent.ANY],
                'environment': environment_item.artifact.pk,
                'host_architecture': 'amd64',
                'input': {'source_artifact': source.pk},
            },
        )
        self.assertIsNone(wr.dynamic_task_data)
        self.assertIsNone(wr.parent)

    def test_simulate_package_build(self) -> None:
        """Test simulating a whole package build."""
        playground = Playground()
        source = playground.create_source_artifact()
        wr = playground.simulate_package_build(source, architecture="amd64")
        task_config_collection = Collection.objects.get(
            workspace=playground.get_default_workspace(),
            name="default",
            category=CollectionCategory.TASK_CONFIGURATION,
        )

        binaries: list[Artifact] = []
        buildlogs: list[Artifact] = []
        uploads: list[Artifact] = []
        for artifact in Artifact.objects.filter(created_by_work_request=wr):
            match artifact.category:
                case ArtifactCategory.BINARY_PACKAGE:
                    binaries.append(artifact)
                case ArtifactCategory.PACKAGE_BUILD_LOG:
                    buildlogs.append(artifact)
                case ArtifactCategory.UPLOAD:
                    uploads.append(artifact)
                case _ as unreachable:
                    self.fail(
                        "Work request generated unexpected"
                        f" {unreachable} artifact"
                    )
        self.assertEqual(len(binaries), 1)
        self.assertEqual(len(buildlogs), 1)
        self.assertEqual(len(uploads), 1)

        self.assertEqual(wr.workspace, playground.get_default_workspace())
        self.assertIsNotNone(wr.started_at)
        self.assertIsNotNone(wr.completed_at)
        self.assertEqual(wr.created_by, playground.get_default_user())
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.SUCCESS)
        self.assertIsNotNone(wr.worker)
        self.assertEqual(wr.task_type, TaskTypes.WORKER)
        self.assertEqual(wr.task_name, "sbuild")
        environment = Artifact.objects.get(pk=wr.task_data["environment"])
        source = Artifact.objects.get(
            pk=wr.task_data["input"]["source_artifact"]
        )
        self.assertEqual(
            wr.task_data,
            {
                'backend': BackendType.UNSHARE,
                'build_components': [SbuildBuildComponent.ANY],
                'environment': environment.pk,
                'host_architecture': 'amd64',
                'input': {'source_artifact': source.pk},
            },
        )
        self.assertEqual(
            SbuildDynamicData.parse_obj(wr.dynamic_task_data),
            SbuildDynamicData(
                environment_id=environment.pk,
                input_source_artifact_id=source.pk,
                binnmu_maintainer=(
                    f"Debusine <noreply@{settings.DEBUSINE_FQDN}>"
                ),
                subject="hello",
                runtime_context="any:amd64:amd64",
                configuration_context="bookworm",
                task_configuration_id=task_config_collection.id,
            ),
        )
        self.assertIsNone(wr.parent)
        self.assertEqual(wr.event_reactions_json, {})

        # Test environment
        self.assertEqual(environment.category, ArtifactCategory.SYSTEM_TARBALL)

        # Test source
        self.assertEqual(source.category, ArtifactCategory.SOURCE_PACKAGE)
        self.assert_source_artifact_equal(
            DebianSourcePackage(**source.data), name="hello", version="1.0-1"
        )

        # Test buildlog
        buildlog = buildlogs[0]
        self.assertEqual(buildlog.category, ArtifactCategory.PACKAGE_BUILD_LOG)
        self.assertEqual(
            buildlog.data,
            {
                'filename': 'hello_1.0-1_amd64.buildlog',
                'source': 'hello',
                'version': '1.0-1',
                'architecture': 'amd64',
                'bd_uninstallable': None,
            },
        )

        # Test binary
        binary = binaries[0]
        self.assertEqual(binary.category, ArtifactCategory.BINARY_PACKAGE)
        self.assertEqual(
            binary.data,
            {
                'deb_control_files': ['control'],
                'deb_fields': {
                    'Architecture': 'amd64',
                    'Description': 'Example description',
                    'Maintainer': 'Example Maintainer <example@example.org>',
                    'Package': 'hello',
                    'Version': '1.0-1',
                },
                'srcpkg_name': 'hello',
                'srcpkg_version': '1.0-1',
            },
        )

        # Test upload
        upload = uploads[0]
        self.assertEqual(upload.category, ArtifactCategory.UPLOAD)
        self.assertEqual(upload.data["changes_fields"]["Architecture"], "amd64")
        self.assertEqual(upload.data["changes_fields"]["Binary"], "hello")
        self.assertEqual(upload.data["changes_fields"]["Source"], "hello")
        self.assertEqual(upload.data["changes_fields"]["Version"], "1.0-1")

        # Test artifact relations
        self.assert_artifact_relations(
            buildlog,
            [
                (source, ArtifactRelation.Relations.RELATES_TO),
                (binary, ArtifactRelation.Relations.RELATES_TO),
            ],
        )
        self.assert_artifact_relations(source, [])
        self.assert_artifact_relations(
            binary, [(source, ArtifactRelation.Relations.BUILT_USING)]
        )
        self.assert_artifact_relations(
            upload, [(binary, ArtifactRelation.Relations.EXTENDS)]
        )

    def test_simulate_package_build_custom(self) -> None:
        """Test simulating a whole package build with custom args."""
        playground = Playground()

        workspace = playground.create_workspace(name="custom", public=True)
        source = playground.create_source_artifact(
            name="test", version="2.0", workspace=workspace
        )
        env_item = playground.create_debian_environment(
            workspace=workspace,
        )
        environment = env_item.artifact
        assert environment is not None
        worker = playground.create_worker()

        wr = playground.simulate_package_build(
            source,
            environment=environment,
            worker=worker,
            architecture="amd64",
        )

        binaries: list[Artifact] = []
        buildlogs: list[Artifact] = []
        uploads: list[Artifact] = []
        for artifact in Artifact.objects.filter(created_by_work_request=wr):
            match artifact.category:
                case ArtifactCategory.BINARY_PACKAGE:
                    binaries.append(artifact)
                case ArtifactCategory.PACKAGE_BUILD_LOG:
                    buildlogs.append(artifact)
                case ArtifactCategory.UPLOAD:
                    uploads.append(artifact)
                case _ as unreachable:
                    self.fail(
                        "Work request generated unexpected"
                        f" {unreachable} artifact"
                    )
        self.assertEqual(len(binaries), 1)
        self.assertEqual(len(buildlogs), 1)

        self.assertEqual(wr.workspace, workspace)
        self.assertIsNotNone(wr.started_at)
        self.assertIsNotNone(wr.completed_at)
        self.assertEqual(wr.created_by, playground.get_default_user())
        self.assertEqual(wr.status, WorkRequest.Statuses.COMPLETED)
        self.assertEqual(wr.result, WorkRequest.Results.SUCCESS)
        self.assertEqual(wr.worker, worker)
        self.assertEqual(wr.task_type, TaskTypes.WORKER)
        self.assertEqual(wr.task_name, "sbuild")
        self.assertEqual(
            Artifact.objects.get(pk=wr.task_data["environment"]), environment
        )
        self.assertEqual(
            Artifact.objects.get(pk=wr.task_data["input"]["source_artifact"]),
            source,
        )
        self.assertEqual(
            wr.task_data,
            {
                'backend': BackendType.UNSHARE,
                'build_components': [SbuildBuildComponent.ANY],
                'environment': environment.pk,
                'host_architecture': 'amd64',
                'input': {'source_artifact': source.pk},
            },
        )
        self.assertEqual(
            SbuildDynamicData.parse_obj(wr.dynamic_task_data),
            SbuildDynamicData(
                environment_id=environment.pk,
                input_source_artifact_id=source.pk,
                binnmu_maintainer=(
                    f"Debusine <noreply@{settings.DEBUSINE_FQDN}>"
                ),
                subject="test",
                runtime_context="any:amd64:amd64",
                configuration_context="bookworm",
            ),
        )
        self.assertIsNone(wr.parent)
        self.assertEqual(wr.event_reactions_json, {})

        # Test environment
        self.assertEqual(environment.category, ArtifactCategory.SYSTEM_TARBALL)

        # Test buildlog
        buildlog = buildlogs[0]
        self.assertEqual(buildlog.category, ArtifactCategory.PACKAGE_BUILD_LOG)
        self.assertEqual(
            buildlog.data,
            {
                'filename': 'test_2.0_amd64.buildlog',
                'source': 'test',
                'version': '2.0',
                'architecture': 'amd64',
                'bd_uninstallable': None,
            },
        )

        # Test binary
        binary = binaries[0]
        self.assertEqual(binary.category, ArtifactCategory.BINARY_PACKAGE)
        self.assertEqual(
            binary.data,
            {
                'deb_control_files': ['control'],
                'deb_fields': {
                    'Architecture': 'amd64',
                    'Description': 'Example description',
                    'Maintainer': 'Example Maintainer <example@example.org>',
                    'Package': 'test',
                    'Version': '2.0',
                },
                'srcpkg_name': 'test',
                'srcpkg_version': '2.0',
            },
        )

        # Test upload
        upload = uploads[0]
        self.assertEqual(upload.category, ArtifactCategory.UPLOAD)
        self.assertEqual(upload.data["changes_fields"]["Architecture"], "amd64")
        self.assertEqual(upload.data["changes_fields"]["Binary"], "test")
        self.assertEqual(upload.data["changes_fields"]["Source"], "test")
        self.assertEqual(upload.data["changes_fields"]["Version"], "2.0")

        # Test artifact relations
        self.assert_artifact_relations(
            buildlog,
            [
                (source, ArtifactRelation.Relations.RELATES_TO),
                (binary, ArtifactRelation.Relations.RELATES_TO),
            ],
        )
        self.assert_artifact_relations(source, [])
        self.assert_artifact_relations(
            binary, [(source, ArtifactRelation.Relations.BUILT_USING)]
        )
        self.assert_artifact_relations(
            upload, [(binary, ArtifactRelation.Relations.EXTENDS)]
        )

    def test_simulate_package_build_workflow(self) -> None:
        """Test simulating a package build in a workflow."""
        playground = Playground()
        template = playground.create_workflow_template("test", "noop")
        workflow = playground.create_workflow(template, task_data={})
        source = playground.create_source_artifact()
        wr = playground.simulate_package_build(source, workflow=workflow)
        self.assertEqual(wr.parent, workflow)
        self.assertEqual(
            wr.workflow_data,
            WorkRequestWorkflowData(
                display_name="Build all",
                step="build-all",
            ),
        )
        self.assertEqual(
            wr.event_reactions_json,
            {
                "on_creation": [],
                "on_unblock": [],
                "on_assignment": [],
                "on_success": [
                    {
                        "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                        "artifact_filters": {
                            "category": ArtifactCategory.UPLOAD
                        },
                        "collection": "internal@collections",
                        "created_at": None,
                        "name_template": "build-all",
                        "variables": {
                            "architecture": "all",
                            "binary_names": ["hello"],
                            "source_package_name": "hello",
                        },
                    }
                ],
                "on_failure": [],
            },
        )

    def test_create_workflow(self) -> None:
        """Test create_workflow."""
        playground = Playground()
        workflow = playground.create_workflow()

        self.assertTrue(workflow.is_workflow)

    def test_create_work_request(self) -> None:
        """Test create_work_request return a saved work request."""
        playground = Playground()
        work_request = playground.create_work_request()
        work_request.refresh_from_db()
        self.assertIsInstance(work_request, WorkRequest)
        self.assertEqual(
            work_request.workspace, playground.get_default_workspace()
        )

    def test_create_work_request_use_created_by_user(self) -> None:
        """Test create_work_request use created_by Token."""
        playground = Playground()
        user = playground.create_user("testuser")
        work_request = playground.create_work_request(created_by=user)
        work_request.refresh_from_db()
        self.assertEqual(work_request.created_by, user)

    def test_create_work_request_expired(self) -> None:
        """Test create_work_request expired=True."""
        playground = Playground()
        work_request = playground.create_work_request(expired=True)

        self.assertIsNotNone(work_request.expire_at)
        assert work_request.expire_at is not None
        self.assertLess(work_request.expire_at, timezone.now())

    def test_create_bare_token(self) -> None:
        """create_bare_token for an enabled token with no user and worker."""
        playground = Playground()
        token = playground.create_bare_token()
        self.assertTrue(token.enabled)
        self.assertIsNone(token.user)
        self.assertFalse(hasattr(token, "worker"))
        self.assertFalse(hasattr(token, "activating_worker"))

    def test_create_bare_token_disabled(self) -> None:
        """create_bare_token for a disabled token with no user and worker."""
        playground = Playground()
        token = playground.create_bare_token(enabled=False)
        self.assertFalse(token.enabled)
        self.assertIsNone(token.user)
        self.assertFalse(hasattr(token, "worker"))
        self.assertFalse(hasattr(token, "activating_worker"))

    def test_create_user_token(self) -> None:
        """create_user_token returns an enabled token with user."""
        playground = Playground()
        token = playground.create_user_token()
        self.assertTrue(token.enabled)
        self.assertEqual(token.user, playground.get_default_user())
        self.assertFalse(hasattr(token, "worker"))
        self.assertFalse(hasattr(token, "activating_worker"))

    def test_create_user_token_with_user(self) -> None:
        """Test create_user_token with a given user."""
        playground = Playground()
        user = playground.create_user("test")
        token = playground.create_user_token(user=user)
        self.assertTrue(token.enabled)
        self.assertEqual(token.user, user)
        self.assertFalse(hasattr(token, "worker"))
        self.assertFalse(hasattr(token, "activating_worker"))

    def test_create_user_token_disabled(self) -> None:
        """create_user_token returns a disabled token with user."""
        playground = Playground()
        token = playground.create_user_token(enabled=False)
        self.assertFalse(token.enabled)
        self.assertIsNotNone(token.user)
        self.assertFalse(hasattr(token, "worker"))
        self.assertFalse(hasattr(token, "activating_worker"))

    def test_create_worker_token(self) -> None:
        """create_worker_token returns an enabled token."""
        playground = Playground()
        token = playground.create_worker_token()
        self.assertTrue(token.enabled)
        self.assertIsNone(token.user)
        self.assertIsNotNone(token.worker)
        self.assertFalse(hasattr(token, "activating_worker"))

    def test_create_worker_token_with_worker(self) -> None:
        """create_worker_token returns a token with a worker."""
        playground = Playground()
        worker = playground.create_worker()
        token = playground.create_worker_token(worker=worker)
        self.assertTrue(token.enabled)
        self.assertIsNone(token.user)
        self.assertEqual(token.worker, worker)
        self.assertFalse(hasattr(token, "activating_worker"))

    def test_create_worker_token_disabled(self) -> None:
        """create_worker_token returns a disabled token."""
        playground = Playground()
        token = playground.create_worker_token(enabled=False)
        self.assertFalse(token.enabled)
        self.assertIsNone(token.user)
        self.assertIsNotNone(token.worker)
        self.assertFalse(hasattr(token, "activating_worker"))

    def test_create_worker_activation_token(self) -> None:
        """create_worker_activation_token returns an enabled token."""
        playground = Playground()
        token = playground.create_worker_activation_token()
        self.assertTrue(token.enabled)
        self.assertIsNone(token.user)
        self.assertFalse(hasattr(token, "worker"))
        self.assertIsNotNone(token.activating_worker)

    def test_create_worker_activation_token_with_worker(self) -> None:
        """create_worker_activation_token returns a token with a worker."""
        playground = Playground()
        worker = playground.create_worker()
        token = playground.create_worker_activation_token(worker=worker)
        self.assertTrue(token.enabled)
        self.assertIsNone(token.user)
        self.assertFalse(hasattr(token, "worker"))
        self.assertEqual(token.activating_worker, worker)

    def test_create_worker_activation_token_disabled(self) -> None:
        """create_worker_activation_token returns a disabled token."""
        playground = Playground()
        token = playground.create_worker_activation_token(enabled=False)
        self.assertFalse(token.enabled)
        self.assertIsNone(token.user)
        self.assertFalse(hasattr(token, "worker"))
        self.assertIsNotNone(token.activating_worker)

    def test_create_group(self) -> None:
        playground = Playground()
        scope = playground.get_default_scope()
        user1 = playground.create_user("user1")
        group = playground.create_group("testgroup", users=[user1], scope=scope)
        self.assertEqual(group.name, "testgroup")
        self.assertQuerySetEqual(group.users.all(), [user1])
        self.assertEqual(group.scope, scope)

    def test_create_group_defaults(self) -> None:
        playground = Playground()
        group = playground.create_group("testgroup")
        self.assertEqual(group.name, "testgroup")
        self.assertQuerySetEqual(group.users.all(), [])
        self.assertEqual(group.scope, playground.get_default_scope())

    def test_create_group_role(self) -> None:
        """Test create_group_role."""
        playground = Playground()
        scope = playground.get_default_scope()
        group = playground.create_group_role(scope, Scope.Roles.OWNER)
        self.assertEqual(group.name, f"{scope.name}-owner")
        self.assertQuerySetEqual(group.users.all(), [])
        assignment = ScopeRole.objects.get(resource=scope, group=group)
        self.assertEqual(assignment.role, Scope.Roles.OWNER)

    def test_create_group_role_with_name(self) -> None:
        """Test create_group_role with name."""
        playground = Playground()
        scope = playground.get_default_scope()
        group = playground.create_group_role(
            scope, Scope.Roles.OWNER, name="foo"
        )
        self.assertEqual(group.name, "foo")
        self.assertQuerySetEqual(group.users.all(), [])
        assignment = ScopeRole.objects.get(resource=scope, group=group)
        self.assertEqual(assignment.role, Scope.Roles.OWNER)

    def test_create_group_role_with_users(self) -> None:
        """Test create_group_role with name."""
        playground = Playground()
        scope = playground.get_default_scope()
        user1 = playground.get_default_user()
        user2 = playground.create_user("user2")
        group = playground.create_group_role(
            scope, Scope.Roles.OWNER, users=[user1, user2]
        )
        self.assertQuerySetEqual(
            group.users.all(), [user1, user2], ordered=False
        )
        assignment = ScopeRole.objects.get(resource=scope, group=group)
        self.assertEqual(assignment.role, Scope.Roles.OWNER)

    def test_create_group_role_idempotent(self) -> None:
        """Test create_group_role being idempotent."""
        playground = Playground()
        scope = playground.get_default_scope()
        user1 = playground.get_default_user()
        user2 = playground.create_user("user2")

        group = playground.create_group_role(
            scope, Scope.Roles.OWNER, users=[user1]
        )
        assignment = ScopeRole.objects.get(resource=scope, group=group)
        self.assertEqual(assignment.role, Scope.Roles.OWNER)
        self.assertQuerySetEqual(group.users.all(), [user1])

        group2 = playground.create_group_role(
            scope, Scope.Roles.OWNER, users=[user2]
        )
        self.assertEqual(group.pk, group2.pk)
        self.assertQuerySetEqual(group2.users.all(), [user2])

        assignment2 = ScopeRole.objects.get(resource=scope, group=group)
        # Assignment is regenerated
        self.assertNotEqual(assignment.pk, assignment2.pk)
        self.assertEqual(assignment.role, Scope.Roles.OWNER)

    def test_scenario(self) -> None:
        """Test building a scenario."""
        playground = Playground()
        scenario = scenarios.DefaultContext()
        playground.build_scenario(scenario)
        self.assertIsNotNone(scenario.scope)
        self.assertEqual(playground.scenarios, {})
        self.assertIsNone(context.scope)

    def test_scenario_named(self) -> None:
        """Test building a named scenario."""
        playground = Playground()
        scenario = scenarios.DefaultContext()
        playground.build_scenario(scenario, scenario_name="test")
        self.assertIsNotNone(scenario.scope)
        self.assertEqual(playground.scenarios, {"test": scenario})
        self.assertIsNone(context.scope)

    def test_scenario_set_current(self) -> None:
        """Test building a scenario with set_current."""
        with context.local():
            playground = Playground()
            scenario = scenarios.DefaultContext(set_current=True)
            playground.build_scenario(
                scenario, scenario_name="test", set_current=True
            )
            self.assertEqual(playground.scenarios, {"test": scenario})
            self.assertEqual(context.scope, scenario.scope)

    def test_scenario_set_current_not_requested(self) -> None:
        """Test building a scenario with set_current."""
        with context.local():
            playground = Playground()
            scenario = scenarios.DefaultContext()
            playground.build_scenario(
                scenario, scenario_name="test", set_current=True
            )
            self.assertEqual(playground.scenarios, {"test": scenario})
            self.assertIsNone(context.scope)

    def test_scenario_set_current_not_yet(self) -> None:
        """Test building a scenario with set_current."""
        with context.local():
            playground = Playground()
            scenario = scenarios.DefaultContext(set_current=True)
            playground.build_scenario(scenario, scenario_name="test")
            self.assertEqual(playground.scenarios, {"test": scenario})
            self.assertIsNone(context.scope)
