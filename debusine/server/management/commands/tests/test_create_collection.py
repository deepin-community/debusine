# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command create_collection."""

import io

import yaml
from django.conf import settings
from django.core.management import CommandError
from django.test import override_settings

from debusine.artifacts.models import CollectionCategory
from debusine.db.models import Collection, default_workspace
from debusine.django.management.tests import call_command
from debusine.test.django import TestCase


@override_settings(LANGUAGE_CODE="en-us")
class CreateCollectionCommandTests(TestCase):
    """Tests for the create_collection command."""

    def test_create_collection_from_file(self) -> None:
        """`create_collection` creates a new collection (data in file)."""
        name = "test"
        category = CollectionCategory.SUITE
        data = {"may_reuse_versions": True}
        data_file = self.create_temporary_file(
            contents=yaml.safe_dump(data).encode()
        )
        stdout, stderr, exit_code = call_command(
            "create_collection", name, category, "--data", str(data_file)
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        collection = Collection.objects.get(name=name, category=category)
        self.assertEqual(
            collection.workspace.name, settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        self.assertEqual(collection.data, data)

    def test_create_collection_from_stdin(self) -> None:
        """`create_collection` creates a new collection (data in file)."""
        name = "test"
        category = CollectionCategory.SUITE
        data = {"may_reuse_versions": True}
        stdout, stderr, exit_code = call_command(
            "create_collection",
            name,
            category,
            stdin=io.StringIO(yaml.safe_dump(data)),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        collection = Collection.objects.get(name=name, category=category)
        self.assertEqual(
            collection.workspace.name, settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        self.assertEqual(collection.data, data)

    def test_create_collection_empty_data(self) -> None:
        """`create_collection` defaults data to {}."""
        name = "test"
        category = CollectionCategory.ENVIRONMENTS
        stdout, stderr, exit_code = call_command(
            "create_collection", name, category, stdin=io.StringIO()
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        collection = Collection.objects.get(name=name, category=category)
        self.assertEqual(collection.data, {})

    def test_create_collection_different_workspace(self) -> None:
        """`create_collection` can use a non-default workspace."""
        name = "test"
        category = CollectionCategory.ENVIRONMENTS
        workspace_name = "test-workspace"
        workspace = self.playground.create_workspace(name=workspace_name)
        stdout, stderr, exit_code = call_command(
            "create_collection",
            name,
            category,
            "--workspace",
            workspace_name,
            stdin=io.StringIO(),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        collection = Collection.objects.get(name=name, category=category)
        self.assertEqual(collection.workspace, workspace)

    def test_create_collection_different_scope(self) -> None:
        """`create_collection` can use a workspace in a different scope."""
        name = "test"
        category = CollectionCategory.ENVIRONMENTS
        scope_name = "test-scope"
        scope = self.playground.get_or_create_scope(name=scope_name)
        workspace = self.playground.create_workspace(
            scope=scope, name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        stdout, stderr, exit_code = call_command(
            "create_collection",
            name,
            category,
            "--workspace",
            f"{scope_name}/{settings.DEBUSINE_DEFAULT_WORKSPACE}",
            stdin=io.StringIO(),
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

        collection = Collection.objects.get(name=name, category=category)
        self.assertEqual(collection.workspace, workspace)

    def test_create_collection_invalid_data_yaml(self) -> None:
        """`create_collection` returns error: cannot parse YAML data."""
        with self.assertRaisesRegex(
            CommandError, r"^Error parsing YAML:"
        ) as exc:
            call_command(
                "create_collection",
                "test",
                CollectionCategory.ENVIRONMENTS,
                stdin=io.StringIO(":"),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_collection_nonexistent_workspace(self) -> None:
        """`create_collection` returns error: workspace not found."""
        with self.assertRaisesRegex(
            CommandError,
            fr"^Workspace 'nonexistent' not found in scope "
            fr"'{settings.DEBUSINE_DEFAULT_SCOPE}'$",
        ) as exc:
            call_command(
                "create_collection",
                "test",
                CollectionCategory.ENVIRONMENTS,
                "--workspace",
                "nonexistent",
                stdin=io.StringIO(),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_collection_duplicated_name_and_category(self) -> None:
        """`create_collection` returns error: duplicated name and category."""
        Collection.objects.create(
            name="test",
            category=CollectionCategory.SUITE,
            workspace=default_workspace(),
            data={},
        )
        with self.assertRaisesRegex(
            CommandError,
            r"^Error creating collection: "
            r"Collection with this Name, Category and Workspace already "
            r"exists\.$",
        ) as exc:
            call_command(
                "create_collection",
                "test",
                CollectionCategory.SUITE,
                stdin=io.StringIO(yaml.safe_dump({"may_reuse_versions": True})),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_create_collection_invalid_name(self) -> None:
        """`create_collection` returns error: name is not valid."""
        with self.assertRaisesRegex(
            CommandError,
            r"^Error creating collection: "
            r"'_test' is not a valid collection name",
        ):
            call_command(
                "create_collection",
                "_test",
                CollectionCategory.TEST,
                stdin=io.StringIO(),
            )

        with self.assertRaisesRegex(
            CommandError,
            r"^Error creating collection: "
            r"'a/b' is not a valid collection name",
        ):
            call_command(
                "create_collection",
                "a/b",
                CollectionCategory.TEST,
                stdin=io.StringIO(),
            )
