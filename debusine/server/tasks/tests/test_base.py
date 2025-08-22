# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for base server-side task classes."""

import re
from pathlib import Path
from typing import ClassVar
from unittest import mock

from debusine.artifacts.local_artifact import WorkRequestDebugLogs
from debusine.db.context import context
from debusine.db.models import Artifact, ArtifactRelation, WorkRequest
from debusine.db.models.permissions import format_permission_check_error
from debusine.server.tasks.base import ServerTaskPermissionDenied
from debusine.server.tasks.tests.helpers import SampleBaseServerTask
from debusine.tasks.models import BaseDynamicTaskData, BaseTaskData, WorkerType
from debusine.test.django import TestCase
from debusine.worker.system_information import host_architecture


class SampleBaseServerTask1(
    SampleBaseServerTask[BaseTaskData, BaseDynamicTaskData]
):
    """Sample class to test BaseServerTask class."""

    def _execute(self) -> bool:
        """Unused abstract method from BaseTask."""
        raise NotImplementedError()


class SampleBaseServerTask2Data(BaseTaskData):
    """Data representation for SampleBaseServerTask2."""

    foo: str


class SampleBaseServerTask2(
    SampleBaseServerTask[SampleBaseServerTask2Data, BaseDynamicTaskData]
):
    """Test BaseServerTask class with jsonschema validation."""

    TASK_VERSION = 1

    def _execute(self) -> bool:
        """Unused abstract method from BaseTask."""
        raise NotImplementedError()


class BaseServerTaskTests(TestCase):
    """Unit tests for :py:class:`BaseServerTask`."""

    task: ClassVar[SampleBaseServerTask1]
    task2: ClassVar[SampleBaseServerTask2]
    worker_metadata: ClassVar[dict[str, WorkerType]]

    @classmethod
    def setUpTestData(cls) -> None:
        """Create the shared attributes."""
        super().setUpTestData()
        cls.task = SampleBaseServerTask1({})
        cls.task2 = SampleBaseServerTask2({"foo": "bar"})
        cls.worker_metadata = {"system:worker_type": WorkerType.CELERY}

    def test_can_run_on_no_version(self) -> None:
        """Ensure can_run_on returns True if no version is specified."""
        self.assertIsNone(self.task.TASK_VERSION)
        metadata = {**self.worker_metadata, **self.task.analyze_worker()}
        self.assertEqual(self.task.can_run_on(metadata), True)

    def test_can_run_on_with_different_versions(self) -> None:
        """Ensure can_run_on returns False if versions differ."""
        self.assertIsNone(self.task.TASK_VERSION)
        metadata = {**self.worker_metadata, **self.task.analyze_worker()}
        metadata["server:samplebaseservertask1:version"] = 1
        self.assertEqual(self.task.can_run_on(metadata), False)

    def test_set_work_request(self) -> None:
        """set_work_request sets appropriate attributes."""
        work_request = self.playground.create_work_request()

        self.task.set_work_request(work_request)

        self.assertEqual(self.task.work_request, work_request)
        self.assertEqual(self.task.workspace, work_request.workspace)
        self.assertEqual(self.task.work_request_id, work_request.id)
        self.assertEqual(self.task.workspace_name, work_request.workspace.name)
        self.assertEqual(
            self.task.worker_host_architecture, host_architecture()
        )

    def test_execute_sets_up_context(self) -> None:
        """`execute` sets up a suitable context."""
        work_request = self.playground.create_work_request()
        self.task.set_work_request(work_request)

        def fake_execute() -> bool:
            self.assertEqual(context.scope, work_request.workspace.scope)
            self.assertEqual(context.user, work_request.created_by)
            self.assertEqual(context.workspace, work_request.workspace)
            return True

        with mock.patch.object(
            SampleBaseServerTask1, "_execute", side_effect=fake_execute
        ):
            self.task.execute()

        self.assertIsNone(context.scope)
        self.assertIsNone(context.user)
        self.assertIsNone(context.workspace)

    def setup_upload_work_request_debug_logs(
        self, source_artifacts: list[Artifact] | None = None
    ) -> list[tuple[str, bytes]]:
        """Setup for upload_work_request_debug_logs tests."""  # noqa: D401
        # Add a file to be uploaded
        with self.task.open_debug_log_file("test.log") as file:
            file.write("log")
        assert self.task._debug_log_files_directory is not None

        self.task.set_work_request(self.playground.create_work_request())
        self.task._source_artifacts_ids = [
            artifact.id for artifact in source_artifacts or []
        ]

        return [
            (path.name, path.read_bytes())
            for path in Path(
                self.task._debug_log_files_directory.name
            ).iterdir()
        ]

    def assert_uploaded_work_request_debug_logs_artifact(
        self,
        work_request: WorkRequest,
        expected_files: list[tuple[str, bytes]],
    ) -> Artifact:
        """Assert that an Artifact was created."""
        artifact = Artifact.objects.filter(
            category=WorkRequestDebugLogs._category
        ).last()
        assert artifact is not None
        assert artifact.created_by_work_request is not None

        self.assertEqual(artifact.category, WorkRequestDebugLogs._category)
        self.assertEqual(artifact.workspace, work_request.workspace)
        self.assertEqual(artifact.data, {})
        self.assertEqual(artifact.created_by_work_request, work_request)
        for file_in_artifact, (name, contents) in zip(
            artifact.fileinartifact_set.order_by("id"), expected_files
        ):
            self.assertEqual(file_in_artifact.path, name)
            file_backend = work_request.workspace.scope.download_file_backend(
                file_in_artifact.file
            )
            with file_backend.get_stream(file_in_artifact.file) as file:
                self.assertEqual(file.read(), contents)

        return artifact

    @context.disable_permission_checks()
    def test_upload_work_request_debug_logs_with_relation(self) -> None:
        """
        Artifact is created and uploaded. Relation is created.

        The relation is from the debug logs artifact to the source_artifact_id.
        """
        source_artifact = self.playground.create_artifact()[0]
        expected_files = self.setup_upload_work_request_debug_logs(
            [source_artifact]
        )
        assert self.task.work_request is not None

        self.task._upload_work_request_debug_logs()

        artifact = self.assert_uploaded_work_request_debug_logs_artifact(
            self.task.work_request, expected_files
        )

        self.assertEqual(artifact.relations.count(), 1)
        relation = artifact.relations.first()
        assert relation is not None
        self.assertEqual(relation.artifact, artifact)
        self.assertEqual(relation.target, source_artifact)
        self.assertEqual(relation.type, ArtifactRelation.Relations.RELATES_TO)

    @context.disable_permission_checks()
    def test_upload_work_request_debug_logs(self) -> None:
        """Artifact is created and uploaded."""
        expected_files = self.setup_upload_work_request_debug_logs()
        assert self.task.work_request is not None

        self.task._upload_work_request_debug_logs()

        self.assert_uploaded_work_request_debug_logs_artifact(
            self.task.work_request, expected_files
        )

    def test_upload_work_request_no_log_files(self) -> None:
        """No log files: no artifact created."""
        self.task._upload_work_request_debug_logs()

        self.assertFalse(
            Artifact.objects.filter(
                category=WorkRequestDebugLogs._category
            ).exists()
        )

    def test_enforce(self) -> None:
        """Test enforcing permissions."""
        work_request = self.playground.create_work_request()
        work_request.set_current()
        self.task.set_work_request(work_request)

        workspace = self.playground.get_default_workspace()

        self.task.enforce(workspace.can_display)

        with self.assertRaisesRegex(
            ServerTaskPermissionDenied,
            re.escape(
                format_permission_check_error(
                    workspace.can_create_work_requests, work_request.created_by
                )
            ),
        ):
            self.task.enforce(workspace.can_create_work_requests)
