# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the test playground functions."""

from typing import ClassVar

import debusine.db.playground
from debusine.db.context import context
from debusine.db.models import Artifact, FileInArtifact, FileStore, Workspace
from debusine.db.playground import scenarios
from debusine.test.django import (
    PlaygroundTestCase,
    TestCase,
    TransactionTestCase,
)
from debusine.test.playground import Playground


class IntScenario(scenarios.Scenario):
    """Scenario used to test modification isolation between test cases."""

    value: int

    def build(self, playground: debusine.db.playground.Playground) -> None:
        """Build the scenario."""
        super().build(playground)
        self.value = 0


class ScenarioTestCase(PlaygroundTestCase):
    """Common scenario-related tests."""

    scenario = scenarios.DefaultContext()
    int_scenario = IntScenario()

    def test_scenario_instantiated(self) -> None:
        """Test that the scenario has been instantiated."""
        self.assertIsNot(self.scenario, self.__class__.scenario)
        self.assertEqual(
            getattr(self, "playground").scenarios["scenario"], self.scenario
        )
        self.assertIsNotNone(self.scenario.scope)
        self.assertIsNone(context.scope)

    def test_scenario_local_changes1(self) -> None:
        """Test local changes isolation between methods."""
        self.assertEqual(self.int_scenario.value, 0)
        self.int_scenario.value = 1

    def test_scenario_local_changes2(self) -> None:
        """Test local changes isolation between methods."""
        self.assertEqual(self.int_scenario.value, 0)
        self.int_scenario.value = 1


class MemoryPlaygroundTestCaseTest(ScenarioTestCase, TestCase):
    """Test instantiation of playground in normal test case."""

    def test_instantiated(self) -> None:
        """Playground is instantiated."""
        self.assertIsInstance(self.playground, Playground)
        self.assertEqual(
            self.playground.file_store.backend, FileStore.BackendChoices.MEMORY
        )


class LocalPlaygroundTestCaseTest(ScenarioTestCase, TestCase):
    """Test instantiation of playground in normal test case."""

    playground_memory_file_store = False
    scenario = scenarios.DefaultContext()

    def test_instantiated(self) -> None:
        """Playground is instantiated."""
        self.assertIsInstance(self.playground, Playground)
        self.assertEqual(
            self.playground.file_store.backend, FileStore.BackendChoices.LOCAL
        )


class NoPlaygroundTestCaseTest(TestCase):
    """Test instantiation of playground in normal test case."""

    playground_needed = False
    scenario = scenarios.DefaultContext()

    def test_instantiated(self) -> None:
        """Playground is instantiated."""
        self.assertFalse(hasattr(self, "playground"))

    def test_scenario_not_instantiated(self) -> None:
        """Test that the scenario has not been instantiated."""
        self.assertFalse(hasattr(self.scenario, "scope"))
        self.assertIsNone(context.scope)


class MemoryPlaygroundTransactionTestCaseTest(
    ScenarioTestCase, TransactionTestCase
):
    """Test instantiation of playground in normal test case."""

    scenario = scenarios.DefaultContext()

    def test_instantiated(self) -> None:
        """Playground is instantiated."""
        self.assertIsInstance(self.playground, Playground)
        self.assertEqual(
            self.playground.file_store.backend, FileStore.BackendChoices.MEMORY
        )


class LocalPlaygroundTransactionTestCaseTest(
    ScenarioTestCase, TransactionTestCase
):
    """Test instantiation of playground in normal test case."""

    playground_memory_file_store = False
    scenario = scenarios.DefaultContext()

    def test_instantiated(self) -> None:
        """Playground is instantiated."""
        self.assertIsInstance(self.playground, Playground)
        self.assertEqual(
            self.playground.file_store.backend, FileStore.BackendChoices.LOCAL
        )


class NoPlaygroundTransactionTestCaseTest(TransactionTestCase):
    """Test instantiation of playground in normal test case."""

    playground_needed = False
    scenario = scenarios.DefaultContext()

    def test_instantiated(self) -> None:
        """Playground is instantiated."""
        self.assertFalse(hasattr(self, "playground"))

    def test_scenario_not_instantiated(self) -> None:
        """Test that the scenario has not been instantiated."""
        self.assertFalse(hasattr(self.scenario, "scope"))
        self.assertIsNone(context.scope)


class _InheritedScenarioTestBase(TestCase):
    scenario1 = scenarios.DefaultContext()


class InheritedScenarioTest(_InheritedScenarioTestBase):
    """Test inheritance of scenario definitions."""

    scenario2 = scenarios.DefaultContext()

    def test_list_playground_scenarios(self) -> None:
        """Test list_playground_scenarios method."""
        self.assertEqual(
            list(_InheritedScenarioTestBase.list_playground_scenarios()),
            [("scenario1", self.__class__.scenario1)],
        )
        self.assertEqual(
            list(self.list_playground_scenarios()),
            [
                ("scenario1", self.__class__.scenario1),
                ("scenario2", self.__class__.scenario2),
            ],
        )

    def test_scenario_instantiated(self) -> None:
        """Test that the scenario has been instantiated."""
        self.assertIsNotNone(self.scenario1.scope)
        self.assertIsNotNone(self.scenario2.scope)
        self.assertIsNone(context.scope)


class SetCurrentScenarioTestCaseTest(TestCase):
    """Test scenario annotations."""

    scenario = scenarios.DefaultContext(set_current=True)

    def test_scenario_instantiated(self) -> None:
        """Test that the scenario has been instantiated."""
        self.assertEqual(context.scope, self.scenario.scope)


class SetCurrentScenarioTransactionTestCaseTest(TransactionTestCase):
    """Test scenario annotations."""

    scenario = scenarios.DefaultContext(set_current=True)

    def test_scenario_instantiated(self) -> None:
        """Test that the scenario has been instantiated."""
        self.assertEqual(context.scope, self.scenario.scope)


class PlaygroundTransactionalTestMixin(TestCase):
    """Test restoring state across test methods."""

    # This is a common set of tests for both the in-memory store case and the
    # local file store case. This class is deleted at the end of the module to
    # avoid it being run as unittests

    # See https://docs.python.org/3/library/unittest.html#organizing-test-code
    # "The order in which the various tests will be run is determined by
    # sorting the test method names with respect to the built-in ordering for
    # strings."

    workspace: ClassVar[Workspace]
    art1: ClassVar[Artifact]
    art2: ClassVar[Artifact]

    @classmethod
    def setUpTestData(cls) -> None:
        """Create a playground to setup test data."""
        super().setUpTestData()
        cls.workspace = cls.playground.get_default_workspace()
        cls.art1, _ = cls.playground.create_artifact(
            {"test.deb": b"testdeb"}, create_files=True
        )
        cls.art2, _ = cls.playground.create_artifact(
            {"test.dsc": b"testdsc", "test.tar.xz": b"testtarxz"},
            create_files=True,
        )

    def assertTestFixtureUnchanged(self) -> None:
        """Check that the text fixture is as originally set up."""
        self.assertEqual(self.playground.default_username, "playground")
        files1 = list(self.art1.files.order_by("id"))
        files2 = list(self.art2.files.order_by("id"))
        self.assertEqual(len(files1), 1)
        self.assertEqual(len(files2), 2)
        self.assertEqual(Artifact.objects.count(), 2)
        backend = self.workspace.scope.download_file_backend(files1[0])
        with backend.get_stream(files1[0]) as fd:
            self.assertEqual(fd.read(), b"testdeb")
        with backend.get_stream(files2[0]) as fd:
            self.assertEqual(fd.read(), b"testdsc")
        with backend.get_stream(files2[1]) as fd:
            self.assertEqual(fd.read(), b"testtarxz")

    def test_alter_file_storage_1(self) -> None:
        """First part of testing transactional file storage behaviour."""
        self.assertTestFixtureUnchanged()
        self.playground.default_username = "changed_username"
        self.assertEqual(self.playground.default_username, "changed_username")

        # Add a new artifact
        self.playground.create_artifact(
            paths={"testfile": b"testdata"}, create_files=True
        )

        # Add a file to an existing artifact
        fileobj = self.playground.create_file_in_backend(
            contents=b"newfilecontents"
        )
        FileInArtifact.objects.create(
            artifact=self.art1, path="newfile", file=fileobj, complete=True
        )

        # Remove a file
        backend = self.playground.get_default_file_store().get_backend_object()
        backend.remove_file(self.art1.files.earliest("id"))

    def test_alter_file_storage_2(self) -> None:
        """Second part of testing transactional file storage behaviour."""
        self.assertTestFixtureUnchanged()


class PlaygroundTransactionalTestMemoryStore(
    PlaygroundTransactionalTestMixin,
):
    """Test transactional behaviour with in-memory file store."""


class PlaygroundTransactionalTestFileStore(PlaygroundTransactionalTestMixin):
    """Test transactional behaviour with on-disk file store."""

    playground_memory_file_store = False


# Avoid running tests from the common base
del ScenarioTestCase
del PlaygroundTransactionalTestMixin
