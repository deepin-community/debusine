# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the workspace models."""
import datetime
from typing import ClassVar
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from debusine.artifacts.models import CollectionCategory
from debusine.db.context import ContextConsistencyError, context
from debusine.db.models import (
    Collection,
    FileInArtifact,
    FileInStore,
    FileStore,
    Group,
    Scope,
    WorkRequest,
    Workspace,
    default_workspace,
)
from debusine.db.models.auth import User
from debusine.db.models.permissions import PartialCheckResult
from debusine.db.models.workspaces import (
    DeleteWorkspaces,
    WorkspaceChain,
    WorkspaceRole,
    WorkspaceRoleBase,
    WorkspaceRoles,
    is_valid_workspace_name,
)
from debusine.db.playground import scenarios
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    override_permission,
)


class WorkspaceRolesTests(TestCase):
    """Tests for the WorkspaceRoles class."""

    scenario = scenarios.DefaultContext()

    def test_init(self) -> None:
        r = WorkspaceRoleBase("test")
        r._setup()
        self.assertEqual(r.implied_by_scope_roles, frozenset())
        self.assertEqual(r.implied_by_workspace_roles, frozenset([r]))

    def test_init_invalid_implication(self) -> None:
        r = WorkspaceRoleBase("test", implied_by=[Q()])
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"Workspace roles do not support implications"
            r" by <class 'django.db.models.query_utils.Q'>",
        ):
            r._setup()

    def test_q_no_scope_implications(self) -> None:
        """Test generating a query with no scope implications."""
        user = self.scenario.user
        self.playground.create_group_role(
            self.scenario.scope, Scope.Roles.OWNER, users=[user]
        )

        r = WorkspaceRoleBase("test")
        r._setup()
        self.assertQuerySetEqual(Workspace.objects.filter(r.q(user)), [])

        # Assign r as a role
        with context.disable_permission_checks():
            group = Group.objects.create(scope=self.scenario.scope, name="test")
            group.users.add(user)
            Workspace.objects.get_roles_model().objects.create(
                group=group, resource=self.scenario.workspace, role=r
            )
        self.assertQuerySetEqual(
            Workspace.objects.filter(r.q(user)), [self.scenario.workspace]
        )

    def test_choices(self) -> None:
        self.assertEqual(
            WorkspaceRoles.choices,
            [("owner", "Owner"), ("contributor", "Contributor")],
        )

    def test_implications(self) -> None:
        self.assertEqual(Workspace.Roles.OWNER.label, "Owner")
        self.assertEqual(
            Workspace.Roles.OWNER.implied_by_scope_roles,
            frozenset([Scope.Roles.OWNER]),
        )
        self.assertEqual(
            Workspace.Roles.OWNER.implied_by_workspace_roles,
            frozenset([Workspace.Roles.OWNER]),
        )

        self.assertEqual(Workspace.Roles.CONTRIBUTOR.label, "Contributor")
        self.assertEqual(
            Workspace.Roles.CONTRIBUTOR.implied_by_scope_roles,
            frozenset([Scope.Roles.OWNER]),
        )
        self.assertEqual(
            Workspace.Roles.CONTRIBUTOR.implied_by_workspace_roles,
            frozenset([Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR]),
        )


class WorkspaceManagerTests(TestCase):
    """Tests for the WorkspaceManager class."""

    scenario = scenarios.DefaultContext()
    scope1: ClassVar[Scope]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.scope1 = cls.playground.get_or_create_scope("Scope1")

    def test_get_roles_model(self) -> None:
        """Test the get_roles_model method."""
        self.assertIs(Workspace.objects.get_roles_model(), WorkspaceRole)

    def test_get_for_context_missing_scope(self) -> None:
        """Test get_for_context without scope in context."""
        context.reset()
        with self.assertRaisesRegex(
            ContextConsistencyError, r"scope is not set in context"
        ):
            Workspace.objects.get_for_context(self.scenario.workspace.name)

    def test_get_for_context_missing_user(self) -> None:
        """Test get_for_context without user in context."""
        context.reset()
        context.set_scope(self.scenario.scope)
        with self.assertRaisesRegex(
            ContextConsistencyError, r"user is not set in context"
        ):
            Workspace.objects.get_for_context(self.scenario.workspace.name)

    def test_get_for_context_notfound(self) -> None:
        """Test get_for_context using a wrong name."""
        self.scenario.set_current()
        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get_for_context("does-not-exist")

    def test_get_for_context_wrong_scope(self) -> None:
        """Test get_for_context with a name in the wrong scope."""
        context.reset()
        context.set_scope(self.scope1)
        context.set_user(self.scenario.user)
        with self.assertRaises(Workspace.DoesNotExist):
            Workspace.objects.get_for_context(self.scenario.workspace.name)

    def test_get_for_context_anonymous_user(self) -> None:
        """Test get_for_context with an anonymous user."""
        context.reset()
        context.set_scope(self.scenario.scope)
        context.set_user(AnonymousUser())
        workspace = Workspace.objects.get_for_context(
            self.scenario.workspace.name
        )
        # Scope is prefetched
        with self.assertNumQueries(0):
            workspace.scope
        # Roles are empty
        self.assertFalse(hasattr(workspace, "user_roles"))

    def test_get_for_context_fetch_roles(self) -> None:
        """Ensure get_for_context fetches user roles."""
        self.scenario.set_current()
        # No roles
        workspace = Workspace.objects.get_for_context(
            self.scenario.workspace.name
        )
        self.assertEqual(getattr(workspace, "user_roles"), [])

        # Scope is prefetched
        with self.assertNumQueries(0):
            workspace.scope

        # Roles via one group
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )
        workspace = Workspace.objects.get_for_context(
            self.scenario.workspace.name
        )
        self.assertEqual(
            getattr(workspace, "user_roles"), [Workspace.Roles.OWNER]
        )

        # Roles via multiple groups
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
            name="more owners",
        )
        workspace = Workspace.objects.get_for_context(
            self.scenario.workspace.name
        )
        self.assertEqual(
            getattr(workspace, "user_roles"), [Workspace.Roles.OWNER]
        )

    def test_in_current_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter."""
        with context.local():
            self.scenario.set_current()
            self.assertQuerySetEqual(
                Workspace.objects.in_current_scope(), [self.scenario.workspace]
            )

        workspace1 = self.playground.create_workspace(scope=self.scope1)
        with context.local():
            context.set_scope(self.scope1)
            self.assertQuerySetEqual(
                Workspace.objects.in_current_scope(), [workspace1]
            )

    def test_in_current_scope_no_context_scope(self) -> None:
        """Test the in_current_scope() QuerySet filter without scope set."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "scope is not set"
        ):
            Workspace.objects.in_current_scope()


class WorkspaceTests(TestCase):
    """Tests for the Workspace class."""

    scenario = scenarios.DefaultContext()

    a: ClassVar[Workspace]
    b: ClassVar[Workspace]
    c: ClassVar[Workspace]
    d: ClassVar[Workspace]

    @staticmethod
    def _get_collection(
        workspace: Workspace,
        name: str,
        category: CollectionCategory = CollectionCategory.SUITE,
        user: User | AnonymousUser | None = None,
    ) -> Collection:
        """Shortcut to lookup a collection from a workspace."""
        if user is None:
            user = AnonymousUser()
        return workspace.get_collection(name=name, category=category, user=user)

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.a = cls.playground.create_workspace(name="a", public=True)
        cls.b = cls.playground.create_workspace(name="b", public=True)
        cls.c = cls.playground.create_workspace(name="c", public=True)
        cls.d = cls.playground.create_workspace(name="d", public=True)

    def assertInherits(
        self, child: Workspace, parents: list[Workspace]
    ) -> None:
        """Ensure inheritance chain matches."""
        chain = list(
            child.chain_parents.order_by("order").values_list(
                "child__name", "parent__name", "order"
            )
        )

        expected: list[tuple[str, str, int]] = []
        for idx, parent in enumerate(parents):
            expected.append((child.name, parent.name, idx))
        self.assertEqual(chain, expected)

    def assertGetCollectionEqual(
        self,
        workspace: Workspace,
        name: str,
        expected: Collection,
        *,
        category: CollectionCategory = CollectionCategory.SUITE,
        user: User | None = None,
    ) -> None:
        """Check that collection lookup yields the given result."""
        self.assertEqual(
            self._get_collection(
                workspace, name=name, category=category, user=user
            ),
            expected,
        )

    def assertGetCollectionFails(
        self,
        workspace: Workspace,
        name: str,
        *,
        category: CollectionCategory = CollectionCategory.SUITE,
        user: User | None = None,
    ) -> None:
        """Check that collection lookup fails."""
        with self.assertRaises(Collection.DoesNotExist):
            self._get_collection(
                workspace, name=name, category=category, user=user
            )

    def assertPass(self, value: PartialCheckResult) -> None:
        """Check that the value is PASS."""
        self.assertEqual(value, PartialCheckResult.PASS)

    def test_get_absolute_url(self) -> None:
        """Test the get_absolute_url method."""
        self.assertEqual(
            self.scenario.workspace.get_absolute_url(),
            f"/{settings.DEBUSINE_DEFAULT_SCOPE}/System/",
        )

    def test_get_absolute_url_configure(self) -> None:
        """Test the get_absolute_url_configure method."""
        self.assertEqual(
            self.scenario.workspace.get_absolute_url_configure(),
            f"/{settings.DEBUSINE_DEFAULT_SCOPE}/System/configure/",
        )

    def test_is_valid_workspace_name(self) -> None:
        """Test is_valid_workspace_name."""
        valid_names = (
            "debian",
            "debusine",
            "System",
            "c++",
            "foo_bar",
            "foo.bar",
            "foo+bar",
            "tail_",
            "c--",
        )
        invalid_names = (
            "artifact",
            "workspaces",
            "+tag",
            "-tag",
            ".profile",
            "_reserved",
            "foo:bar",
        )

        for name in valid_names:
            with self.subTest(name=name):
                self.assertTrue(is_valid_workspace_name(name))

        for name in invalid_names:
            with self.subTest(name=name):
                self.assertFalse(is_valid_workspace_name(name))

    def test_workspace_name_validation(self) -> None:
        """Test validation for workspace names."""
        kwargs = {"scope": self.scenario.scope}
        Workspace(name="foo", **kwargs).full_clean()
        Workspace(name="foo_", **kwargs).full_clean()
        with self.assertRaises(ValidationError) as exc:
            Workspace(name="_foo", **kwargs).full_clean()
        self.assertEqual(
            exc.exception.message_dict,
            {'name': ["'_foo' is not a valid workspace name"]},
        )

    @context.disable_permission_checks()
    def test_default_values_fields(self) -> None:
        """Test basic behavior."""
        name = "test"
        workspace = Workspace(name=name, scope=self.scenario.scope)

        self.assertEqual(workspace.name, name)
        self.assertFalse(workspace.public)
        self.assertIsNone(workspace.created_at)
        self.assertIsNone(workspace.expiration_delay)

        pre_save_time = timezone.now()

        workspace.clean_fields()
        workspace.save()
        self.assertGreaterEqual(workspace.created_at, pre_save_time)

    @context.disable_permission_checks()
    def test_scoping(self) -> None:
        """Test scoping."""
        scope1 = self.playground.get_or_create_scope("scope1")
        scope2 = self.playground.get_or_create_scope("scope2")
        Workspace.objects.create(name="test", scope=scope1)
        Workspace.objects.create(name="test", scope=scope2)
        with (
            transaction.atomic(),
            self.assertRaisesRegex(
                IntegrityError,
                # Only match constraint name to support non-english locales
                # "duplicate key value violates unique constraint"
                "db_workspace_unique_scope_name",
            ),
        ):
            Workspace.objects.create(name="test", scope=scope1)

    def test_context_has_role_no_context(self) -> None:
        """Test context_has_role with no context set."""
        self.assertPass(
            self.scenario.workspace.context_has_role(
                self.scenario.user, Workspace.Roles.OWNER
            )
        )

    def test_context_has_role_wrong_user(self) -> None:
        """Test context_has_role with a different user than in context."""
        self.scenario.set_current()
        user = self.playground.create_user("test")
        self.assertPass(
            self.scenario.workspace.context_has_role(
                user, Workspace.Roles.OWNER
            ),
        )

    def test_context_has_role_wrong_scope(self) -> None:
        """Test context_has_role with a different scope than in context."""
        self.scenario.set_current()
        scope = self.playground.get_or_create_scope("test")
        workspace = self.playground.create_workspace(name="test", scope=scope)
        self.assertPass(
            workspace.context_has_role(
                self.scenario.user, Workspace.Roles.OWNER
            )
        )

    def test_context_has_role_wrong_workspace(self) -> None:
        """Test context_has_role with a different workspace than in context."""
        # User is owner of the scope, not of the workspace
        self.playground.create_group_role(
            self.scenario.scope, Scope.Roles.OWNER, users=[self.scenario.user]
        )

        self.scenario.set_current()
        workspace = self.playground.create_workspace(name="test")

        # Not enough information to decide about workspace roles
        self.assertPass(
            workspace.context_has_role(
                self.scenario.user, Workspace.Roles.OWNER
            )
        )

    def test_context_has_role(self) -> None:
        """Test context_has_role."""
        for scope_roles, workspace_roles, roles, expected in (
            (
                [],
                [],
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                PartialCheckResult.DENY,
            ),
            (
                [Scope.Roles.OWNER],
                [],
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                PartialCheckResult.ALLOW,
            ),
            (
                [],
                [Workspace.Roles.OWNER],
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                PartialCheckResult.ALLOW,
            ),
            (
                [],
                [Workspace.Roles.CONTRIBUTOR],
                [Workspace.Roles.CONTRIBUTOR],
                PartialCheckResult.ALLOW,
            ),
            (
                [],
                [Workspace.Roles.CONTRIBUTOR],
                [Workspace.Roles.OWNER],
                PartialCheckResult.DENY,
            ),
        ):
            for role in roles:
                with (
                    self.subTest(
                        scope_roles=scope_roles,
                        workspace_roles=workspace_roles,
                        role=role,
                    ),
                    context.local(),
                ):
                    Group.objects.all().delete()
                    for sr in scope_roles:
                        self.playground.create_group_role(
                            self.scenario.scope, sr, users=[self.scenario.user]
                        )
                    for wr in workspace_roles:
                        self.playground.create_group_role(
                            self.scenario.workspace,
                            wr,
                            users=[self.scenario.user],
                        )
                    self.scenario.set_current()
                    self.assertEqual(
                        self.scenario.workspace.context_has_role(
                            self.scenario.user, role
                        ),
                        expected,
                    )

    @context.disable_permission_checks()
    def test_file_needs_upload_initial_file_store(self) -> None:
        """
        Test file_needs_upload returns the correct values.

        The file is added in the scope's initial FileStore.
        """
        fileobj = self.playground.create_file()
        workspace = default_workspace()
        scope = workspace.scope
        initial_file_store = scope.file_stores.get()
        self.assertTrue(workspace.file_needs_upload(fileobj))

        # Add file in store
        FileInStore.objects.create(file=fileobj, store=initial_file_store)

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [initial_file_store]
        )
        self.assertTrue(workspace.file_needs_upload(fileobj))

        # Add file to an artifact in another workspace
        other_workspace = self.playground.create_workspace(name="other")
        other_artifact, _ = self.playground.create_artifact(
            workspace=other_workspace
        )
        FileInArtifact.objects.create(
            artifact=other_artifact, path="test", file=fileobj
        )

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [initial_file_store]
        )
        self.assertTrue(workspace.file_needs_upload(fileobj))

        # Add different but complete file to an artifact in this workspace
        artifact, _ = self.playground.create_artifact(workspace=workspace)
        FileInArtifact.objects.create(
            artifact=artifact,
            path="other",
            file=self.playground.create_file(b"other"),
            complete=True,
        )

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [initial_file_store]
        )
        self.assertTrue(workspace.file_needs_upload(fileobj))

        # Add incomplete file to an artifact in this workspace
        file_in_artifact = FileInArtifact.objects.create(
            artifact=artifact, path="test", file=fileobj, complete=False
        )

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [initial_file_store]
        )
        self.assertTrue(workspace.file_needs_upload(fileobj))

        # Mark the previous file as complete
        file_in_artifact.complete = True
        file_in_artifact.save()

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [initial_file_store]
        )
        self.assertFalse(workspace.file_needs_upload(fileobj))

    @context.disable_permission_checks()
    def test_file_needs_upload_other_file_store(self) -> None:
        """
        Test file_stores and file_needs_upload return the correct values.

        The file is added in a FileStore other than the scope's initial one.
        """
        fileobj = self.playground.create_file()
        workspace = default_workspace()
        scope = workspace.scope
        self.assertQuerySetEqual(scope.download_file_stores(fileobj), [])
        self.assertTrue(workspace.file_needs_upload(fileobj))

        # Add file in a store which is not the initial one
        store = FileStore.objects.create(
            name="nas-01", backend=FileStore.BackendChoices.LOCAL
        )
        scope.file_stores.add(store)
        workspace.refresh_from_db()

        self.assertQuerySetEqual(scope.download_file_stores(fileobj), [])
        self.assertTrue(workspace.file_needs_upload(fileobj))

        # Add file in the store
        FileInStore.objects.create(file=fileobj, store=store)

        self.assertQuerySetEqual(scope.download_file_stores(fileobj), [store])
        self.assertTrue(workspace.file_needs_upload(fileobj))

        # Add file to an artifact in this workspace
        artifact, _ = self.playground.create_artifact(workspace=workspace)
        FileInArtifact.objects.create(
            artifact=artifact, path="test", file=fileobj, complete=True
        )

        self.assertQuerySetEqual(scope.download_file_stores(fileobj), [store])
        self.assertFalse(workspace.file_needs_upload(fileobj))

        # Add file to the initial store as well
        FileInStore.objects.create(
            file=fileobj, store=scope.file_stores.earliest("id")
        )

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj),
            [scope.file_stores.earliest("id"), store],
        )
        self.assertFalse(workspace.file_needs_upload(fileobj))

    def test_str(self) -> None:
        """Test __str__ method."""
        workspace = Workspace(name="test", scope=self.scenario.scope)

        self.assertEqual(workspace.__str__(), "debusine/test")

    def test_workspacechain_str(self) -> None:
        """Test WorkspaceChain.__str__ method."""
        chain = WorkspaceChain(parent=self.a, child=self.b, order=5)
        self.assertEqual(chain.__str__(), "5:b→a")

    def test_set_inheritance(self) -> None:
        """Test set_inheritance method."""
        a, b, c = self.a, self.b, self.c

        a.set_inheritance([b])
        self.assertInherits(a, [b])
        self.assertInherits(b, [])
        self.assertInherits(c, [])

        a.set_inheritance([b, c])
        self.assertInherits(a, [b, c])
        self.assertInherits(b, [])
        self.assertInherits(c, [])

        a.set_inheritance([c, b])
        self.assertInherits(a, [c, b])
        self.assertInherits(b, [])
        self.assertInherits(c, [])

        c.set_inheritance([a])
        self.assertInherits(a, [c, b])
        self.assertInherits(b, [])
        self.assertInherits(c, [a])

        a.set_inheritance([])
        self.assertInherits(a, [])
        self.assertInherits(b, [])
        self.assertInherits(c, [a])

        with self.assertRaisesRegex(
            ValueError, r"duplicate workspace 'b' in inheritance chain"
        ):
            a.set_inheritance([b, b, c])

    def test_collection_lookup(self) -> None:
        """Test collection lookup."""
        a, b, c = self.a, self.b, self.c

        a1 = self.playground.create_collection(
            name="a", category=CollectionCategory.SUITE, workspace=a
        )

        # Lookup locally
        self.assertEqual(self._get_collection(a, "a"), a1)
        self.assertGetCollectionFails(b, "a")
        self.assertGetCollectionFails(c, "a")

        # Lookup through a simple chain
        b.set_inheritance([a])
        self.assertGetCollectionEqual(b, "a", a1)

        # Walk the inheritance chain until the end
        b.set_inheritance([c, a])
        self.assertGetCollectionEqual(b, "a", a1)
        self.assertGetCollectionFails(c, "a")

        # Create a chain loop, lookup does not break
        c.set_inheritance([b])
        self.assertGetCollectionEqual(b, "a", a1)

    def test_collection_lookup_graph(self) -> None:
        """Test collection lookup graph."""
        a, b, c, d = self.a, self.b, self.c, self.d

        cb = self.playground.create_collection(
            name="a", category=CollectionCategory.SUITE, workspace=b
        )
        cc = self.playground.create_collection(
            name="a", category=CollectionCategory.SUITE, workspace=c
        )

        # Lookup happens in order
        a.set_inheritance([b, c])
        self.assertGetCollectionEqual(a, "a", cb)
        a.set_inheritance([c, b])
        self.assertGetCollectionEqual(a, "a", cc)

        # Lookup happens depth-first
        a.set_inheritance([d, c])
        b.set_inheritance([])
        c.set_inheritance([])
        d.set_inheritance([b])
        self.assertGetCollectionEqual(a, "a", cb)

    def test_user_restrictions(self) -> None:
        """Test user restriction enforcement."""
        user = self.scenario.user
        with context.disable_permission_checks():
            wpub = self.playground.create_workspace(name="public", public=True)
            cpub = self.playground.create_collection(
                "test", CollectionCategory.SUITE, workspace=wpub
            )
            wpriv = self.playground.create_workspace(
                name="private", public=False
            )
            cpriv = self.playground.create_collection(
                "test", CollectionCategory.SUITE, workspace=wpriv
            )
            wstart = self.playground.create_workspace(name="start", public=True)

            self.playground.create_group_role(
                wpriv, Workspace.Roles.OWNER, users=[user]
            )

            other_user = User.objects.create_user(
                username="other", email="other@example.org"
            )

        self.assertGetCollectionFails(wstart, "test", user=None)
        self.assertGetCollectionFails(wstart, "test", user=user)
        self.assertGetCollectionFails(wstart, "test", user=other_user)

        # Lookups in the workspace itself do check restrictions
        self.assertGetCollectionEqual(wpub, "test", cpub, user=None)
        self.assertGetCollectionEqual(wpub, "test", cpub, user=user)
        self.assertGetCollectionEqual(wpub, "test", cpub, user=other_user)
        self.assertGetCollectionFails(wpriv, "test", user=None)
        self.assertGetCollectionEqual(wpriv, "test", cpriv, user=user)
        self.assertGetCollectionFails(wpriv, "test", user=other_user)

        # Inheritance chain is always followed for public datasets
        wstart.set_inheritance([wpub])
        self.assertGetCollectionEqual(wstart, "test", cpub, user=None)
        self.assertGetCollectionEqual(wstart, "test", cpub, user=user)
        self.assertGetCollectionEqual(wstart, "test", cpub, user=other_user)

        # Inheritance chain on private datasets is followed only if logged in
        wstart.set_inheritance([wpriv])
        self.assertGetCollectionFails(wstart, "test", user=None)
        self.assertGetCollectionEqual(wstart, "test", cpriv, user=user)
        self.assertGetCollectionFails(wstart, "test", user=other_user)

        # Inheritance chain skips private datasets but can see public ones
        wstart.set_inheritance([wpriv, wpub])
        self.assertGetCollectionEqual(wstart, "test", cpub, user=None)
        self.assertGetCollectionEqual(wstart, "test", cpriv, user=user)
        self.assertGetCollectionEqual(wstart, "test", cpub, user=other_user)

    def test_get_roles_not_authenticated(self) -> None:
        """Test Workspace.get_roles when not authenticated."""
        user = AnonymousUser()
        self.assertQuerySetEqual(self.a.get_roles(user), [])

    def test_get_roles(self) -> None:
        """Test Workspace.get_roles."""
        group = self.playground.create_group_role(self.a, Workspace.Roles.OWNER)
        user = self.playground.get_default_user()
        self.assertQuerySetEqual(self.a.get_roles(user), [])

        self.playground.add_user(group, user)
        self.assertEqual(list(self.a.get_roles(user)), [Workspace.Roles.OWNER])

    def test_get_roles_multiple_groups(self) -> None:
        """Test Workspace.get_roles with the user in multiple groups."""
        group1 = self.playground.create_group_role(
            self.a, Workspace.Roles.OWNER, name="Group1"
        )
        group2 = self.playground.create_group_role(
            self.a, Workspace.Roles.OWNER, name="Group2"
        )

        user = self.playground.get_default_user()
        self.assertQuerySetEqual(self.a.get_roles(user), [])

        self.playground.add_user(group1, user)
        self.assertEqual(list(self.a.get_roles(user)), [Workspace.Roles.OWNER])

        self.playground.add_user(group2, user)
        self.assertEqual(list(self.a.get_roles(user)), [Workspace.Roles.OWNER])

    def test_set_current_without_scope(self) -> None:
        """Test set_current without scope."""
        with self.assertRaisesRegex(
            ContextConsistencyError, "Cannot set workspace before scope"
        ):
            self.a.set_current()
        self.assertIsNone(context.workspace)
        self.assertIsNone(context._workspace_roles.get())

    def test_set_current_without_user(self) -> None:
        """Test set_current without user."""
        context.set_scope(self.scenario.scope)
        with self.assertRaisesRegex(
            ContextConsistencyError, "Cannot set workspace before user"
        ):
            self.a.set_current()
        self.assertIsNone(context.workspace)
        self.assertIsNone(context._workspace_roles.get())

    def test_set_current_with_token(self) -> None:
        """Test set_current without user."""
        context.set_scope(self.scenario.scope)
        context.set_worker_token(self.playground.create_worker_token())
        self.a.set_current()
        self.assertEqual(context.workspace, self.a)
        self.assertEqual(context.workspace_roles, frozenset())

    def test_context_cannot_change_workspace(self) -> None:
        """Changing workspace in context is not allowed."""
        self.scenario.set_current()

        with self.assertRaisesRegex(
            ContextConsistencyError,
            "Workspace was already set to debusine/System",
        ):
            self.b.set_current()

        self.assertEqual(context.scope, self.scenario.scope)
        self.assertEqual(context.user, self.scenario.user)
        self.assertEqual(context.workspace, self.scenario.workspace)

    def test_set_current_wrong_scope(self) -> None:
        """Test scope/workspace.scope consistency checks."""
        scope1 = Scope.objects.create(name="scope1")
        self.scenario.workspace.scope = scope1
        self.scenario.workspace.save()

        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)
        with self.assertRaisesRegex(
            ContextConsistencyError,
            "workspace scope 'scope1' does not match current scope 'debusine'",
        ):
            self.scenario.workspace.set_current()

        self.assertEqual(context.scope, self.scenario.scope)
        self.assertEqual(context.user, self.scenario.user)
        self.assertIsNone(context.workspace)
        self.assertIsNone(context._workspace_roles.get())

    def test_set_current_forbidden_workspace(self) -> None:
        """Test set_current with an inaccessible workspace."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()

        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)
        with self.assertRaisesRegex(
            ContextConsistencyError,
            "User playground cannot access workspace debusine/System",
        ):
            self.scenario.workspace.set_current()

        self.assertEqual(context.scope, self.scenario.scope)
        self.assertEqual(context.user, self.scenario.user)
        self.assertIsNone(context.workspace)
        self.assertIsNone(context._workspace_roles.get())

    def test_set_current(self) -> None:
        """Test set_current."""
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)
        # One query to fetch the list of workspace roles
        with self.assertNumQueries(1):
            self.a.set_current()
        self.assertEqual(context.workspace_roles, frozenset())

    def test_set_current_cached_roles(self) -> None:
        """Test set_current with user roles cached in Workspace."""
        workspace = self.scenario.workspace
        workspace.public = False
        workspace.save()
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)
        for role in Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR:
            with self.subTest(role=role), context.local():
                setattr(workspace, "user_roles", [role])
                with self.assertNumQueries(0):
                    workspace.set_current()
                self.assertEqual(context.workspace_roles, frozenset((role,)))

    def test_predicate_deny_from_context(self) -> None:
        """Test predicates propagating DENY from context_has_role."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        with mock.patch(
            "debusine.db.models.workspaces.Workspace.context_has_role",
            return_value=PartialCheckResult.DENY,
        ):
            self.assertFalse(
                self.scenario.workspace.can_display(self.scenario.user)
            )
            self.assertFalse(
                self.scenario.workspace.can_configure(self.scenario.user)
            )
            self.assertFalse(
                self.scenario.workspace.can_create_artifacts(self.scenario.user)
            )
            self.assertFalse(
                self.scenario.workspace.can_create_experiment_workspace(
                    self.scenario.user
                )
            )

    def test_can_display_public(self) -> None:
        """Test the can_display predicate on public workspaces."""
        self.assertPermission(
            "can_display",
            users=(AnonymousUser(), self.scenario.user),
            allowed=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )

    def test_can_display_current(self) -> None:
        """Test the can_display predicate on the current workspace."""
        user1 = self.playground.create_user("test1")
        self.a.public = False
        self.a.save()
        for role in Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR:
            with self.subTest(role=role), context.local():
                self.playground.create_group_role(
                    self.a, role, users=[self.scenario.user, user1]
                )

                context.set_scope(self.scenario.scope)
                context.set_user(self.scenario.user)
                self.a.set_current()

                # can_display with current workspace and user is shortcut
                with self.assertNumQueries(0):
                    self.assertTrue(self.a.can_display(self.scenario.user))

                with self.assertNumQueries(1):
                    self.assertTrue(self.a.can_display(user1))

    def test_can_display_by_roles(self) -> None:
        """Test can_display workspace behaviour with roles."""
        # Make workspace private
        self.scenario.workspace.public = False
        self.scenario.workspace.save()

        self.assertPermissionWhenRole(
            self.scenario.workspace.can_display,
            self.scenario.user,
            (Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR),
            scope_roles=Scope.Roles.OWNER,
        )

    def test_can_display_with_token(self) -> None:
        """Test can_display with a worker token."""
        # Make a private
        self.a.public = False
        self.a.save()

        # Workspace is not accessible
        self.assertPermission(
            "can_display",
            users=(AnonymousUser(), self.scenario.user),
            allowed=[self.b, self.c, self.d, self.scenario.workspace],
            denied=self.a,
        )

        # Workspace is now accessible also without user information
        self.assertPermission(
            "can_display",
            users=(None, AnonymousUser(), self.scenario.user),
            allowed=[self.a, self.b, self.c, self.d, self.scenario.workspace],
            token=self.playground.create_worker_token(),
        )

    def test_can_configure_public(self) -> None:
        """Test the can_configure predicate on public workspaces."""
        self.assertPermission(
            "can_configure",
            users=(AnonymousUser(), self.scenario.user),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )

    def test_can_configure_current(self) -> None:
        """Test the can_configure predicate on the current workspace."""
        user1 = self.playground.create_user("test1")
        for role, expected in (
            (Workspace.Roles.OWNER, True),
            (Workspace.Roles.CONTRIBUTOR, False),
        ):
            with self.subTest(role=role), context.local():
                group = self.playground.create_group_role(
                    self.a, role, users=[self.scenario.user, user1]
                )

                context.set_scope(self.scenario.scope)
                context.set_user(self.scenario.user)
                self.a.set_current()

                # can_configure with current workspace and user is shortcut
                with self.assertNumQueries(0):
                    self.assertEqual(
                        self.a.can_configure(self.scenario.user), expected
                    )

                with self.assertNumQueries(1):
                    self.assertEqual(self.a.can_configure(user1), expected)

                group.delete()

    def test_can_configure_by_roles(self) -> None:
        """Test can_configure workspace behaviour with roles."""
        self.assertPermissionWhenRole(
            self.scenario.workspace.can_configure,
            self.scenario.user,
            Workspace.Roles.OWNER,
            scope_roles=Scope.Roles.OWNER,
        )

    def test_can_configure_with_token(self) -> None:
        """Test can_configure with a worker token."""
        # Workspace is not configurable
        self.assertPermission(
            "can_configure",
            users=(AnonymousUser(), self.scenario.user),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )

        # Workspace is also not configurable with a worker token
        self.assertPermission(
            "can_configure",
            users=(None, AnonymousUser(), self.scenario.user),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
            token=self.playground.create_worker_token(),
        )

    def test_create_checks(self) -> None:
        """Test can_create_workspace hook in save."""
        with mock.patch(
            "debusine.db.models.Scope.can_create_workspace"
        ) as pred:
            test1 = Workspace.objects.create(
                name="test1", scope=self.scenario.scope
            )
        pred.assert_called_once_with(None)
        self.assertIsNotNone(test1.pk)

        with override_permission(Scope, "can_create_workspace", AllowAll):
            test2 = Workspace.objects.create(
                name="test2", scope=self.scenario.scope
            )
        self.assertIsNotNone(test2.pk)

        with override_permission(Scope, "can_create_workspace", DenyAll):
            with self.assertRaisesRegex(
                PermissionDenied, r"None cannot create workspaces in debusine"
            ):
                Workspace.objects.create(
                    name="test3", scope=self.scenario.scope
                )
        self.assertQuerySetEqual(Workspace.objects.filter(name="test3"), [])

    def test_can_create_artifacts_public(self) -> None:
        """Test the can_create_artifacts predicate on public workspaces."""
        self.assertPermission(
            "can_create_artifacts",
            users=AnonymousUser(),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )
        self.assertPermission(
            "can_create_artifacts",
            users=self.scenario.user,
            allowed=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )

    def test_can_create_artifacts_current(self) -> None:
        """Test the can_create_artifacts predicate on the current workspace."""
        user1 = self.playground.create_user("test1")
        self.a.public = False
        self.a.save()
        for role in Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR:
            with self.subTest(role=role), context.local():
                self.playground.create_group_role(
                    self.a, role, users=[self.scenario.user, user1]
                )

                context.set_scope(self.scenario.scope)
                context.set_user(self.scenario.user)
                self.a.set_current()

                # can_create_artifacts with current workspace and user is
                # shortcut
                with self.assertNumQueries(0):
                    self.assertTrue(
                        self.a.can_create_artifacts(self.scenario.user)
                    )

                with self.assertNumQueries(1):
                    self.assertTrue(self.a.can_create_artifacts(user1))

    def test_can_create_artifacts_by_roles(self) -> None:
        """Test can_create_artifacts behaviour with roles."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()

        self.assertPermissionWhenRole(
            self.scenario.workspace.can_create_artifacts,
            self.scenario.user,
            [Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR],
            scope_roles=Scope.Roles.OWNER,
        )

    def test_can_create_artifacts_with_token(self) -> None:
        """Test can_create_artifacts with a worker token."""
        # Make a private
        self.a.public = False
        self.a.save()

        # Workspace is not accessible
        self.assertPermission(
            "can_create_artifacts",
            users=AnonymousUser(),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )
        self.assertPermission(
            "can_create_artifacts",
            users=self.scenario.user,
            allowed=[self.b, self.c, self.d, self.scenario.workspace],
            denied=self.a,
        )

        # Workspace is now accessible also without user information
        self.assertPermission(
            "can_create_artifacts",
            users=(None, AnonymousUser(), self.scenario.user),
            allowed=[self.a, self.b, self.c, self.d, self.scenario.workspace],
            token=self.playground.create_worker_token(),
        )

    def test_can_create_work_requests_public(self) -> None:
        """Test can_create_work_requests on public workspaces."""
        self.assertPermission(
            "can_create_work_requests",
            users=(AnonymousUser(), self.scenario.user),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )

    def test_can_create_work_requests_current_owner(self) -> None:
        """Test can_create_work_requests on current owner."""
        user1 = self.playground.create_user("test1")
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.OWNER,
            users=[self.scenario.user, user1],
        )

        self.scenario.set_current()

        # Test shortcut code path
        with self.assertNumQueries(0):
            self.assertTrue(
                self.scenario.workspace.can_create_work_requests(
                    self.scenario.user
                )
            )

        with self.assertNumQueries(1):
            self.assertTrue(
                self.scenario.workspace.can_create_work_requests(user1)
            )

    def test_can_create_work_requests_current_contributor(self) -> None:
        """Test can_create_work_requests on current contributor."""
        user1 = self.playground.create_user("test1")
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[self.scenario.user, user1],
        )

        self.scenario.set_current()

        # Test shortcut code path
        with self.assertNumQueries(0):
            self.assertTrue(
                self.scenario.workspace.can_create_work_requests(
                    self.scenario.user
                )
            )

        with self.assertNumQueries(1):
            self.assertTrue(
                self.scenario.workspace.can_create_work_requests(user1)
            )

    def test_can_create_work_requests_by_roles(self) -> None:
        """Test can_create_work_requests behaviour with roles."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()

        self.assertPermissionWhenRole(
            self.scenario.workspace.can_create_work_requests,
            self.scenario.user,
            (Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR),
            scope_roles=Scope.Roles.OWNER,
        )

    def test_can_create_work_requests_with_token(self) -> None:
        """Test can_create_work_requests with a worker token."""
        self.playground.create_group_role(
            self.scenario.scope, Scope.Roles.OWNER, users=[self.scenario.user]
        )

        # Anonymous cannot
        self.assertPermission(
            "can_create_work_requests",
            users=AnonymousUser(),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )

        # User can
        self.assertPermission(
            "can_create_work_requests",
            users=self.scenario.user,
            allowed=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )

        # A worker token cannot in any case
        self.assertPermission(
            "can_create_work_requests",
            users=(None, AnonymousUser(), self.scenario.user),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
            token=self.playground.create_worker_token(),
        )

    def test_can_create_experiment_workspace_public(self) -> None:
        """Test can_create_experiment_workspace on public workspaces."""
        self.assertPermission(
            "can_create_experiment_workspace",
            users=AnonymousUser(),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )
        self.assertPermission(
            "can_create_experiment_workspace",
            users=self.scenario.user,
            allowed=[self.a, self.b, self.c, self.d, self.scenario.workspace],
        )

    def test_can_create_experiment_workspace_current(self) -> None:
        """Test can_create_experiment_workspace on the current workspace."""
        user1 = self.playground.create_user("test1")
        self.a.public = False
        self.a.save()
        for role in Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR:
            with self.subTest(role=role), context.local():
                self.playground.create_group_role(
                    self.a, role, users=[self.scenario.user, user1]
                )

                context.set_scope(self.scenario.scope)
                context.set_user(self.scenario.user)
                self.a.set_current()

                # can_create_experiment_workspace with current workspace and
                # user is shortcut
                with self.assertNumQueries(0):
                    self.assertTrue(
                        self.a.can_create_experiment_workspace(
                            self.scenario.user
                        )
                    )

                with self.assertNumQueries(1):
                    self.assertTrue(
                        self.a.can_create_experiment_workspace(user1)
                    )

    def test_can_create_experiment_workspace_by_roles(self) -> None:
        """Test can_create_experiment_workspace workspace with roles."""
        # Make workspace private
        self.scenario.workspace.public = False
        self.scenario.workspace.save()

        self.assertPermissionWhenRole(
            self.scenario.workspace.can_create_experiment_workspace,
            self.scenario.user,
            (Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR),
            scope_roles=Scope.Roles.OWNER,
        )

    def test_can_create_experiment_workspace_with_token(self) -> None:
        """Test can_create_experiment_workspace with a worker token."""
        # Make a private
        self.a.public = False
        self.a.save()

        # Using a worker token, the permission is always denied
        self.assertPermission(
            "can_create_experiment_workspace",
            users=(None, AnonymousUser(), self.scenario.user),
            denied=[self.a, self.b, self.c, self.d, self.scenario.workspace],
            token=self.playground.create_worker_token(),
        )

    def test_with_expiration_time_no_expiry(self) -> None:
        """Test with_expiration_time with no expiry."""
        ws = Workspace.objects.with_expiration_time().get(name="System")
        self.assertIsNone(ws.expiration_time)

    def test_with_expiration_time_created_at(self) -> None:
        """Test with_expiration_time with only created_at."""
        self.a.expiration_delay = datetime.timedelta(days=3)
        self.a.save()
        ws = Workspace.objects.with_expiration_time().get(pk=self.a.pk)
        self.assertEqual(
            ws.expiration_time, self.a.created_at + self.a.expiration_delay
        )

    def test_with_expiration_time_work_request_completed(self) -> None:
        """Test with_expiration_time with only created_at."""
        self.a.expiration_delay = datetime.timedelta(days=3)
        self.a.save()
        wr = self.playground.create_work_request(
            result=WorkRequest.Results.SUCCESS, workspace=self.a
        )
        wr.completed_at = self.a.created_at + datetime.timedelta(days=42)
        wr.save()
        ws = Workspace.objects.with_expiration_time().get(pk=self.a.pk)
        self.assertEqual(
            ws.expiration_time,
            self.a.created_at
            + self.a.expiration_delay
            + datetime.timedelta(days=42),
        )

    def test_expire_at_no_expiry(self) -> None:
        """Test expire_at with no expiry."""
        self.assertIsNone(self.scenario.workspace.expire_at)

    def test_expire_at(self) -> None:
        """Test with_expiration_time, computed by expire_at."""
        self.a.expiration_delay = datetime.timedelta(days=3)
        self.a.save()
        self.assertFalse(hasattr(self.a, "expiration_time"))
        expiration_time = self.a.created_at + self.a.expiration_delay
        self.assertEqual(self.a.expire_at, expiration_time)
        self.assertEqual(
            self.a.expiration_time,  # type: ignore[attr-defined]
            expiration_time,
        )

    def test_expire_at_precomputed(self) -> None:
        """Test with_expiration_time precomputed by with_expiration_time."""
        self.a.expiration_delay = datetime.timedelta(days=3)
        self.a.save()
        expiration_time = self.a.created_at + self.a.expiration_delay
        a = Workspace.objects.with_expiration_time().get(pk=self.a.pk)
        self.assertEqual(
            a.expiration_time,
            expiration_time,
        )
        self.assertEqual(a.expire_at, expiration_time)


class WorkspaceRoleTests(TestCase):
    """Tests for the WorkspaceRole class."""

    workspace1: ClassVar[Workspace]
    workspace2: ClassVar[Workspace]
    group1: ClassVar[Group]
    group2: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        with context.disable_permission_checks():
            cls.workspace1 = cls.playground.create_workspace(name="Workspace1")
            cls.workspace2 = cls.playground.create_workspace(name="Workspace2")
            cls.group1 = cls.playground.create_group("Group1")
            cls.group2 = cls.playground.create_group("Group2")

    def test_str(self) -> None:
        """Test stringification."""
        sr = WorkspaceRole(
            group=self.group1,
            resource=self.workspace1,
            role=WorkspaceRole.Roles.OWNER,
        )
        self.assertEqual(str(sr), "debusine/Group1─owner⟶debusine/Workspace1")

    def test_assign_multiple_groups(self) -> None:
        """Multiple group can share a role on a workspace."""
        WorkspaceRole.objects.create(
            group=self.group1,
            resource=self.workspace1,
            role=WorkspaceRole.Roles.OWNER,
        )
        WorkspaceRole.objects.create(
            group=self.group2,
            resource=self.workspace1,
            role=WorkspaceRole.Roles.OWNER,
        )

    def test_assign_multiple_scopes(self) -> None:
        """A group can be given a role in different workspaces."""
        WorkspaceRole.objects.create(
            group=self.group1,
            resource=self.workspace1,
            role=WorkspaceRole.Roles.OWNER,
        )
        WorkspaceRole.objects.create(
            group=self.group1,
            resource=self.workspace2,
            role=WorkspaceRole.Roles.OWNER,
        )

    def test_on_delete_cascade(self) -> None:
        """Test on delete cascade behaviour."""
        assignment = WorkspaceRole.objects.create(
            group=self.group1,
            resource=self.workspace1,
            role=WorkspaceRole.Roles.OWNER,
        )
        self.group1.delete()
        self.assertFalse(
            WorkspaceRole.objects.filter(pk=assignment.pk).exists()
        )

        assignment = WorkspaceRole.objects.create(
            group=self.group2,
            resource=self.workspace2,
            role=WorkspaceRole.Roles.OWNER,
        )
        self.workspace2.delete()
        self.assertFalse(
            WorkspaceRole.objects.filter(pk=assignment.pk).exists()
        )


class WorkspaceDeleteTests(TestCase):
    """Test DeleteWorkspaces."""

    scenario = scenarios.UIPlayground()

    def test_scan(self) -> None:
        """Test DeleteWorkspaces scan of resources."""
        operation = DeleteWorkspaces(
            Workspace.objects.filter(pk=self.scenario.workspace.pk)
        )

        self.assertQuerySetEqual(
            operation.workflow_templates, [self.scenario.template_sbuild]
        )
        self.assertEqual(operation.artifacts.count(), 17)
        self.assertEqual(operation.work_requests.count(), 12)
        self.assertEqual(operation.collection_items.count(), 7)
        self.assertEqual(operation.collections.count(), 6)
        self.assertEqual(operation.file_in_artifacts.count(), 18)
        self.assertEqual(operation.artifact_relations.count(), 14)
        self.assertEqual(operation.asset_usages.count(), 1)
        self.assertEqual(operation.assets.count(), 1)
        self.assertEqual(operation.workspace_roles.count(), 1)

    def test_delete(self) -> None:
        """Test DeleteWorkspaces deletion."""
        operation = DeleteWorkspaces(
            Workspace.objects.filter(pk=self.scenario.workspace.pk)
        )
        operation.perform_deletions()
        self.assertFalse(
            Workspace.objects.filter(pk=self.scenario.workspace.pk).exists()
        )
