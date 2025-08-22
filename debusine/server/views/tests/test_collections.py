# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the collections views."""

from typing import Any, ClassVar, assert_never

from django.urls import reverse
from rest_framework import status

from debusine.artifacts.models import (
    BareDataCategory,
    CollectionCategory,
    DebusineTaskConfiguration,
    TaskTypes,
)
from debusine.db.models import Collection, CollectionItem, Workspace
from debusine.db.playground import scenarios
from debusine.server.collections.debusine_task_configuration import (
    DebusineTaskConfigurationManager,
)
from debusine.server.views.collections import (
    ComparableItem,
    TaskConfigurationCollectionView,
)
from debusine.test.django import (
    DenyAll,
    TestCase,
    TestResponseType,
    override_permission,
)


class ComparableItemTests(TestCase):
    """Tests for ComparableItem."""

    def test_compare_invalid(self) -> None:
        item = ComparableItem(
            DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER, task_name="noop"
            )
        )
        other: Any
        for other in (None, 42, 3.14, "foo", []):
            with (
                self.subTest(other=other),
                self.assertRaisesRegex(
                    TypeError,
                    "'<' not supported between instances of"
                    " 'ComparableItem' and ",
                ),
            ):
                item < None

    def test_compare(self) -> None:
        items = {
            "config1": DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER, task_name="noop"
            ),
            "config2": DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER, task_name="noop", subject="subject"
            ),
            "config3": DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER, task_name="noop", context="context"
            ),
            "config4": DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER,
                task_name="noop",
                use_templates=["template"],
            ),
            "config5": DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER,
                task_name="noop",
                delete_values=["test"],
            ),
            "config6": DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER,
                task_name="noop",
                default_values={"test": True},
            ),
            "config7": DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER,
                task_name="noop",
                override_values={"test": True},
            ),
            "config8": DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER,
                task_name="noop",
                lock_values=["test"],
            ),
            "config9": DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER, task_name="noop", comment="test"
            ),
            "template1": DebusineTaskConfiguration(template="template"),
            "template2": DebusineTaskConfiguration(
                template="template", comment="2"
            ),
        }
        for a, b, lt in (
            ("config1", "config2", True),
            ("config2", "config1", False),
            ("config1", "config3", True),
            ("config1", "config4", True),
            ("config1", "config5", True),
            ("config1", "config6", True),
            ("config1", "config7", True),
            ("config1", "config8", True),
            ("config1", "config9", True),
            ("config1", "template1", False),
            ("template1", "config1", True),
            ("template1", "template2", True),
            ("template2", "template1", False),
        ):
            with self.subTest(a=a, b=b):
                self.assertEqual(
                    ComparableItem(items[a]) < ComparableItem(items[b]), lt
                )


class TaskConfigurationCollectionViewTests(TestCase):
    """Tests for TaskConfigurationCollectionView."""

    scenario = scenarios.DefaultContextAPI()
    collection: ClassVar[Collection]
    manager: ClassVar[DebusineTaskConfigurationManager]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.collection = cls.scenario.workspace.get_collection(
            name="default",
            category=CollectionCategory.TASK_CONFIGURATION,
            user=cls.scenario.user,
        )
        cls.manager = DebusineTaskConfigurationManager(
            collection=cls.collection
        )

    @classmethod
    def add_config(cls, entry: DebusineTaskConfiguration) -> CollectionItem:
        """Add a config entry to config_collection."""
        return cls.manager.add_bare_data(
            BareDataCategory.TASK_CONFIGURATION,
            user=cls.scenario.user,
            data=entry,
        )

    def get_items(self) -> list[DebusineTaskConfiguration]:
        collection = self.collection

        res: list[DebusineTaskConfiguration] = []
        for child in collection.child_items.active().filter(
            child_type=CollectionItem.Types.BARE
        ):
            res.append(DebusineTaskConfiguration(**child.data))
        return res

    def get(
        self,
        name: str = "default",
        workspace: Workspace | str | None = None,
        user_token: bool = True,
    ) -> TestResponseType:
        """GET request for a named collection."""
        match workspace:
            case None:
                workspace_name = self.scenario.workspace.name
            case str():
                workspace_name = workspace
            case _:
                workspace_name = workspace.name

        match user_token:
            case True:
                headers = {"token": self.scenario.user_token.key}
            case False:
                headers = {}
                pass
            case _ as unreachable:
                assert_never(unreachable)

        return self.client.get(
            reverse(
                "api:task-configuration-collection",
                kwargs={"workspace": workspace_name, "name": name},
            ),
            content_type="application/json",
            headers=headers,
        )

    def post(
        self,
        name: str = "default",
        workspace: Workspace | str | None = None,
        user_token: bool = True,
        data: dict[str, Any] | None = None,
    ) -> TestResponseType:
        """POST request for a named collection."""
        match workspace:
            case None:
                workspace_name = self.scenario.workspace.name
            case str():
                workspace_name = workspace
            case _:
                workspace_name = workspace.name

        match user_token:
            case True:
                headers = {"token": self.scenario.user_token.key}
            case False:
                headers = {}
                pass
            case _ as unreachable:
                assert_never(unreachable)

        if data is None:
            data = {}

        return self.client.post(
            reverse(
                "api:task-configuration-collection",
                kwargs={"workspace": workspace_name, "name": name},
            ),
            data=data,
            content_type="application/json",
            headers=headers,
        )

    def test_unauthenticated(self) -> None:
        """Authentication is required."""
        for method in "get", "post":
            with self.subTest(method=method):
                response = getattr(self, method)(user_token=False)
                self.assertResponseProblem(
                    response,
                    "Error",
                    detail_pattern=(
                        "Authentication credentials were not provided."
                    ),
                    status_code=status.HTTP_403_FORBIDDEN,
                )

    def test_workspace_not_found(self) -> None:
        """Workspace must exist."""
        for method in "get", "post":
            with self.subTest(method=method):
                response = getattr(self, method)(workspace="does-not-exist")
                self.assertResponseProblem(
                    response,
                    "Workspace not found",
                    detail_pattern=(
                        "Workspace does-not-exist not found in scope debusine"
                    ),
                    status_code=status.HTTP_404_NOT_FOUND,
                )

    def test_workspace_not_accessible(self) -> None:
        """User must be able to display the workspace."""
        workspace = self.playground.create_workspace(name="private")
        for method in "get", "post":
            with self.subTest(method=method):
                response = getattr(self, method)(workspace=workspace)
                self.assertResponseProblem(
                    response,
                    "Workspace not found",
                    detail_pattern=(
                        "Workspace private not found in scope debusine"
                    ),
                    status_code=status.HTTP_404_NOT_FOUND,
                )

    def test_not_found(self) -> None:
        """The collection must exist."""
        for method in "get", "post":
            with self.subTest(method=method):
                response = getattr(self, method)(name="does-not-exist")
                self.assertResponseProblem(
                    response,
                    "Collection not found",
                    detail_pattern=(
                        "Task configuration collection 'does-not-exist'"
                        " does not exist in workspace 'System'"
                    ),
                    status_code=status.HTTP_404_NOT_FOUND,
                )

    def test_get_unauthorized(self) -> None:
        """The user must be able to display the collection."""
        with override_permission(Collection, "can_display", DenyAll):
            response = self.get()
            self.assertResponseProblem(
                response,
                (
                    "playground cannot display collection"
                    " default@debusine:task-configuration"
                ),
                status_code=status.HTTP_403_FORBIDDEN,
            )

    def test_post_unauthorized(self) -> None:
        """The user must be able to display the collection."""
        with override_permission(
            Workspace, "can_edit_task_configuration", DenyAll
        ):
            response = self.post()
            self.assertResponseProblem(
                response,
                (
                    "playground cannot edit task configuration"
                    " in debusine/System"
                ),
                status_code=status.HTTP_403_FORBIDDEN,
            )

    def test_get(self) -> None:
        response = self.get()
        data = self.assertAPIResponseOk(response)

        collection = data["collection"]
        self.assertEqual(collection["id"], self.collection.pk)
        self.assertEqual(collection["name"], "default")
        self.assertEqual(collection["data"], self.collection.data)

        items = data["items"]
        self.assertEqual(items, [])

    def test_get_items(self) -> None:
        item = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.add_config(item)
        response = self.get()
        data = self.assertAPIResponseOk(response)
        items = data["items"]
        self.assertEqual(items, [item.dict()])

    def test_post_matches_collection(self) -> None:
        self.playground.create_group_role(
            self.scenario.workspace, Workspace.Roles.OWNER, [self.scenario.user]
        )
        for data, detail_pattern in (
            (
                {"id": 0, "name": self.collection.name, "data": {}},
                r"Data posted for collection 0"
                rf" to update collection {self.collection.pk}",
            ),
            (
                {
                    "id": self.collection.pk,
                    "name": "does-not-exist",
                    "data": {},
                },
                r"Data posted for collection 'does-not-exist'"
                r" to update collection 'default'",
            ),
        ):
            with self.subTest(data=data):
                response = self.post(data={"collection": data, "items": []})
                self.assertResponseProblem(
                    response,
                    "Posted collection mismatch",
                    detail_pattern=detail_pattern,
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

    def test_post_add(self) -> None:
        self.playground.create_group_role(
            self.scenario.workspace, Workspace.Roles.OWNER, [self.scenario.user]
        )

        item = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        response = self.post(
            data={
                "collection": {
                    "id": self.collection.pk,
                    "name": self.collection.name,
                    "data": self.collection.data,
                },
                "items": [item.dict()],
            }
        )
        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data, {"added": 1, "removed": 0, "updated": 0, "unchanged": 0}
        )

        self.assertEqual(self.get_items(), [item])

    def test_post_remove(self) -> None:
        self.add_config(
            DebusineTaskConfiguration(
                task_type=TaskTypes.WORKER, task_name="noop"
            )
        )
        self.playground.create_group_role(
            self.scenario.workspace, Workspace.Roles.OWNER, [self.scenario.user]
        )

        response = self.post(
            data={
                "collection": {
                    "id": self.collection.pk,
                    "name": self.collection.name,
                    "data": self.collection.data,
                },
                "items": [],
            }
        )
        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data, {"added": 0, "removed": 1, "updated": 0, "unchanged": 0}
        )

        self.assertEqual(self.get_items(), [])

    def test_post_update(self) -> None:
        old = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        new = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            comment="updated",
        )
        self.add_config(old)
        self.playground.create_group_role(
            self.scenario.workspace, Workspace.Roles.OWNER, [self.scenario.user]
        )

        response = self.post(
            data={
                "collection": {
                    "id": self.collection.pk,
                    "name": self.collection.name,
                    "data": self.collection.data,
                },
                "items": [new.dict()],
            }
        )
        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data, {"added": 0, "removed": 0, "updated": 1, "unchanged": 0}
        )
        self.assertEqual(self.get_items(), [new])

    def test_post_dry_run(self) -> None:
        old = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        new = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER,
            task_name="noop",
            comment="updated",
        )
        self.add_config(old)
        self.playground.create_group_role(
            self.scenario.workspace, Workspace.Roles.OWNER, [self.scenario.user]
        )

        response = self.post(
            data={
                "collection": {
                    "id": self.collection.pk,
                    "name": self.collection.name,
                    "data": self.collection.data,
                },
                "items": [new.dict()],
                "dry_run": True,
            }
        )
        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data, {"added": 0, "removed": 0, "updated": 1, "unchanged": 0}
        )
        self.assertEqual(self.get_items(), [old])

    def test_post_git_commit_invalid_type(self) -> None:
        self.playground.create_group_role(
            self.scenario.workspace, Workspace.Roles.OWNER, [self.scenario.user]
        )
        value: Any
        for value in (False, 42, [], {}):
            with self.subTest(value=value):
                response = self.post(
                    data={
                        "collection": {
                            "id": self.collection.pk,
                            "name": self.collection.name,
                            "data": {"git_commit": value},
                        },
                        "items": [],
                        "dry_run": True,
                    }
                )
                self.assertResponseProblem(
                    response,
                    "Invalid git commit",
                    detail_pattern="git_commit is not a string",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

    def test_post_git_commit_invalid_string(self) -> None:
        self.playground.create_group_role(
            self.scenario.workspace, Workspace.Roles.OWNER, [self.scenario.user]
        )
        for value in ("f00", "foo", "", "a" * 100):
            with self.subTest(value=value):
                response = self.post(
                    data={
                        "collection": {
                            "id": self.collection.pk,
                            "name": self.collection.name,
                            "data": {"git_commit": value},
                        },
                        "items": [],
                        "dry_run": True,
                    }
                )
                self.assertResponseProblem(
                    response,
                    "Invalid git commit",
                    detail_pattern="git_commit is malformed",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

    def test_post_valid_git_commit(self) -> None:
        self.playground.create_group_role(
            self.scenario.workspace, Workspace.Roles.OWNER, [self.scenario.user]
        )
        commit = "bb07924dd93295740939e66db5a23777439c7c51"
        self.collection.data["git_commit"] = "0" * 40
        self.collection.save()

        data = self.collection.data.copy()
        data["git_commit"] = commit
        response = self.post(
            data={
                "collection": {
                    "id": self.collection.pk,
                    "name": self.collection.name,
                    "data": data,
                },
                "items": [],
                "dry_run": True,
            }
        )
        data = self.assertAPIResponseOk(response)
        self.assertEqual(
            data, {"added": 0, "removed": 0, "updated": 0, "unchanged": 0}
        )

        self.collection.refresh_from_db()
        self.assertEqual(self.collection.data["git_commit"], commit)

    def test_diff_items(self) -> None:
        item1 = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop", subject="item1"
        )
        item2 = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop", subject="item2"
        )
        item3 = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop", subject="item3"
        )
        item4 = DebusineTaskConfiguration(
            task_type=TaskTypes.WORKER, task_name="noop", subject="item4"
        )

        for old_items, new_items, removed, added, unchanged in (
            ([], [], [], [], 0),
            ([item1], [], [item1], [], 0),
            ([item1], [item2], [item1], [item2], 0),
            (
                [item1, item2, item3, item4],
                [item2, item4],
                [item1, item3],
                [],
                2,
            ),
            ([item1, item3], [item2, item4], [item1, item3], [item2, item4], 0),
            ([item1, item3], [item3, item4], [item1], [item4], 1),
        ):
            with self.subTest(
                old=[x.subject for x in old_items],
                new=[x.subject for x in new_items],
            ):
                # Populate the collection with old_items
                self.collection.child_items.all().delete()
                for item in old_items:
                    self.add_config(item)

                actual_removed, actual_added, actual_unchanged = (
                    TaskConfigurationCollectionView._diff_items(
                        self.collection, new_items
                    )
                )
                self.assertEqual(
                    (
                        sorted(x.split(":")[2] for x in actual_removed),
                        sorted(x.subject or "unknown" for x in actual_added),
                        actual_unchanged,
                    ),
                    (
                        sorted(x.subject or "unknown" for x in removed),
                        sorted(x.subject or "unknown" for x in added),
                        unchanged,
                    ),
                )
