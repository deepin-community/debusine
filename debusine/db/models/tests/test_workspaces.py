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

from debusine.artifacts.models import (
    ArtifactCategory,
    CollectionCategory,
    RuntimeStatistics,
)
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
from debusine.db.models.permissions import Allow, PermissionUser
from debusine.db.models.tests.utils import RolesTestCase
from debusine.db.models.workspaces import (
    DeleteWorkspaces,
    WorkspaceChain,
    WorkspaceRole,
    WorkspaceRoleBase,
    WorkspaceRoles,
    is_valid_workspace_name,
)
from debusine.db.playground import scenarios
from debusine.tasks.models import (
    ActionUpdateCollectionWithArtifacts,
    OutputData,
)
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    override_permission,
)


class WorkspaceRolesTests(RolesTestCase[WorkspaceRoles]):
    """Tests for the WorkspaceRoles class."""

    scenario = scenarios.DefaultContext()
    roles_class = WorkspaceRoles

    def test_invariants(self) -> None:
        self.assertRolesInvariants()

    def test_from_iterable(self) -> None:
        for value, expected in (
            ([], []),
            (["owner"], [Workspace.Roles.OWNER]),
            (["contributor"], [Workspace.Roles.CONTRIBUTOR]),
            (
                ["contributor", Workspace.Roles.CONTRIBUTOR],
                [Workspace.Roles.CONTRIBUTOR],
            ),
            (["contributor", Workspace.Roles.OWNER], [Workspace.Roles.OWNER]),
            (
                [
                    "owner",
                    "owner",
                    Workspace.Roles.OWNER,
                    Workspace.Roles.CONTRIBUTOR,
                ],
                [Workspace.Roles.OWNER],
            ),
            (
                [
                    "contributor",
                    Workspace.Roles.VIEWER,
                ],
                [Workspace.Roles.CONTRIBUTOR],
            ),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    Workspace.Roles.from_iterable(value),
                    frozenset(expected),
                )

    def test_choices(self) -> None:
        self.assertChoices()

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

    def test_q_non_authenticated(self) -> None:
        """Test generating Q objects with anonymous users."""
        for role, expected in (
            (WorkspaceRoles.OWNER, False),
            (WorkspaceRoles.CONTRIBUTOR, False),
            (WorkspaceRoles.VIEWER, True),
        ):
            with self.subTest(role=role):
                expected_qs = [self.scenario.workspace] if expected else []
                self.assertQuerySetEqual(
                    Workspace.objects.filter(
                        pk=self.scenario.workspace.pk
                    ).filter(role.q(AnonymousUser())),
                    expected_qs,
                )

    def test_values(self) -> None:
        self.assertEqual(Workspace.Roles.OWNER.label, "Owner")
        self.assertEqual(
            Workspace.Roles.OWNER.implied_by_scope_roles,
            frozenset([Scope.Roles.OWNER]),
        )
        self.assertEqual(
            Workspace.Roles.OWNER.implied_by_workspace_roles,
            frozenset([Workspace.Roles.OWNER]),
        )
        self.assertFalse(Workspace.Roles.OWNER.implied_by_public)

        self.assertEqual(Workspace.Roles.CONTRIBUTOR.label, "Contributor")
        self.assertEqual(
            Workspace.Roles.CONTRIBUTOR.implied_by_scope_roles,
            frozenset([Scope.Roles.OWNER]),
        )
        self.assertEqual(
            Workspace.Roles.CONTRIBUTOR.implied_by_workspace_roles,
            frozenset([Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR]),
        )
        self.assertFalse(Workspace.Roles.CONTRIBUTOR.implied_by_public)

        self.assertEqual(Workspace.Roles.VIEWER.label, "Viewer")
        self.assertEqual(
            Workspace.Roles.VIEWER.implied_by_scope_roles,
            frozenset([Scope.Roles.OWNER]),
        )
        self.assertEqual(
            Workspace.Roles.VIEWER.implied_by_workspace_roles,
            frozenset(
                [
                    Workspace.Roles.OWNER,
                    Workspace.Roles.CONTRIBUTOR,
                    Workspace.Roles.VIEWER,
                ]
            ),
        )
        self.assertTrue(Workspace.Roles.VIEWER.implied_by_public)


class WorkspaceManagerTests(TestCase):
    """Tests for the WorkspaceManager class."""

    scenario = scenarios.DefaultContext()
    scope1: ClassVar[Scope]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.scope1 = cls.playground.get_or_create_scope("Scope1")

    def assertRoleAnnotations(
        self, user: PermissionUser, expected: dict[int, dict[str, bool]]
    ) -> None:
        """
        Ensure the roles computed by the database match the expected set.

        :param expected: a dict in the form ``{pk: {role: bool}}``
        """
        by_pk: dict[int, dict[str, bool]] = {}
        for workspace in Workspace.objects.with_role_annotations(user):
            has_roles: dict[str, bool] = {}
            for role in Workspace.Roles:
                has_roles[role] = getattr(workspace, f"is_{role}")
            by_pk[workspace.pk] = has_roles

            roles_for: str = getattr(workspace, "roles_for")
            if user is None or not user.is_authenticated:
                self.assertEqual(roles_for, "")
            else:
                self.assertEqual(roles_for, str(user.pk))

        self.assertEqual(by_pk, expected)

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

    def test_with_role_public(self) -> None:
        """Test with_role on a public workspace."""
        for user, roles, role, expected in (
            (AnonymousUser(), [], Workspace.Roles.CONTRIBUTOR, False),
            (AnonymousUser(), [], Workspace.Roles.VIEWER, True),
            (self.scenario.user, [], Workspace.Roles.VIEWER, True),
            (
                self.scenario.user,
                [Workspace.Roles.VIEWER],
                Workspace.Roles.VIEWER,
                True,
            ),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR],
                Workspace.Roles.CONTRIBUTOR,
                True,
            ),
            (self.scenario.user, [], Workspace.Roles.CONTRIBUTOR, False),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR],
                Workspace.Roles.CONTRIBUTOR,
                True,
            ),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR],
                Workspace.Roles.OWNER,
                False,
            ),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                Workspace.Roles.CONTRIBUTOR,
                True,
            ),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                Workspace.Roles.OWNER,
                True,
            ),
        ):
            with (
                self.subTest(user=user, roles=roles, role=role),
                self.scenario.assign_role(self.scenario.workspace, *roles),
            ):
                results = list(Workspace.objects.with_role(user, role))
                if expected:
                    self.assertIn(self.scenario.workspace, results)
                else:
                    self.assertNotIn(self.scenario.workspace, results)

    def test_with_role_private(self) -> None:
        """Test with_role on a private workspace."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        for user, roles, role, expected in (
            (AnonymousUser(), [], Workspace.Roles.CONTRIBUTOR, False),
            (AnonymousUser(), [], Workspace.Roles.VIEWER, False),
            (self.scenario.user, [], Workspace.Roles.VIEWER, False),
            (
                self.scenario.user,
                [Workspace.Roles.VIEWER],
                Workspace.Roles.VIEWER,
                True,
            ),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR],
                Workspace.Roles.CONTRIBUTOR,
                True,
            ),
            (self.scenario.user, [], Workspace.Roles.CONTRIBUTOR, False),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR],
                Workspace.Roles.CONTRIBUTOR,
                True,
            ),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR],
                Workspace.Roles.OWNER,
                False,
            ),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                Workspace.Roles.CONTRIBUTOR,
                True,
            ),
            (
                self.scenario.user,
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                Workspace.Roles.OWNER,
                True,
            ),
        ):
            with (
                self.subTest(user=user, roles=roles, role=role),
                self.scenario.assign_role(self.scenario.workspace, *roles),
            ):
                results = list(Workspace.objects.with_role(user, role))
                if expected:
                    self.assertIn(self.scenario.workspace, results)
                else:
                    self.assertNotIn(self.scenario.workspace, results)

    def test_with_role_annotations_anonymous(self) -> None:
        public = self.scenario.workspace
        private = self.playground.create_workspace(name="private")
        self.assertRoleAnnotations(
            AnonymousUser(),
            {
                public.pk: {
                    Workspace.Roles.OWNER: False,
                    Workspace.Roles.CONTRIBUTOR: False,
                    Workspace.Roles.VIEWER: True,
                },
                private.pk: {
                    Workspace.Roles.OWNER: False,
                    Workspace.Roles.CONTRIBUTOR: False,
                    Workspace.Roles.VIEWER: False,
                },
            },
        )

    def test_with_role_annotations(self) -> None:
        public = self.scenario.workspace
        private = self.playground.create_workspace(name="private")
        contributed = self.playground.create_workspace(name="contributed")
        self.playground.create_group_role(
            contributed, Workspace.Roles.CONTRIBUTOR, users=[self.scenario.user]
        )

        owned = self.playground.create_workspace(name="owned")
        self.playground.create_group_role(
            owned, Workspace.Roles.OWNER, users=[self.scenario.user]
        )

        self.assertRoleAnnotations(
            self.scenario.user,
            {
                public.pk: {
                    Workspace.Roles.OWNER: False,
                    Workspace.Roles.CONTRIBUTOR: False,
                    Workspace.Roles.VIEWER: True,
                },
                private.pk: {
                    Workspace.Roles.OWNER: False,
                    Workspace.Roles.CONTRIBUTOR: False,
                    Workspace.Roles.VIEWER: False,
                },
                contributed.pk: {
                    Workspace.Roles.OWNER: False,
                    Workspace.Roles.CONTRIBUTOR: True,
                    Workspace.Roles.VIEWER: True,
                },
                owned.pk: {
                    Workspace.Roles.OWNER: True,
                    Workspace.Roles.CONTRIBUTOR: True,
                    Workspace.Roles.VIEWER: True,
                },
            },
        )

    def test_annotate_with_workflow_stats(self) -> None:
        template = self.playground.create_workflow_template(
            name="test", task_name="noop"
        )
        # 3 running
        for i in range(3):
            workflow = self.playground.create_workflow(template, task_data={})
            workflow.mark_running()

        # 2 needs_input
        for i in range(2):
            workflow = self.playground.create_workflow(template, task_data={})
            workflow.workflow_runtime_status = (
                WorkRequest.RuntimeStatuses.NEEDS_INPUT
            )
            workflow.save()

        # 1 completed
        workflow = self.playground.create_workflow(template, task_data={})
        workflow.mark_completed(WorkRequest.Results.SUCCESS)

        ws = Workspace.objects.annotate_with_workflow_stats().get()
        self.assertEqual(ws.running, 3)
        self.assertEqual(ws.needs_input, 2)
        self.assertEqual(ws.completed, 1)


class WorkspaceTests(TestCase):
    """Tests for the Workspace class."""

    scenario = scenarios.DefaultContext()

    a: ClassVar[Workspace]
    b: ClassVar[Workspace]
    c: ClassVar[Workspace]

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
    def setUpTestData(cls) -> None:
        """Set up common data for tests."""
        super().setUpTestData()
        cls.a = cls.playground.create_workspace(name="a", public=True)
        cls.b = cls.playground.create_workspace(name="b", public=True)
        cls.c = cls.playground.create_workspace(name="c", public=True)

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

    def assertHasRole(
        self,
        role: Workspace.Roles,
        *,
        user: User | None = None,
        workspace: Workspace | None = None,
        hits_database: bool = False,
    ) -> None:
        """Ensure the user has the given role on the workspace."""
        workspace = workspace or self.scenario.workspace
        user = user or self.scenario.user
        expected_queries = 1 if hits_database else 0
        with self.assertNumQueries(expected_queries):
            self.assertTrue(workspace.has_role(user, role))

    def assertHasNoRole(
        self,
        role: Workspace.Roles,
        *,
        user: User | None = None,
        workspace: Workspace | None = None,
        hits_database: bool = False,
    ) -> None:
        """Ensure the user does not have the given role on the workspace."""
        workspace = workspace or self.scenario.workspace
        user = user or self.scenario.user
        expected_queries = 1 if hits_database else 0
        with self.assertNumQueries(expected_queries):
            self.assertFalse(workspace.has_role(user, role))

    def test_get_absolute_url(self) -> None:
        """Test the get_absolute_url method."""
        self.assertEqual(
            self.scenario.workspace.get_absolute_url(),
            f"/{settings.DEBUSINE_DEFAULT_SCOPE}/System/",
        )

    def test_get_absolute_url_different_scope(self) -> None:
        """get_absolute_url works in another scope."""
        other_scope = self.playground.get_or_create_scope(name="other")
        workspace = self.playground.create_workspace(scope=other_scope)
        self.assertEqual(
            workspace.get_absolute_url(),
            f"/{other_scope}/{workspace.name}/",
        )

    def test_get_absolute_url_configure(self) -> None:
        """Test the get_absolute_url_configure method."""
        self.assertEqual(
            self.scenario.workspace.get_absolute_url_configure(),
            f"/{settings.DEBUSINE_DEFAULT_SCOPE}/System/configure/",
        )

    def test_get_absolute_url_configure_different_scope(self) -> None:
        """get_absolute_url_configure works in another scope."""
        other_scope = self.playground.get_or_create_scope(name="other")
        workspace = self.playground.create_workspace(scope=other_scope)
        self.assertEqual(
            workspace.get_absolute_url_configure(),
            f"/{other_scope}/{workspace.name}/configure/",
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

    def test_has_role_no_context(self) -> None:
        """Test has_role with no context set."""
        self.assertHasNoRole(Workspace.Roles.OWNER, hits_database=True)
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )
        self.assertHasRole(Workspace.Roles.OWNER, hits_database=True)

    def test_has_role_user_not_in_context(self) -> None:
        """Test has_role with a different user than in context."""
        self.scenario.set_current()
        user = self.playground.create_user("test")
        self.assertHasNoRole(
            Workspace.Roles.CONTRIBUTOR, user=user, hits_database=True
        )
        self.playground.create_group_role(
            self.scenario.workspace,
            Workspace.Roles.CONTRIBUTOR,
            users=[user],
        )
        self.assertHasRole(
            Workspace.Roles.CONTRIBUTOR, user=user, hits_database=True
        )

    def test_has_role_workspace_not_in_context(self) -> None:
        """Test has_role with a different workspace than in context."""
        self.scenario.set_current()
        workspace = self.playground.create_workspace(name="test")
        self.assertHasNoRole(
            Workspace.Roles.CONTRIBUTOR, workspace=workspace, hits_database=True
        )
        self.playground.create_group_role(
            workspace,
            Workspace.Roles.OWNER,
            users=[self.scenario.user],
        )
        self.assertHasRole(
            Workspace.Roles.CONTRIBUTOR, workspace=workspace, hits_database=True
        )

    def test_has_role(self) -> None:
        """Test has_role."""
        for scope_roles, workspace_roles, roles, expected in (
            (
                [],
                [],
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                False,
            ),
            (
                [Scope.Roles.OWNER],
                [],
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                True,
            ),
            (
                [],
                [Workspace.Roles.OWNER],
                [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
                True,
            ),
            (
                [],
                [Workspace.Roles.CONTRIBUTOR],
                [Workspace.Roles.CONTRIBUTOR],
                True,
            ),
            ([], [Workspace.Roles.CONTRIBUTOR], [Workspace.Roles.OWNER], False),
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
                    with self.assertNumQueries(0):
                        self.assertEqual(
                            self.scenario.workspace.has_role(
                                self.scenario.user, role
                            ),
                            expected,
                        )

    def test_get_roles(self) -> None:
        public = self.scenario.workspace
        private = self.playground.create_workspace(name="private")
        contributed = self.playground.create_workspace(name="contributed")
        self.playground.create_group_role(
            contributed, Workspace.Roles.CONTRIBUTOR, users=[self.scenario.user]
        )

        owned = self.playground.create_workspace(name="owned")
        self.playground.create_group_role(
            owned, Workspace.Roles.OWNER, users=[self.scenario.user]
        )

        expected: dict[int, list[WorkspaceRoles]] = {
            public.pk: [Workspace.Roles.VIEWER],
            private.pk: [],
            contributed.pk: [Workspace.Roles.CONTRIBUTOR],
            owned.pk: [Workspace.Roles.OWNER],
        }

        base_queryset = Workspace.objects.filter(
            pk__in=(public.pk, private.pk, contributed.pk, owned.pk)
        )
        for name, queryset, num_queries in (
            (
                "plain",
                base_queryset,
                {public.pk: 2, private.pk: 3, contributed.pk: 3, owned.pk: 3},
            ),
            (
                "annotated",
                base_queryset.with_role_annotations(self.scenario.user),
                {public.pk: 0, private.pk: 0, contributed.pk: 0, owned.pk: 0},
            ),
            (
                "annotated_anonymous",
                base_queryset.with_role_annotations(AnonymousUser()),
                {public.pk: 2, private.pk: 3, contributed.pk: 3, owned.pk: 3},
            ),
        ):
            with self.subTest(queryset=name):
                actual: dict[int, frozenset[WorkspaceRoles]] = {}
                for workspace in queryset:
                    with (
                        self.subTest(workspace=workspace),
                        self.assertNumQueries(num_queries[workspace.pk]),
                    ):
                        actual[workspace.pk] = workspace.get_roles(
                            self.scenario.user
                        )

                self.assertEqual(
                    actual, {k: frozenset(v) for k, v in expected.items()}
                )

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
            ValueError, r"inheritance chain contains the workspace itself"
        ):
            a.set_inheritance([a])

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
        a, b, c = self.a, self.b, self.c
        d = self.playground.create_workspace(name="d", public=True)

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
        wpub = self.playground.create_workspace(name="public", public=True)
        cpub = self.playground.create_collection(
            "test", CollectionCategory.SUITE, workspace=wpub
        )
        wpriv = self.playground.create_workspace(name="private", public=False)
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

    def test_skipping_chains_of_inaccessible_parents(self) -> None:
        user = self.scenario.user
        wpub1 = self.playground.create_workspace(name="public1", public=True)
        cpub1 = self.playground.create_collection(
            "test", CollectionCategory.SUITE, workspace=wpub1
        )
        wpub2 = self.playground.create_workspace(name="public2", public=True)
        cpub2 = self.playground.create_collection(
            "test", CollectionCategory.SUITE, workspace=wpub2
        )
        wpriv = self.playground.create_workspace(name="private", public=False)
        wstart = self.playground.create_workspace(name="start", public=True)

        wpriv.set_inheritance([wpub1])
        wstart.set_inheritance([wpriv, wpub2])

        self.playground.create_group_role(
            wpriv, Workspace.Roles.OWNER, users=[user]
        )

        # Lookups in the workspace itself do check restrictions
        self.assertGetCollectionEqual(wstart, "test", cpub2, user=None)
        self.assertGetCollectionEqual(wstart, "test", cpub1, user=user)

    def test_get_group_roles_not_authenticated(self) -> None:
        """Test Workspace.get_group_roles when not authenticated."""
        user = AnonymousUser()
        self.assertQuerySetEqual(self.a.get_group_roles(user), [])

    def test_get_group_roles(self) -> None:
        """Test Workspace.get_group_roles."""
        group = self.playground.create_group_role(self.a, Workspace.Roles.OWNER)
        user = self.playground.get_default_user()
        self.assertQuerySetEqual(self.a.get_group_roles(user), [])

        self.playground.add_user(group, user)
        self.assertEqual(
            list(self.a.get_group_roles(user)), [Workspace.Roles.OWNER]
        )

    def test_get_group_roles_multiple_groups(self) -> None:
        """Test Workspace.get_group_roles with the user in multiple groups."""
        group1 = self.playground.create_group_role(
            self.a, Workspace.Roles.OWNER, name="Group1"
        )
        group2 = self.playground.create_group_role(
            self.a, Workspace.Roles.OWNER, name="Group2"
        )
        group3 = self.playground.create_group_role(
            self.a, Workspace.Roles.CONTRIBUTOR, name="Group3"
        )

        user = self.playground.get_default_user()
        self.assertQuerySetEqual(self.a.get_group_roles(user), [])

        self.playground.add_user(group1, user)
        self.assertQuerySetEqual(
            self.a.get_group_roles(user), [Workspace.Roles.OWNER]
        )

        self.playground.add_user(group2, user)
        self.assertQuerySetEqual(
            self.a.get_group_roles(user), [Workspace.Roles.OWNER]
        )

        self.playground.add_user(group3, user)
        self.assertQuerySetEqual(
            self.a.get_group_roles(user),
            [Workspace.Roles.CONTRIBUTOR, Workspace.Roles.OWNER],
            ordered=False,
        )

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

    def test_set_current_public_anonymous_user(self) -> None:
        """Test set_current without user."""
        context.set_scope(self.scenario.scope)
        context.set_user(AnonymousUser())
        self.scenario.workspace.set_current()
        self.assertEqual(context.workspace, self.scenario.workspace)
        self.assertEqual(
            context._workspace_roles.get(), frozenset([Workspace.Roles.VIEWER])
        )

    def test_set_current_private_anonymous_user(self) -> None:
        """Test set_current without user."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        context.set_scope(self.scenario.scope)
        context.set_user(AnonymousUser())
        with self.assertRaisesRegex(
            ContextConsistencyError,
            "User AnonymousUser cannot access workspace debusine/System",
        ):
            self.scenario.workspace.set_current()
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

    def test_set_current_public(self) -> None:
        """Test set_current on a public workspace."""
        context.set_scope(self.scenario.scope)
        context.set_user(self.scenario.user)
        # One query to fetch the list of workspace roles
        with self.assertNumQueries(1):
            self.scenario.workspace.set_current()
        self.assertEqual(
            context.workspace_roles, frozenset((Workspace.Roles.VIEWER,))
        )

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

    def test_set_current_precached_multiple_roles(self) -> None:
        """Test set_current with multiple precached roles."""
        for public, roles, expected in (
            (False, [Workspace.Roles.OWNER], [Workspace.Roles.OWNER]),
            (
                False,
                [Workspace.Roles.VIEWER],
                [Workspace.Roles.VIEWER],
            ),
            (
                False,
                [Workspace.Roles.CONTRIBUTOR],
                [Workspace.Roles.CONTRIBUTOR],
            ),
            (
                False,
                [Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR],
                [Workspace.Roles.OWNER],
            ),
            (True, [], [Workspace.Roles.VIEWER]),
            (True, [Workspace.Roles.OWNER], [Workspace.Roles.OWNER]),
            (
                True,
                [Workspace.Roles.VIEWER],
                [Workspace.Roles.VIEWER],
            ),
            (
                True,
                [Workspace.Roles.CONTRIBUTOR],
                [Workspace.Roles.CONTRIBUTOR],
            ),
            (
                True,
                [Workspace.Roles.OWNER, Workspace.Roles.CONTRIBUTOR],
                [Workspace.Roles.OWNER],
            ),
        ):
            with self.subTest(public=public, roles=roles), context.local():
                self.scenario.workspace.public = public
                self.scenario.workspace.save()
                Group.objects.all().delete()
                for role in roles:
                    self.playground.create_group_role(
                        self.scenario.workspace,
                        role,
                        users=[self.scenario.user],
                    )
                context.set_scope(self.scenario.scope)
                context.set_user(self.scenario.user)
                workspace = Workspace.objects.get_for_context(
                    name=self.scenario.workspace.name
                )
                self.assertCountEqual(getattr(workspace, "user_roles"), roles)
                workspace.set_current()
                self.assertEqual(context.workspace_roles, frozenset(expected))

    def test_predicate_deny_from_context(self) -> None:
        """Test predicates propagating DENY from has_role."""
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        with mock.patch(
            "debusine.db.models.workspaces.Workspace.has_role",
            return_value=False,
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
        user = self.scenario.user
        for u, roles, expected in (
            (AnonymousUser(), [], True),
            (user, [], True),
            (user, [Workspace.Roles.VIEWER], True),
            (user, [Workspace.Roles.CONTRIBUTOR], True),
            (user, [Workspace.Roles.OWNER], True),
        ):
            with (
                self.subTest(user=u, roles=roles),
                self.playground.assign_role(self.scenario.workspace, u, *roles),
            ):
                self.assertPermissionPredicate(
                    self.scenario.workspace.can_display, u, expected
                )

    def test_can_display_private(self) -> None:
        """Test the can_display predicate on private workspaces."""
        user = self.scenario.user
        self.scenario.workspace.public = False
        self.scenario.workspace.save()
        for u, roles, expected in (
            (AnonymousUser(), [], False),
            (user, [], False),
            (user, [Workspace.Roles.VIEWER], True),
            (user, [Workspace.Roles.CONTRIBUTOR], True),
            (user, [Workspace.Roles.OWNER], True),
        ):
            with (
                self.subTest(user=u, roles=roles),
                self.playground.assign_role(self.scenario.workspace, u, *roles),
            ):
                self.assertPermissionPredicate(
                    self.scenario.workspace.can_display, u, expected
                )

    def test_can_configure(self) -> None:
        """Test the can_configure predicate on public workspaces."""
        self.assertRolePredicate(
            self.scenario.workspace,
            "can_create_work_requests",
            Workspace.Roles.CONTRIBUTOR,
            workers=Allow.NEVER,
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

    def test_can_create_artifacts_anonymous(self) -> None:
        """Test the can_create_artifacts predicate with an anonymous user."""
        self.assertFalse(
            self.scenario.workspace.can_create_artifacts(AnonymousUser())
        )

    def test_can_create_artifacts(self) -> None:
        """Test the can_create_artifacts predicate."""
        self.assertRolePredicate(
            self.scenario.workspace,
            "can_create_artifacts",
            Workspace.Roles.CONTRIBUTOR,
            workers=Allow.ALWAYS,
        )

    def test_can_create_work_requests_anonymous(self) -> None:
        """Test the can_create_work_requests with an anonymous user."""
        self.assertFalse(
            self.scenario.workspace.can_create_work_requests(AnonymousUser())
        )

    def test_can_create_work_requests(self) -> None:
        self.assertRolePredicate(
            self.scenario.workspace,
            "can_create_work_requests",
            Workspace.Roles.CONTRIBUTOR,
            workers=Allow.NEVER,
        )

    def test_can_create_experiment_workspace_anonymous(self) -> None:
        """Test the can_create_experiment_workspace with an anonymous user."""
        self.assertFalse(
            self.scenario.workspace.can_create_experiment_workspace(
                AnonymousUser()
            )
        )

    def test_can_create_experiment_workspace(self) -> None:
        """Test can_create_experiment_workspace."""
        self.assertRolePredicate(
            self.scenario.workspace,
            "can_create_experiment_workspace",
            Workspace.Roles.CONTRIBUTOR,
            workers=Allow.NEVER,
        )

    def test_can_edit_task_configuration_anonymous(self) -> None:
        """Test the can_edit_task_configuration with an anonymous user."""
        self.assertFalse(
            self.scenario.workspace.can_edit_task_configuration(AnonymousUser())
        )

    def test_can_edit_task_configuration(self) -> None:
        """Test can_edit_task_configuration."""
        self.assertRolePredicate(
            self.scenario.workspace,
            "can_edit_task_configuration",
            Workspace.Roles.OWNER,
            workers=Allow.NEVER,
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

    def test_list_accessible_collections_empty(self) -> None:
        workspace = self.playground.create_workspace(name="test", public=True)
        self.assertEqual(
            list(
                workspace.list_accessible_collections(
                    AnonymousUser(), CollectionCategory.TASK_CONFIGURATION
                )
            ),
            [],
        )

    def test_list_accessible_collections(self) -> None:
        base = self.playground.create_workspace(name="base", public=True)
        priv = self.playground.create_workspace(name="priv")
        pub1 = self.playground.create_workspace(name="pub1", public=True)
        pub2 = self.playground.create_workspace(name="pub2", public=True)

        base.set_inheritance([priv, pub2, pub1])
        priv.set_inheritance([pub1])

        self.playground.create_group_role(
            priv, Workspace.Roles.OWNER, [self.scenario.user]
        )

        collections: dict[str, Collection] = {}
        for workspace in base, priv, pub1, pub2:
            name_common = "config"
            name_only = f"{workspace.name}_only"
            collections[workspace.name] = self.playground.create_collection(
                name_common,
                CollectionCategory.TASK_CONFIGURATION,
                workspace=workspace,
            )
            collections[name_only] = self.playground.create_collection(
                f"{name_common}_{name_only}",
                CollectionCategory.TASK_CONFIGURATION,
                workspace=workspace,
            )

        _reverse = {v: k for k, v in collections.items()}

        for user, workspace, expected in (
            (
                None,
                base,
                ["base", "base_only", "pub2", "pub2_only", "pub1", "pub1_only"],
            ),
            (
                self.scenario.user,
                base,
                [
                    "base",
                    "base_only",
                    "priv",
                    "priv_only",
                    "pub1",
                    "pub1_only",
                    "pub2",
                    "pub2_only",
                ],
            ),
            (
                None,
                priv,
                [],
            ),
            (
                self.scenario.user,
                priv,
                ["priv", "priv_only", "pub1", "pub1_only"],
            ),
            (
                self.scenario.user,
                base,
                [
                    "base",
                    "base_only",
                    "priv",
                    "priv_only",
                    "pub1",
                    "pub1_only",
                    "pub2",
                    "pub2_only",
                ],
            ),
        ):
            with self.subTest(user=user, workspace=workspace):
                result = list(
                    workspace.list_accessible_collections(
                        user or AnonymousUser(),
                        CollectionCategory.TASK_CONFIGURATION,
                    )
                )
                actual = [_reverse[c] for c in result]
                self.assertEqual(actual, expected)

    def test_list_accessible_collections_chain_loop(self) -> None:
        base = self.playground.create_workspace(name="base", public=True)
        pub = self.playground.create_workspace(name="pub", public=True)

        base.set_inheritance([pub])
        pub.set_inheritance([base])

        cbase = self.playground.create_collection(
            "base",
            CollectionCategory.TASK_CONFIGURATION,
            workspace=base,
        )
        cpub = self.playground.create_collection(
            "pub",
            CollectionCategory.TASK_CONFIGURATION,
            workspace=pub,
        )

        self.assertEqual(
            list(
                base.list_accessible_collections(
                    AnonymousUser(), CollectionCategory.TASK_CONFIGURATION
                )
            ),
            [cbase, cpub],
        )
        self.assertEqual(
            list(
                pub.list_accessible_collections(
                    AnonymousUser(), CollectionCategory.TASK_CONFIGURATION
                )
            ),
            [cpub, cbase],
        )


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
            operation.workflow_templates,
            [self.scenario.template_sbuild, self.scenario.template_noop],
            ordered=False,
        )
        self.assertEqual(operation.artifacts.count(), 24)
        self.assertEqual(operation.work_requests.count(), 19)
        self.assertEqual(operation.collection_items.count(), 12)
        self.assertEqual(operation.collections.count(), 13)
        self.assertEqual(operation.file_in_artifacts.count(), 30)
        self.assertEqual(operation.artifact_relations.count(), 22)
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

    def test_delete_experiment_workspace(self) -> None:
        """An experiment workspace can be deleted."""
        base_workspace = self.playground.create_workspace(
            name="base", public=True
        )
        base_task_history, _ = Collection.objects.get_or_create_singleton(
            CollectionCategory.TASK_HISTORY,
            base_workspace,
            data={"old_items_to_keep": 0},
        )
        experiment_workspace = self.playground.create_workspace(
            name="experiment", public=True
        )
        experiment_workspace.set_inheritance([base_workspace])

        # Create a task-history item in the base workspace, then supersede
        # it with a similar one in the experiment workspace.
        for workspace in (base_workspace, experiment_workspace):
            template = self.playground.create_workflow_template(
                name="noop-template", task_name="noop", workspace=workspace
            )
            workflow = self.playground.create_workflow(
                task_name=template, workspace=workspace
            )
            wr = workflow.create_child(
                "noop", status=WorkRequest.Statuses.PENDING
            )
            wr.assign_worker(self.playground.create_worker())
            self.assertTrue(wr.mark_running())
            self.assertTrue(
                wr.mark_completed(
                    WorkRequest.Results.SUCCESS,
                    output_data=OutputData(
                        runtime_statistics=RuntimeStatistics(duration=1)
                    ),
                )
            )
        self.assertEqual(base_task_history.child_items.count(), 2)
        self.assertEqual(base_task_history.child_items.active().count(), 1)

        operation = DeleteWorkspaces(
            Workspace.objects.filter(pk=experiment_workspace.pk)
        )
        operation.perform_deletions()
        self.assertFalse(
            Workspace.objects.filter(pk=experiment_workspace.pk).exists()
        )

    def test_delete_cross_workspace_collection_items(self) -> None:
        """Delete collection items referring to the workspace to be deleted."""
        base_workspace = self.playground.create_workspace(
            name="base", public=True
        )
        base_package_build_logs, _ = Collection.objects.get_or_create_singleton(
            CollectionCategory.PACKAGE_BUILD_LOGS, base_workspace
        )
        experiment_workspace = self.playground.create_workspace(
            name="experiment", public=True
        )
        experiment_workspace.set_inheritance([base_workspace])

        workflow = self.playground.create_workflow(
            task_name="noop", workspace=experiment_workspace
        )
        wr = workflow.create_child("noop", status=WorkRequest.Statuses.PENDING)
        wr.add_event_reaction(
            "on_success",
            ActionUpdateCollectionWithArtifacts(
                collection=base_package_build_logs.id,
                variables={
                    "work_request_id": wr.id,
                    "vendor": "debian",
                    "codename": "sid",
                    "architecture": "all",
                    "srcpkg_name": "hello",
                    "srcpkg_version": "1.0",
                },
                artifact_filters={
                    "category": ArtifactCategory.PACKAGE_BUILD_LOG
                },
            ),
        )
        wr.assign_worker(self.playground.create_worker())
        self.assertTrue(wr.mark_running())
        self.playground.create_artifact(
            category=ArtifactCategory.PACKAGE_BUILD_LOG,
            workspace=experiment_workspace,
            work_request=wr,
        )
        self.assertTrue(wr.mark_completed(WorkRequest.Results.SUCCESS))
        self.assertEqual(base_package_build_logs.child_items.count(), 1)
        self.assertEqual(
            base_package_build_logs.child_items.active().count(), 1
        )
        item = base_package_build_logs.child_items.get()
        self.assertEqual(item.parent_collection.workspace, base_workspace)
        assert item.artifact is not None
        self.assertEqual(item.artifact.workspace, experiment_workspace)

        operation = DeleteWorkspaces(
            Workspace.objects.filter(pk=experiment_workspace.pk)
        )
        operation.perform_deletions()
        self.assertFalse(
            Workspace.objects.filter(pk=experiment_workspace.pk).exists()
        )
        self.assertEqual(base_package_build_logs.child_items.count(), 0)
