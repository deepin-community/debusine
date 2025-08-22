# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the base Workflow classes."""
import abc

from debusine.artifacts.models import (
    ArtifactCategory,
    BareDataCategory,
    TaskTypes,
)
from debusine.client.models import LookupChildType
from debusine.db.models import CollectionItem, WorkRequest, default_workspace
from debusine.server.workflows import NoopWorkflow, Workflow
from debusine.server.workflows.models import (
    BaseWorkflowData,
    WorkRequestWorkflowData,
)
from debusine.server.workflows.tests.helpers import SampleWorkflow
from debusine.signing.tasks.models import DebsignData
from debusine.tasks import BaseTask
from debusine.tasks.models import (
    ActionTypes,
    BaseDynamicTaskData,
    LookupMultiple,
)
from debusine.test.django import TestCase
from debusine.test.test_utils import preserve_task_registry


class WorkflowTests(TestCase):
    """Unit tests for Workflow class."""

    @preserve_task_registry()
    def test_create(self) -> None:
        """Test instantiating a Workflow."""

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Concrete workflow instance to use for tests."""

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        wr = WorkRequest(task_data={}, workspace=default_workspace())
        wf = ExampleWorkflow(wr)
        self.assertEqual(wf.data, BaseWorkflowData())
        self.assertEqual(wf.work_request, wr)
        self.assertEqual(wf.workspace, default_workspace())
        self.assertEqual(wf.work_request_id, wr.id)
        self.assertEqual(wf.workspace_name, wr.workspace.name)

    @preserve_task_registry()
    def test_registration(self) -> None:
        """Test class subclass registry."""

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Concrete workflow instance to use for tests."""

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        class ExampleWorkflowABC(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData],
            abc.ABC,
        ):
            """Abstract workflow subclass to use for tests."""

        class ExampleWorkflowName(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Workflow subclass with a custom name."""

            TASK_NAME = "examplename"

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        # name gets filled
        self.assertEqual(ExampleWorkflow.name, "exampleworkflow")
        self.assertEqual(ExampleWorkflowABC.name, "exampleworkflowabc")
        self.assertEqual(ExampleWorkflowName.name, "examplename")

        # Subclasses that are not ABC get registered
        self.assertIn(
            "exampleworkflow", BaseTask._sub_tasks[TaskTypes.WORKFLOW]
        )
        self.assertNotIn(
            "exampleworkflowabc", BaseTask._sub_tasks[TaskTypes.WORKFLOW]
        )
        self.assertIn("examplename", BaseTask._sub_tasks[TaskTypes.WORKFLOW])

    @preserve_task_registry()
    def test_provides_artifact(self) -> None:
        """Test provides_artifact() update event_reactions.on_creation."""

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Concrete workflow instance to use for tests."""

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        workflow_root = self.playground.create_workflow()

        child = workflow_root.create_child("noop")

        workflow = ExampleWorkflow(child)

        self.assert_work_request_event_reactions(child, on_creation=[])

        name = "hello_1:1.0-1|testing"
        data = {"architecture": "amd64"}
        category = ArtifactCategory.TEST
        artifact_filters = {"xxx": "yyy"}

        workflow.provides_artifact(
            child, category, name, data=data, artifact_filters=artifact_filters
        )

        promise = CollectionItem.objects.active().get(
            parent_collection=workflow_root.internal_collection, name=name
        )
        self.assertEqual(promise.child_type, CollectionItem.Types.BARE)
        self.assertEqual(promise.category, BareDataCategory.PROMISE)
        self.assertEqual(
            promise.data,
            {
                "promise_work_request_id": child.id,
                "promise_workflow_id": workflow_root.id,
                "promise_category": category,
                **data,
            },
        )
        on_success = [
            {
                "action": ActionTypes.UPDATE_COLLECTION_WITH_ARTIFACTS,
                "collection": "internal@collections",
                "name_template": name,
                "variables": data,
                "artifact_filters": {
                    **artifact_filters,
                    "category": category,
                },
                "created_at": None,
            }
        ]
        self.assert_work_request_event_reactions(child, on_success=on_success)

        # provides_artifact is idempotent.
        artifact, _ = self.playground.create_artifact()
        assert workflow_root.internal_collection is not None
        workflow_root.internal_collection.manager.add_artifact(
            artifact,
            user=child.created_by,
            workflow=workflow_root,
            variables={},
            name=name,
            replace=True,
        )

        workflow.provides_artifact(
            child, category, name, data=data, artifact_filters=artifact_filters
        )

        item = CollectionItem.objects.active().get(
            parent_collection=workflow_root.internal_collection, name=name
        )
        self.assertEqual(item.child_type, CollectionItem.Types.ARTIFACT)
        self.assertEqual(item.category, ArtifactCategory.TEST)
        self.assertEqual(item.artifact, artifact)
        self.assert_work_request_event_reactions(child, on_success=on_success)

    @preserve_task_registry()
    def test_provides_artifact_bad_name(self) -> None:
        r"""Test requires_artifact() ValueError: name contains a slash."""

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Concrete workflow instance to use for tests."""

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        workflow_root = self.playground.create_workflow()

        child = workflow_root.create_child("noop")

        workflow = ExampleWorkflow(child)

        with self.assertRaisesRegex(
            ValueError, r'Collection item name may not contain "/"\.'
        ):
            workflow.provides_artifact(
                child, ArtifactCategory.TEST, "prefix/testing"
            )

    @preserve_task_registry()
    def test_provides_artifact_bad_data_key(self) -> None:
        r"""Test requires_artifact() ValueError: key starts with promise\_."""

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Concrete workflow instance to use for tests."""

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        workflow_root = self.playground.create_workflow()

        child = workflow_root.create_child("noop")

        workflow = ExampleWorkflow(child)

        key = "promise_key"
        msg = f'Field name "{key}" starting with "promise_" is not allowed.'
        with self.assertRaisesRegex(ValueError, msg):
            workflow.provides_artifact(
                child, ArtifactCategory.TEST, "testing", data={key: "value"}
            )

    @preserve_task_registry()
    def test_requires_artifact_lookup_single(self) -> None:
        """Test requires_artifact() call work_request.add_dependency()."""

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Concrete workflow instance to use for tests."""

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        workflow_root = self.playground.create_workflow()

        # Provides the relevant artifact
        child_provides_1 = workflow_root.create_child("noop")

        # Provides a non-relevant artifact (for the Lookup query of
        # the requires_artifact()
        child_provides_2 = workflow_root.create_child("noop")

        child_requires = workflow_root.create_child("noop")

        workflow = ExampleWorkflow(child_provides_1)

        self.assertEqual(child_requires.dependencies.count(), 0)

        workflow.provides_artifact(
            child_provides_1, ArtifactCategory.TEST, "build-amd64"
        )
        workflow.provides_artifact(
            child_provides_2, ArtifactCategory.TEST, "build-i386"
        )
        child_provides_1.process_event_reactions("on_creation")
        child_provides_2.process_event_reactions("on_creation")

        workflow.requires_artifact(
            child_requires, "internal@collections/name:build-amd64"
        )

        self.assertEqual(child_requires.dependencies.count(), 1)
        self.assertQuerySetEqual(
            child_requires.dependencies.all(), [child_provides_1]
        )

        # Calling requires_artifact() if it was already required: is a noop
        workflow.requires_artifact(
            child_requires, "internal@collections/name:build-amd64"
        )
        self.assertEqual(child_requires.dependencies.count(), 1)

    @preserve_task_registry()
    def test_requires_artifact_lookup_multiple(self) -> None:
        """Test requires_artifact() call work_request.add_dependency()."""

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Concrete workflow instance to use for tests."""

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        workflow_root = self.playground.create_workflow()
        assert workflow_root.internal_collection is not None

        # Providers that will match
        child_provides_1 = workflow_root.create_child("noop")
        child_provides_2 = workflow_root.create_child("noop")

        # Requirer
        child_requires = workflow_root.create_child("noop")

        workflow = ExampleWorkflow(child_provides_1)

        self.assertEqual(child_requires.dependencies.count(), 0)

        workflow.provides_artifact(
            child_provides_1, ArtifactCategory.TEST, "build-amd64"
        )
        workflow.provides_artifact(
            child_provides_2, ArtifactCategory.TEST, "build-i386"
        )
        child_provides_1.process_event_reactions("on_creation")
        child_provides_2.process_event_reactions("on_creation")

        # Add manually CollectionItem with a category != PROMISE for
        # unit testing coverage
        CollectionItem.objects.create_from_bare_data(
            BareDataCategory.PACKAGE_BUILD_LOG,
            parent_collection=workflow_root.internal_collection,
            name="not-relevant",
            data={},
            created_by_user=workflow_root.created_by,
            created_by_workflow=workflow_root,
        )

        workflow.requires_artifact(
            child_requires,
            LookupMultiple.parse_obj(
                {
                    "collection": "internal@collections",
                    "child_type": LookupChildType.BARE,
                }
            ),
        )

        self.assertEqual(child_requires.dependencies.count(), 2)
        self.assertQuerySetEqual(
            child_requires.dependencies.all(),
            [child_provides_1, child_provides_2],
        )

    def test_lookup(self) -> None:
        """Test lookup of Workflow orchestrators."""
        self.assertEqual(
            BaseTask.class_from_name(TaskTypes.WORKFLOW, "noop"), NoopWorkflow
        )
        self.assertEqual(Workflow.from_name("noop"), NoopWorkflow)

    @preserve_task_registry()
    def test_work_request_ensure_child(self) -> None:
        """Test work_request_ensure_child create or return work request."""

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Concrete workflow instance to use for tests."""

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        workflow_root = self.playground.create_workflow(
            task_name="exampleworkflow", task_data={}
        )

        w = ExampleWorkflow(workflow_root)

        self.assertEqual(w.work_request.children.count(), 0)

        wr_created = w.work_request_ensure_child(
            task_type=TaskTypes.SIGNING,
            task_name="debsign",
            task_data=DebsignData(unsigned=10, key="test"),
            workflow_data=WorkRequestWorkflowData(
                display_name="Sign upload",
                step="debsign",
            ),
        )

        # One work_request got created
        self.assertEqual(w.work_request.children.count(), 1)

        # Try creating a new one (same task_name, task_data, workflow_data...)
        wr_returned = w.work_request_ensure_child(
            task_type=TaskTypes.SIGNING,
            task_name="debsign",
            task_data=DebsignData(unsigned=10, key="test"),
            workflow_data=WorkRequestWorkflowData(
                display_name="Sign upload",
                step="debsign",
            ),
        )

        # No new work request created
        self.assertEqual(w.work_request.children.count(), 1)

        # Returned the same as had been created
        self.assertEqual(wr_created, wr_returned)

    @preserve_task_registry()
    def test_get_label(self) -> None:
        workflow_root = self.playground.create_workflow()

        class ExampleWorkflow(
            SampleWorkflow[BaseWorkflowData, BaseDynamicTaskData]
        ):
            """Concrete workflow instance to use for tests."""

            def populate(self) -> None:
                """Unused abstract method from Workflow."""
                raise NotImplementedError()

        workflow = ExampleWorkflow(workflow_root)

        self.assertEqual(workflow.get_label(), "noop-template")

        workflow_root.workflow_data_json = {"workflow_template_name": None}

        self.assertEqual(workflow.get_label(), "noop")
