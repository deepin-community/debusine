# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the lookup views."""

from typing import Any, ClassVar

from django.urls import reverse
from rest_framework import status

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.client.models import LookupChildType, LookupResultType
from debusine.db.models import CollectionItem, WorkRequest, Worker, Workspace
from debusine.server.views.lookups import LookupMultipleView, LookupSingleView
from debusine.server.views.rest import IsWorkerAuthenticated
from debusine.tasks.models import LookupMultiple
from debusine.test.django import TestCase, TestResponseType


class LookupSingleViewTests(TestCase):
    """Tests for LookupSingleView."""

    workspace: ClassVar[Workspace]
    worker: ClassVar[Worker]
    work_request: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.workspace = cls.playground.create_workspace(
            name="public", public=True
        )
        cls.worker = Worker.objects.create_with_fqdn(
            "worker-test", token=cls.playground.create_bare_token()
        )
        cls.work_request = cls.playground.create_work_request(
            worker=cls.worker,
            task_name="noop",
            workspace=cls.workspace,
        )

    def test_unauthenticated(self) -> None:
        """Authentication is required."""
        self.assertIn(
            IsWorkerAuthenticated, LookupSingleView.permission_classes
        )

        response = self.client.post(
            reverse("api:lookup-single"),
            data={
                "lookup": "",
                "work_request": self.work_request.id,
                "expect_type": LookupChildType.ARTIFACT,
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.json(),
            {
                "title": "Error",
                "detail": "Authentication credentials were not provided.",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_work_request_not_found(self) -> None:
        """The work request must exist."""
        assert self.worker.token is not None
        response = self.client.post(
            reverse("api:lookup-single"),
            data={
                "lookup": "x",
                "work_request": self.work_request.id + 1,
                "expect_type": LookupChildType.ARTIFACT,
            },
            headers={"token": self.worker.token.key},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response, "Error", detail_pattern="Invalid pk"
        )

    def test_unauthorized(self) -> None:
        """The work request must be assigned to this worker."""
        assert self.worker.token is not None
        another_worker = Worker.objects.create_with_fqdn(
            "another-worker-test", token=self.playground.create_bare_token()
        )
        another_work_request = self.playground.create_work_request(
            worker=another_worker, task_name="noop"
        )

        response = self.client.post(
            reverse("api:lookup-single"),
            data={
                "lookup": "x",
                "work_request": another_work_request.id,
                "expect_type": LookupChildType.ARTIFACT,
            },
            headers={"token": self.worker.token.key},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response,
            f"Work request {another_work_request.id} is not assigned to the "
            f"authenticated worker",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    def post_lookup(
        self,
        lookup: int | str,
        expect_type: LookupChildType,
        default_category: CollectionCategory | None = None,
    ) -> TestResponseType:
        """Make a lookup request."""
        data = {
            "lookup": lookup,
            "work_request": self.work_request.id,
            "expect_type": expect_type,
            "default_category": default_category,
        }
        assert self.worker.token is not None
        return self.client.post(
            reverse("api:lookup-single"),
            data=data,
            headers={"token": self.worker.token.key},
            content_type="application/json",
        )

    def test_success_string(self) -> None:
        """A string lookup succeeds and returns an artifact ID."""
        artifact = self.playground.create_artifact(workspace=self.workspace)[0]

        response = self.post_lookup(
            f"{artifact.id}@artifacts", LookupChildType.ARTIFACT
        )

        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data,
            {
                "result_type": LookupResultType.ARTIFACT,
                "collection_item": None,
                "artifact": artifact.id,
                "collection": None,
            },
        )

    def test_success_integer(self) -> None:
        """An integer lookup succeeds and returns an artifact ID."""
        artifact = self.playground.create_artifact(workspace=self.workspace)[0]

        response = self.post_lookup(artifact.id, LookupChildType.ARTIFACT)

        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data,
            {
                "result_type": LookupResultType.ARTIFACT,
                "collection_item": None,
                "artifact": artifact.id,
                "collection": None,
            },
        )

    def test_key_error(self) -> None:
        """The view returns an error if the lookup returns no items."""
        response = self.post_lookup(
            "nonsense@artifacts", LookupChildType.ARTIFACT
        )

        self.assertResponseProblem(
            response,
            "No matches",
            detail_pattern="'nonsense@artifacts' does not exist or is hidden",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_lookup_error(self) -> None:
        """The view returns an error if the lookup is invalid."""
        response = self.post_lookup(
            "internal@collections", LookupChildType.ARTIFACT
        )

        self.assertResponseProblem(
            response,
            "Lookup error",
            detail_pattern=(
                "internal@collections is only valid in the context of a "
                "workflow"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    def test_wrong_workspace(self) -> None:
        """The item must be in a visible workspace."""
        workspace = self.playground.create_workspace(name="test")
        artifact = self.playground.create_artifact(workspace=workspace)[0]
        lookup = f"{artifact.id}@artifacts"

        response = self.post_lookup(lookup, LookupChildType.ARTIFACT)

        self.assertResponseProblem(
            response,
            "No matches",
            detail_pattern=f"{lookup!r} does not exist or is hidden",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_wrong_type(self) -> None:
        """The item must be of the correct type."""
        artifact = self.playground.create_artifact(workspace=self.workspace)[0]
        lookup = f"{artifact.id}@artifacts"

        response = self.post_lookup(lookup, LookupChildType.COLLECTION)

        self.assertResponseProblem(
            response,
            "Lookup error",
            detail_pattern=f"{lookup!r} is of type 'artifact'"
            " instead of expected 'collection'",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    def test_default_category(self) -> None:
        """The client can request a default category."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST, workspace=self.workspace
        )

        response = self.post_lookup("collection", LookupChildType.COLLECTION)

        self.assertResponseProblem(
            response,
            "Lookup error",
            detail_pattern=(
                "'collection' does not specify a category and the context "
                "does not supply a default"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

        response = self.post_lookup(
            "collection",
            LookupChildType.COLLECTION,
            default_category=CollectionCategory.TEST,
        )

        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data,
            {
                "result_type": LookupResultType.COLLECTION,
                "collection_item": None,
                "artifact": None,
                "collection": collection.id,
            },
        )


class LookupMultipleViewTests(TestCase):
    """Tests for LookupMultipleView."""

    workspace: ClassVar[Workspace]
    worker: ClassVar[Worker]
    work_request: ClassVar[WorkRequest]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.workspace = cls.playground.create_workspace(
            name="public", public=True
        )
        cls.worker = Worker.objects.create_with_fqdn(
            "worker-test", token=cls.playground.create_bare_token()
        )
        cls.work_request = cls.playground.create_work_request(
            worker=cls.worker, task_name="noop", workspace=cls.workspace
        )

    def test_unauthenticated(self) -> None:
        """Authentication is required."""
        self.assertIn(
            IsWorkerAuthenticated, LookupMultipleView.permission_classes
        )

        response = self.client.post(
            reverse("api:lookup-multiple"),
            data={
                "lookup": {"collection": "test"},
                "work_request": self.work_request.id,
                "expect_type": LookupChildType.ARTIFACT,
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.json(),
            {
                "title": "Error",
                "detail": "Authentication credentials were not provided.",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_work_request_not_found(self) -> None:
        """The work request must exist."""
        assert self.worker.token is not None
        response = self.client.post(
            reverse("api:lookup-multiple"),
            data={
                "lookup": {"collection": "test"},
                "work_request": self.work_request.id + 1,
                "expect_type": LookupChildType.ARTIFACT,
            },
            headers={"token": self.worker.token.key},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response, "Error", detail_pattern="Invalid pk"
        )

    def test_unauthorized(self) -> None:
        """The work request must be assigned to this worker."""
        assert self.worker.token is not None
        another_worker = Worker.objects.create_with_fqdn(
            "another-worker-test", token=self.playground.create_bare_token()
        )
        another_work_request = self.playground.create_work_request(
            worker=another_worker, task_name="noop"
        )

        response = self.client.post(
            reverse("api:lookup-multiple"),
            data={
                "lookup": {"collection": "test"},
                "work_request": another_work_request.id,
                "expect_type": LookupChildType.ARTIFACT,
            },
            headers={"token": self.worker.token.key},
            content_type="application/json",
        )

        self.assertResponseProblem(
            response,
            f"Work request {another_work_request.id} is not assigned to the "
            f"authenticated worker",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    def post_lookup(
        self,
        lookup: dict[str, Any] | list[str | dict[str, Any]],
        expect_type: LookupChildType,
        default_category: CollectionCategory | None = None,
    ) -> TestResponseType:
        """Make a lookup request."""
        data = {
            "lookup": lookup,
            "work_request": self.work_request.id,
            "expect_type": expect_type,
            "default_category": default_category,
        }
        assert self.worker.token
        return self.client.post(
            reverse("api:lookup-multiple"),
            data=data,
            headers={"token": self.worker.token.key},
            content_type="application/json",
        )

    def test_success(self) -> None:
        """The lookup succeeds and returns an artifact ID."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST, workspace=self.workspace
        )
        artifacts = [
            self.playground.create_artifact(category=ArtifactCategory.TEST)[0]
            for _ in range(2)
        ]
        collection_items = [
            CollectionItem.objects.create(
                parent_collection=collection,
                name=f"artifact{i}",
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.TEST,
                artifact=artifact,
                data={},
                created_by_user=self.playground.get_default_user(),
            )
            for i, artifact in enumerate(artifacts)
        ]

        response = self.post_lookup(
            {"collection": f"collection@{CollectionCategory.TEST}"},
            LookupChildType.ARTIFACT,
        )

        data = self.assertAPIResponseOk(response)
        self.assertCountEqual(
            data,
            [
                {
                    "result_type": LookupResultType.ARTIFACT,
                    "collection_item": collection_item.id,
                    "artifact": artifact.id,
                    "collection": None,
                }
                for artifact, collection_item in zip(
                    artifacts, collection_items
                )
            ],
        )

    def test_normalized(self) -> None:
        """The view accepts fully-normalized lookups."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST, workspace=self.workspace
        )
        artifacts = [
            self.playground.create_artifact(category=ArtifactCategory.TEST)[0]
            for _ in range(2)
        ]
        collection_items = [
            CollectionItem.objects.create(
                parent_collection=collection,
                name=f"artifact{i}",
                child_type=CollectionItem.Types.ARTIFACT,
                category=ArtifactCategory.TEST,
                artifact=artifact,
                data={},
                created_by_user=self.playground.get_default_user(),
            )
            for i, artifact in enumerate(artifacts)
        ]

        lookup = LookupMultiple.parse_obj(
            {"collection": f"collection@{CollectionCategory.TEST}"}
        ).dict()["__root__"]
        self.assertEqual(
            lookup,
            (
                {
                    "category": None,
                    "child_type": "artifact",
                    "collection": f"collection@{CollectionCategory.TEST}",
                    "data_matchers": (),
                    "name_matcher": None,
                    "lookup_filters": (),
                },
            ),
        )

        response = self.post_lookup(lookup, LookupChildType.ARTIFACT)

        data = self.assertAPIResponseOk(response)
        self.assertCountEqual(
            data,
            [
                {
                    "result_type": LookupResultType.ARTIFACT,
                    "collection_item": collection_item.id,
                    "artifact": artifact.id,
                    "collection": None,
                }
                for artifact, collection_item in zip(
                    artifacts, collection_items
                )
            ],
        )

    def test_deserialization_error(self) -> None:
        """The view returns an error if it cannot deserialize the lookup."""
        response = self.post_lookup(
            "nonsense", LookupChildType.ARTIFACT  # type: ignore[arg-type]
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.headers["Content-Type"], "application/problem+json"
        )
        self.assertEqual(response.json()["title"], "Cannot deserialize lookup")
        self.assertRegex(
            response.json()["validation_errors"]["lookup"],
            r"^1 validation error for LookupMultiple",
        )

    def test_key_error(self) -> None:
        """The view returns an empty list if the lookup returns no items."""
        response = self.post_lookup(
            {"collection": "nonexistent@test"}, LookupChildType.ARTIFACT
        )

        self.assertResponseProblem(
            response,
            "One of the lookups returned no matches",
            detail_pattern="'nonexistent@test' does not exist or is hidden",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_lookup_error(self) -> None:
        """The view returns an error if the lookup is invalid."""
        response = self.post_lookup(
            {"collection": "internal@collections"},
            LookupChildType.ARTIFACT,
        )

        self.assertResponseProblem(
            response,
            "Lookup error",
            detail_pattern=(
                "internal@collections is only valid in the context of a "
                "workflow"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    def test_wrong_workspace(self) -> None:
        """The item must be in a visible workspace."""
        workspace = self.playground.create_workspace(name="test")
        self.playground.create_collection(
            "collection", CollectionCategory.TEST, workspace=workspace
        )

        response = self.post_lookup(
            [{"collection": f"collection@{CollectionCategory.TEST}"}],
            LookupChildType.ARTIFACT,
        )

        self.assertResponseProblem(
            response,
            "One of the lookups returned no matches",
            detail_pattern=(
                f"'collection@{CollectionCategory.TEST}' does not exist or is "
                f"hidden"
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_wrong_type(self) -> None:
        """The lookup must be of the correct type."""
        self.playground.create_collection(
            "collection", CollectionCategory.TEST, workspace=self.workspace
        )

        response = self.post_lookup(
            {
                "collection": f"collection@{CollectionCategory.TEST}",
                "child_type": "bare",
            },
            LookupChildType.COLLECTION,
        )

        self.assertResponseProblem(
            response,
            "Lookup error",
            detail_pattern=(
                "Only lookups for type 'collection' are allowed here"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    def test_default_category(self) -> None:
        """The client can request a default category."""
        collection = self.playground.create_collection(
            "collection", CollectionCategory.TEST, workspace=self.workspace
        )
        artifact = self.playground.create_artifact(
            category=ArtifactCategory.TEST
        )[0]
        collection_item = CollectionItem.objects.create(
            parent_collection=collection,
            name="artifact",
            child_type=CollectionItem.Types.ARTIFACT,
            category=ArtifactCategory.TEST,
            artifact=artifact,
            data={},
            created_by_user=self.playground.get_default_user(),
        )

        response = self.post_lookup(
            {"collection": "collection"}, LookupChildType.ARTIFACT
        )

        self.assertResponseProblem(
            response,
            "Lookup error",
            detail_pattern=(
                "'collection' does not specify a category and the context "
                "does not supply a default"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

        response = self.post_lookup(
            {"collection": "collection"},
            LookupChildType.ARTIFACT,
            default_category=CollectionCategory.TEST,
        )

        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data,
            [
                {
                    "result_type": LookupResultType.ARTIFACT,
                    "collection_item": collection_item.id,
                    "artifact": artifact.id,
                    "collection": None,
                }
            ],
        )
