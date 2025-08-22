# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the workflow_utils functions."""
import re

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    CollectionCategory,
    DebianSourcePackage,
    DebusinePromise,
)
from debusine.client.models import LookupChildType
from debusine.db.models import ArtifactRelation, CollectionItem
from debusine.server.collections.lookup import LookupResult
from debusine.server.workflows import (
    LintianWorkflow,
    NoopWorkflow,
    SbuildWorkflow,
    workflow_utils,
)
from debusine.server.workflows.models import (
    LintianWorkflowData,
    SbuildWorkflowData,
)
from debusine.tasks import TaskConfigError
from debusine.tasks.models import (
    BackendType,
    ExtraRepository,
    LookupMultiple,
    SbuildInput,
)
from debusine.test.django import TestCase


class WorkflowUtilsTests(TestCase):
    """Tests for functions in workflows.workflow_utils."""

    def test_source_package_input_and_source_package_data(self) -> None:
        source_package = self.playground.create_source_artifact(
            name="hello", version="1.0"
        )
        self.playground.create_debian_environment(
            vendor="debian", codename="trixie"
        )

        work_request = self.playground.create_workflow(
            "sbuild",
            SbuildWorkflowData(
                input=SbuildInput(source_artifact=source_package.id),
                target_distribution="debian:trixie",
                architectures=["amd64"],
            ),
        )

        workflow = SbuildWorkflow(work_request)

        artifact = workflow_utils.source_package(workflow)
        self.assertEqual(artifact, source_package)

        source_package_data = workflow_utils.source_package_data(workflow)
        self.assertEqual(source_package_data.name, "hello")
        self.assertEqual(source_package_data.version, "1.0")

    def test_source_package_source_artifact(self) -> None:
        source_package = self.playground.create_source_artifact(
            name="hello", version="1.0"
        )
        binary_artifact = (
            self.playground.create_minimal_binary_package_artifact()
        )

        self.playground.create_debian_environment(
            vendor="debian", codename="trixie"
        )

        work_request = self.playground.create_workflow(
            "lintian",
            LintianWorkflowData(
                source_artifact=source_package.id,
                binary_artifacts=LookupMultiple.parse_obj([binary_artifact.id]),
                vendor="debian",
                codename="trixie",
            ),
        )

        workflow = LintianWorkflow(work_request)

        source_package_data = workflow_utils.source_package_data(workflow)

        self.assertEqual(source_package_data.name, "hello")
        self.assertEqual(source_package_data.version, "1.0")

    def test_lookup_result_artifact_category_artifact(self) -> None:
        """lookup_result_artifact_category with an artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": "hello",
                "version": "1.0-1",
                "type": "dpkg",
                "dsc_fields": {"Architecture": "any"},
            },
        )
        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT, artifact=artifact
        )

        self.assertEqual(
            workflow_utils.lookup_result_artifact_category(result),
            ArtifactCategory.SOURCE_PACKAGE,
        )

    def test_lookup_result_artifact_category_promise(self) -> None:
        """lookup_result_artifact_category with a promise."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        promise = self.playground.create_bare_data_item(
            collection,
            "test",
            category=BareDataCategory.PROMISE,
            data={
                "promise_work_request_id": 2,
                "promise_workflow_id": 1,
                "promise_category": ArtifactCategory.TEST,
            },
        )
        result = LookupResult(
            result_type=CollectionItem.Types.BARE, collection_item=promise
        )

        self.assertEqual(
            workflow_utils.lookup_result_artifact_category(result),
            ArtifactCategory.TEST,
        )

    def test_lookup_result_artifact_category_other_bare_item(self) -> None:
        """lookup_result_artifact_category with a non-promise bare item."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        promise = self.playground.create_bare_data_item(
            collection, "test", category=BareDataCategory.TEST
        )
        result = LookupResult(
            result_type=CollectionItem.Types.BARE, collection_item=promise
        )

        with self.assertRaisesRegex(
            ValueError,
            re.escape(
                f"Cannot determine artifact category for lookup result: "
                f"{result}"
            ),
        ):
            workflow_utils.lookup_result_artifact_category(result)

    def test_lookup_result_artifact_category_collection(self) -> None:
        """lookup_result_artifact_category raises ValueError for collections."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        result = LookupResult(
            result_type=CollectionItem.Types.COLLECTION, collection=collection
        )

        with self.assertRaisesRegex(
            ValueError,
            re.escape(
                f"Cannot determine artifact category for lookup result: "
                f"{result}"
            ),
        ):
            workflow_utils.lookup_result_artifact_category(result)

    def test_lookup_result_architecture_promise_with_architecture(self) -> None:
        """lookup_result_architecture with a binary promise."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        promise = self.playground.create_bare_data_item(
            collection,
            "test",
            category=BareDataCategory.PROMISE,
            data={
                "promise_work_request_id": 2,
                "promise_workflow_id": 1,
                "promise_category": ArtifactCategory.TEST,
                "architecture": "amd64",
            },
        )
        result = LookupResult(
            result_type=CollectionItem.Types.BARE, collection_item=promise
        )

        self.assertEqual(
            workflow_utils.lookup_result_architecture(result), "amd64"
        )

    def test_lookup_result_architecture_promise_no_architecture(self) -> None:
        """lookup_result_architecture rejects promise without architecture."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        promise = self.playground.create_bare_data_item(
            collection,
            "test",
            category=BareDataCategory.PROMISE,
            data=DebusinePromise(
                promise_work_request_id=2,
                promise_workflow_id=1,
                promise_category=ArtifactCategory.TEST,
            ),
        )
        result = LookupResult(
            result_type=CollectionItem.Types.BARE, collection_item=promise
        )

        with self.assertRaisesRegex(
            ValueError,
            re.escape(
                f"Cannot determine architecture for lookup result: {result}"
            ),
        ):
            workflow_utils.lookup_result_architecture(result)

    def test_lookup_result_architecture_binary_packages_artifact(self) -> None:
        """lookup_result_architecture with a binary-packages artifact."""
        artifact = self.playground.create_minimal_binary_packages_artifact(
            "hello", "1.0-1", "1.0-1", "i386"
        )
        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT, artifact=artifact
        )

        self.assertEqual(
            workflow_utils.lookup_result_architecture(result), "i386"
        )

    def test_lookup_result_architecture_binary_packages_artifact_item(
        self,
    ) -> None:
        """lookup_result_architecture with a binary-packages artifact item."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        artifact = self.playground.create_minimal_binary_packages_artifact(
            "hello", "1.0-1", "1.0-1", "i386"
        )
        item = CollectionItem.objects.create_from_artifact(
            artifact,
            parent_collection=collection,
            name="hello",
            data={},
            created_by_user=self.playground.get_default_user(),
        )
        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT,
            collection_item=item,
            artifact=artifact,
        )

        self.assertEqual(
            workflow_utils.lookup_result_architecture(result), "i386"
        )

    def test_lookup_result_architecture_debian_binary_package(self) -> None:
        """lookup_result_architecture return arch from a binary package."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0.0",
                "deb_fields": {"Architecture": "amd64"},
                "deb_control_files": [],
            },
        )

        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT, artifact=artifact
        )

        self.assertEqual(
            workflow_utils.lookup_result_architecture(result),
            "amd64",
        )

    def test_lookup_result_architecture_debian_binary_package_item(
        self,
    ) -> None:
        """lookup_result_architecture return arch from a binary package item."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0.0",
                "deb_fields": {"Architecture": "amd64"},
                "deb_control_files": [],
            },
        )
        item = CollectionItem.objects.create_from_artifact(
            artifact,
            parent_collection=collection,
            name="hello",
            data={},
            created_by_user=self.playground.get_default_user(),
        )
        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT,
            collection_item=item,
            artifact=artifact,
        )

        self.assertEqual(
            workflow_utils.lookup_result_architecture(result),
            "amd64",
        )

    def test_lookup_result_architecture_debian_upload(self) -> None:
        """lookup_result_architecture return arch from an upload artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.UPLOAD,
            data={
                "type": "dpkg",
                "changes_fields": {
                    "Architecture": "amd64",
                    "Files": [{"name": "test.deb"}],
                },
            },
        )

        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT, artifact=artifact
        )

        self.assertEqual(
            workflow_utils.lookup_result_architecture(result),
            "amd64",
        )

    def test_lookup_result_architecture_debian_package_build_log(self) -> None:
        """lookup_result_architecture return arch from a build log."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            data={
                "source": "hello",
                "version": "1.0.0-1",
                "architecture": "amd64",
                "filename": "hello.build",
            },
        )

        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT, artifact=artifact
        )

        self.assertEqual(
            workflow_utils.lookup_result_architecture(result),
            "amd64",
        )

    def test_lookup_result_architecture_debian_upload_item(self) -> None:
        """lookup_result_architecture return arch from an upload item."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.UPLOAD,
            data={
                "type": "dpkg",
                "changes_fields": {
                    "Architecture": "amd64",
                    "Files": [{"name": "test.deb"}],
                },
            },
        )
        item = CollectionItem.objects.create_from_artifact(
            artifact,
            parent_collection=collection,
            name="hello",
            data={},
            created_by_user=self.playground.get_default_user(),
        )
        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT,
            collection_item=item,
            artifact=artifact,
        )

        self.assertEqual(
            workflow_utils.lookup_result_architecture(result),
            "amd64",
        )

    def test_lookup_result_architecture_other_artifacts(self) -> None:
        """lookup_result_architecture with source package artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": "hello",
                "version": "1.0-1",
                "type": "dpkg",
                "dsc_fields": {"Architecture": "any"},
            },
        )
        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT, artifact=artifact
        )

        with self.assertRaisesRegex(
            workflow_utils.ArtifactHasNoArchitecture,
            re.escape(f"{DebianSourcePackage!r}"),
        ):
            workflow_utils.lookup_result_architecture(result)

    def test_lookup_result_architecture_collection(self) -> None:
        """lookup_result_architecture with a collection."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        result = LookupResult(
            result_type=CollectionItem.Types.COLLECTION, collection=collection
        )

        with self.assertRaisesRegex(
            ValueError,
            re.escape(
                "Unexpected result: must have collection_item or artifact"
            ),
        ):
            workflow_utils.lookup_result_architecture(result)

    def test_lookup_result_architecture_collection_item_invalid(self) -> None:
        """lookup_result_architecture raise error: invalid architecture type."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        promise = self.playground.create_bare_data_item(
            collection,
            "test",
            category=BareDataCategory.PROMISE,
            data={
                "promise_work_request_id": 2,
                "promise_workflow_id": 1,
                "promise_category": ArtifactCategory.TEST,
                "architecture": 000,
            },
        )
        result = LookupResult(
            result_type=CollectionItem.Types.BARE, collection_item=promise
        )

        with self.assertRaisesRegex(
            ValueError,
            "Cannot determine architecture for lookup result:",
        ):
            workflow_utils.lookup_result_architecture(result)

    def test_lookup_result_architecture_unexpected_result(self) -> None:
        """lookup_result_architecture raise error: unexpected result."""
        result = LookupResult(result_type=CollectionItem.Types.BARE)

        with self.assertRaisesRegex(ValueError, "^Unexpected result: .*"):
            workflow_utils.lookup_result_architecture(result)

    def test_lookup_result_binary_package_name_promise_with_bpn(self) -> None:
        """lookup_result_binary_package_name with a binary promise."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        promise = self.playground.create_bare_data_item(
            collection,
            "test",
            category=BareDataCategory.PROMISE,
            data={
                "promise_work_request_id": 2,
                "promise_workflow_id": 1,
                "promise_category": ArtifactCategory.TEST,
                "binary_package_name": "hello",
            },
        )
        result = LookupResult(
            result_type=CollectionItem.Types.BARE, collection_item=promise
        )

        self.assertEqual(
            workflow_utils.lookup_result_binary_package_name(result), "hello"
        )

    def test_lookup_result_binary_package_name_promise_no_bpn(self) -> None:
        """lookup_result_binary_package_name rejects promise without BPN."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        promise = self.playground.create_bare_data_item(
            collection,
            "test",
            category=BareDataCategory.PROMISE,
            data=DebusinePromise(
                promise_work_request_id=2,
                promise_workflow_id=1,
                promise_category=ArtifactCategory.TEST,
            ),
        )
        result = LookupResult(
            result_type=CollectionItem.Types.BARE, collection_item=promise
        )

        with self.assertRaisesRegex(
            ValueError,
            re.escape(
                f"Cannot determine binary package name for lookup result: "
                f"{result}"
            ),
        ):
            workflow_utils.lookup_result_binary_package_name(result)

    def test_lookup_result_binary_package_name_debian_binary_package(
        self,
    ) -> None:
        """lookup_result_binary_package_name return BPN from a binary."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0.0",
                "deb_fields": {"Package": "hello-bin"},
                "deb_control_files": [],
            },
        )

        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT, artifact=artifact
        )

        self.assertEqual(
            workflow_utils.lookup_result_binary_package_name(result),
            "hello-bin",
        )

    def test_lookup_result_binary_package_name_debian_binary_package_item(
        self,
    ) -> None:
        """lookup_result_binary_package_name return BPN from a binary item."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.BINARY_PACKAGE,
            data={
                "srcpkg_name": "hello",
                "srcpkg_version": "1.0.0",
                "deb_fields": {"Package": "hello-bin"},
                "deb_control_files": [],
            },
        )
        item = CollectionItem.objects.create_from_artifact(
            artifact,
            parent_collection=collection,
            name="hello",
            data={},
            created_by_user=self.playground.get_default_user(),
        )
        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT,
            collection_item=item,
            artifact=artifact,
        )

        self.assertEqual(
            workflow_utils.lookup_result_binary_package_name(result),
            "hello-bin",
        )

    def test_lookup_result_binary_package_name_other_artifacts(self) -> None:
        """lookup_result_binary_package_name with source package artifact."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.SOURCE_PACKAGE,
            data={
                "name": "hello",
                "version": "1.0-1",
                "type": "dpkg",
                "dsc_fields": {"Package": "hello-bin"},
            },
        )
        result = LookupResult(
            result_type=CollectionItem.Types.ARTIFACT, artifact=artifact
        )

        with self.assertRaisesRegex(
            workflow_utils.ArtifactHasNoBinaryPackageName,
            re.escape(f"{DebianSourcePackage!r}"),
        ):
            workflow_utils.lookup_result_binary_package_name(result)

    def test_lookup_result_binary_package_name_collection(self) -> None:
        """lookup_result_binary_package_name with a collection."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        result = LookupResult(
            result_type=CollectionItem.Types.COLLECTION, collection=collection
        )

        with self.assertRaisesRegex(
            ValueError,
            re.escape(
                "Unexpected result: must have collection_item or artifact"
            ),
        ):
            workflow_utils.lookup_result_binary_package_name(result)

    def test_lookup_result_binary_package_name_collection_item_invalid(
        self,
    ) -> None:
        """lookup_result_binary_package_name raise error: invalid BPN type."""
        collection = self.playground.create_collection(
            "test", CollectionCategory.TEST
        )
        promise = self.playground.create_bare_data_item(
            collection,
            "test",
            category=BareDataCategory.PROMISE,
            data={
                "promise_work_request_id": 2,
                "promise_workflow_id": 1,
                "promise_category": ArtifactCategory.TEST,
                "binary_package_name": 000,
            },
        )
        result = LookupResult(
            result_type=CollectionItem.Types.BARE, collection_item=promise
        )

        with self.assertRaisesRegex(
            ValueError,
            "Cannot determine binary package name for lookup result:",
        ):
            workflow_utils.lookup_result_binary_package_name(result)

    def test_lookup_result_binary_package_name_unexpected_result(self) -> None:
        """lookup_result_binary_package_name raise error: unexpected result."""
        result = LookupResult(result_type=CollectionItem.Types.BARE)

        with self.assertRaisesRegex(ValueError, "^Unexpected result: .*"):
            workflow_utils.lookup_result_binary_package_name(result)

    def test_filter_artifact_lookup_by_arch(self) -> None:
        wf = NoopWorkflow(self.playground.create_workflow(task_name="noop"))
        assert wf.work_request.internal_collection
        for architecture in ("amd64", "i386"):
            wf.work_request.internal_collection.manager.add_bare_data(
                name=f"hello-{architecture}",
                category=BareDataCategory.PROMISE,
                workflow=wf.work_request,
                data={
                    "promise_category": ArtifactCategory.BINARY_PACKAGE,
                    "promise_work_request_id": 42,
                    "promise_workflow_id": wf.work_request.id,
                    "architecture": architecture,
                    "binary_package_name": "hello",
                },
                user=self.playground.get_default_user(),
            )
        for architecture in ("armel", "armhf"):
            wf.work_request.internal_collection.manager.add_artifact(
                self.playground.create_minimal_binary_package_artifact(
                    architecture=architecture
                ),
                name=f"hello-{architecture}",
                variables={
                    "architecture": architecture,
                    "binary_package_name": "hello",
                },
                workflow=wf.work_request,
                user=self.playground.get_default_user(),
            )
        self.assertEqual(
            workflow_utils.filter_artifact_lookup_by_arch(
                wf,
                LookupMultiple.parse_obj(
                    [
                        {
                            "collection": "internal@collections",
                            "data__binary_package_name": "hello",
                            "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        }
                    ]
                ),
                ("amd64", "armel", "ppc64el"),
            ),
            LookupMultiple.parse_obj(
                [
                    "internal@collections/name:hello-amd64",
                    "internal@collections/name:hello-armel",
                ]
            ),
        )

    def test_get_architectures(self) -> None:
        wf = NoopWorkflow(self.playground.create_workflow(task_name="noop"))
        assert wf.work_request.internal_collection
        wf.work_request.internal_collection.manager.add_bare_data(
            name="hello-amd64",
            category=BareDataCategory.PROMISE,
            workflow=wf.work_request,
            data={
                "promise_category": ArtifactCategory.BINARY_PACKAGE,
                "promise_work_request_id": 42,
                "promise_workflow_id": wf.work_request.id,
                "architecture": "amd64",
                "binary_package_name": "hello",
            },
            user=self.playground.get_default_user(),
        )
        wf.work_request.internal_collection.manager.add_artifact(
            self.playground.create_minimal_binary_package_artifact(
                architecture="i386"
            ),
            name="hello-i386",
            variables={
                "architecture": "i386",
                "binary_package_name": "hello",
            },
            workflow=wf.work_request,
            user=self.playground.get_default_user(),
        )
        self.assertEqual(
            workflow_utils.get_architectures(
                wf,
                LookupMultiple.parse_obj(
                    [
                        {
                            "collection": "internal@collections",
                            "data__binary_package_name": "hello",
                            "child_type": LookupChildType.ARTIFACT_OR_PROMISE,
                        }
                    ]
                ),
            ),
            {"amd64", "i386"},
        )

    def test_locate_debian_source_package_with_srcpkg(self) -> None:
        """Test with a debian:source-package."""
        source_artifact = self.playground.create_source_artifact()
        result = workflow_utils.locate_debian_source_package(
            "source_artifact", source_artifact
        )
        self.assertEqual(result, source_artifact)

    def test_locate_debian_source_package_with_upload(self) -> None:
        """Test with a debian:source-package."""
        upload_artifacts = self.playground.create_upload_artifacts()
        result = workflow_utils.locate_debian_source_package(
            "upload_artifact", upload_artifacts.upload
        )
        self.assertEqual(result, upload_artifacts.source)

    def test_locate_debian_source_package_with_junk(self) -> None:
        """Test with a debian:source-package."""
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST
        )
        with self.assertRaisesRegex(
            TaskConfigError,
            r"^test_artifact: unexpected artifact category: "
            r"'debusine:test'. Valid categories: "
            r"\['debian:source-package', 'debian:upload'\]$",
        ):
            workflow_utils.locate_debian_source_package(
                "test_artifact", artifact
            )

    def test_follow_artifact_relation_multiple_found(self) -> None:
        """Test with a debian:upload extending multiple srcpkgs."""
        upload_artifacts = self.playground.create_upload_artifacts(source=True)
        source2 = self.playground.create_source_artifact()
        self.playground.create_artifact_relation(
            artifact=upload_artifacts.upload,
            target=source2,
            relation_type=ArtifactRelation.Relations.EXTENDS,
        )

        with self.assertRaisesRegex(
            TaskConfigError,
            r"Multiple artifacts of category debian:source-package with "
            r'a relationship of type extends from ".* debian:upload .*" found',
        ):
            workflow_utils.follow_artifact_relation(
                upload_artifacts.upload,
                ArtifactRelation.Relations.EXTENDS,
                ArtifactCategory.SOURCE_PACKAGE,
            )

    def test_follow_artifact_relation_not_found(self) -> None:
        """Test with a lone debian:upload (no relations)."""
        upload_artifacts = self.playground.create_upload_artifacts(source=False)

        with self.assertRaisesRegex(
            TaskConfigError,
            r"Unable to find an artifact of category debian:source-package "
            r'with a relationship of type extends from ".* debian:upload .*"',
        ):
            workflow_utils.follow_artifact_relation(
                upload_artifacts.upload,
                ArtifactRelation.Relations.EXTENDS,
                ArtifactCategory.SOURCE_PACKAGE,
            )

    def test_follow_artifact_relation_upload(self) -> None:
        """Test finding a source package from an upload."""
        upload_artifacts = self.playground.create_upload_artifacts()
        test_artifact, _ = self.playground.create_artifact()

        # Create some extra unrelated relations
        self.playground.create_artifact_relation(
            artifact=upload_artifacts.upload,
            target=upload_artifacts.binaries[0],
            relation_type=ArtifactRelation.Relations.BUILT_USING,
        )
        self.playground.create_artifact_relation(
            artifact=upload_artifacts.upload,
            target=test_artifact,
            relation_type=ArtifactRelation.Relations.EXTENDS,
        )

        artifact = workflow_utils.follow_artifact_relation(
            upload_artifacts.upload,
            ArtifactRelation.Relations.EXTENDS,
            ArtifactCategory.SOURCE_PACKAGE,
        )
        self.assertEqual(artifact, upload_artifacts.source)

    def test_locate_debian_source_package_lookup_srcpkg(self) -> None:
        """Test locate_debian_source_package_lookup() with a source package."""
        workflow = NoopWorkflow(
            self.playground.create_workflow(task_name="noop")
        )
        source_artifact = self.playground.create_source_artifact()
        self.assertEqual(
            workflow_utils.locate_debian_source_package_lookup(
                workflow, "source_artifact", source_artifact.id
            ),
            source_artifact.id,
        )

    def test_locate_debian_source_package_lookup_upload(self) -> None:
        """Test locate_debian_source_package_lookup() with a debian:upload."""
        workflow = NoopWorkflow(
            self.playground.create_workflow(task_name="noop")
        )
        artifacts = self.playground.create_upload_artifacts()
        self.assertEqual(
            workflow_utils.locate_debian_source_package_lookup(
                workflow, "source_artifact", artifacts.upload.id
            ),
            f"{artifacts.source.id}@artifacts",
        )

    def test_get_source_package_names_invalid_artifact_category(self) -> None:
        source_artifact = self.playground.create_source_artifact(name="hello")

        lookup_result = [
            LookupResult(
                result_type=CollectionItem.Types.ARTIFACT,
                artifact=source_artifact,
            )
        ]

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^testing: unexpected artifact category: 'debian:source-package'. "
            r"Valid categories: "
            r"\['debian:binary-package', 'debian:binary-packages'\]$",
        ):
            workflow_utils.get_source_package_names(
                lookup_result,
                configuration_key="testing",
                artifact_expected_categories=(
                    ArtifactCategory.BINARY_PACKAGE,
                    ArtifactCategory.BINARY_PACKAGES,
                ),
            )

    def test_get_source_package_names_invalid_artifact_in_promise_category(
        self,
    ) -> None:
        lookup_result = [
            LookupResult(
                result_type=CollectionItem.Types.BARE,
                collection_item=CollectionItem(
                    data={
                        "promise_category": ArtifactCategory.TEST,
                        "source_package_name": "python3",
                    }
                ),
            )
        ]

        with self.assertRaisesRegex(
            TaskConfigError,
            r"^testing: unexpected artifact category: 'debusine:test'. "
            r"Valid categories: \['debian:binary-package'\]$",
        ):
            workflow_utils.get_source_package_names(
                lookup_result,
                configuration_key="testing",
                artifact_expected_categories=(ArtifactCategory.BINARY_PACKAGE,),
            )

    def test_get_source_package_names(self) -> None:
        binary_artifact_1 = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello"
            )
        )
        binary_artifact_2 = (
            self.playground.create_minimal_binary_package_artifact(
                srcpkg_name="hello"
            )
        )
        binaries_artifact = (
            self.playground.create_minimal_binary_packages_artifact(
                srcpkg_name="linux-base"
            )
        )
        upload_artifact = self.playground.create_upload_artifacts(
            src_name="firefox"
        ).upload

        lookup_result = [
            LookupResult(
                result_type=CollectionItem.Types.ARTIFACT,
                artifact=binary_artifact_1,
            ),
            LookupResult(
                result_type=CollectionItem.Types.ARTIFACT,
                artifact=binary_artifact_2,
            ),
            LookupResult(
                result_type=CollectionItem.Types.ARTIFACT,
                artifact=binaries_artifact,
            ),
            LookupResult(
                result_type=CollectionItem.Types.ARTIFACT,
                artifact=upload_artifact,
            ),
            LookupResult(
                result_type=CollectionItem.Types.BARE,
                collection_item=CollectionItem(
                    # This isn't valid, without a source_package_name, but it
                    # gives coverage another path
                    data={
                        "promise_category": ArtifactCategory.BINARY_PACKAGE,
                    }
                ),
            ),
            LookupResult(
                result_type=CollectionItem.Types.BARE,
                collection_item=CollectionItem(
                    data={
                        "promise_category": ArtifactCategory.BINARY_PACKAGE,
                        "source_package_name": "python3",
                    }
                ),
            ),
        ]

        self.assertEqual(
            workflow_utils.get_source_package_names(
                lookup_result,
                configuration_key="testing",
                artifact_expected_categories=(
                    ArtifactCategory.BINARY_PACKAGE,
                    ArtifactCategory.BINARY_PACKAGES,
                    ArtifactCategory.UPLOAD,
                ),
            ),
            ["firefox", "hello", "linux-base", "python3"],
        )

    def test_get_available_architectures(self) -> None:
        work_request = self.playground.create_workflow()
        workflow = NoopWorkflow(work_request)
        self.playground.create_debian_environment(
            codename="bookworm", architecture="amd64"
        )
        self.playground.create_debian_environment(
            codename="bookworm", architecture="i386"
        )
        self.assertEqual(
            workflow_utils.get_available_architectures(
                workflow, vendor="debian", codename="bookworm"
            ),
            {"amd64", "i386", "all"},
        )

    def test_get_available_architectures_no_environment(self) -> None:
        work_request = self.playground.create_workflow()
        workflow = NoopWorkflow(work_request)
        self.playground.create_debian_environments_collection()
        with self.assertRaisesRegex(
            TaskConfigError,
            r"Unable to find any environments for debian:bookworm",
        ):
            workflow_utils.get_available_architectures(
                workflow, vendor="debian", codename="bookworm"
            )

    def test_configure_for_overlay_suite_experimental(self) -> None:
        work_request = self.playground.create_workflow()
        workflow = NoopWorkflow(work_request)
        self.playground.create_debian_environment(
            codename="sid",
            variant="sbuild",
            mirror="http://ftp.uk.debian.org/debian",
            components=["main", "contrib"],
        )
        extra_repositories: list[ExtraRepository] | None
        for extra_repositories in (None, []):
            with self.subTest(extra_repositories=extra_repositories):
                result = workflow_utils.configure_for_overlay_suite(
                    workflow,
                    extra_repositories=extra_repositories,
                    vendor="debian",
                    codename="experimental",
                    environment="debian/match:codename=sid",
                    backend=BackendType.UNSHARE,
                    architecture="amd64",
                    try_variant="sbuild",
                )
                self.assertEqual(
                    result,
                    [
                        ExtraRepository(
                            url=pydantic.parse_obj_as(
                                pydantic.AnyUrl,
                                "http://ftp.uk.debian.org/debian",
                            ),
                            suite="experimental",
                            components=["main", "contrib"],
                        ),
                    ],
                )

    def test_configure_for_overlay_suite_no_overlay(self) -> None:
        work_request = self.playground.create_workflow()
        workflow = NoopWorkflow(work_request)
        extra_repositories = [
            ExtraRepository(
                url=pydantic.parse_obj_as(
                    pydantic.AnyUrl, "http://deb.debian.org/debian"
                ),
                suite="foo-backports",
                components=["main"],
            ),
        ]
        for codename in ("sid", "trixie", "bookworm-security"):
            with self.subTest(codename=codename):
                self.playground.create_debian_environment(codename=codename)
                self.assertEqual(
                    workflow_utils.configure_for_overlay_suite(
                        workflow,
                        extra_repositories=extra_repositories,
                        vendor="debian",
                        codename=codename,
                        environment=f"debian/match:codename={codename}",
                        backend=BackendType.UNSHARE,
                        architecture="amd64",
                        try_variant="autopkgtest",
                    ),
                    extra_repositories,
                )
