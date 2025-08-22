# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the scopes models."""
from typing import ClassVar

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings

from debusine.db.context import context
from debusine.db.models import FileInStore, FileStore, Group, Scope, User
from debusine.db.models.permissions import Allow
from debusine.db.models.scopes import (
    ScopeRole,
    ScopeRoleBase,
    ScopeRoles,
    is_valid_scope_name,
)
from debusine.db.models.tests.utils import RolesTestCase
from debusine.db.playground import scenarios
from debusine.test.django import TestCase


class ScopeRolesTests(RolesTestCase[ScopeRoles]):
    """Tests for the ScopeRoles class."""

    roles_class = ScopeRoles

    def test_invariants(self) -> None:
        self.assertRolesInvariants()

    def test_from_iterable(self) -> None:
        for value, expected in (
            ([], []),
            (["owner"], [Scope.Roles.OWNER]),
            ([Scope.Roles.OWNER], [Scope.Roles.OWNER]),
            (["owner", "owner", Scope.Roles.OWNER], [Scope.Roles.OWNER]),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    Scope.Roles.from_iterable(value),
                    frozenset(expected),
                )

    def test_choices(self) -> None:
        self.assertChoices()

    def test_values(self) -> None:
        self.assertEqual(ScopeRoles.OWNER.label, "Owner")
        self.assertEqual(
            ScopeRoles.OWNER.implied_by_scope_roles,
            frozenset([ScopeRoles.OWNER]),
        )

    def test_init_invalid_implication(self) -> None:
        r = ScopeRoleBase("test", implied_by=[Scope.Roles.OWNER])
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            r"Scope roles do not support implications"
            r" by <enum 'ScopeRoles'>",
        ):
            r._setup()


class ScopeManagerTests(TestCase):
    """Tests for the ScopeManager class."""

    scenario = scenarios.DefaultScopeUser()

    def test_get_roles_model(self) -> None:
        """Test the get_roles_model method."""
        self.assertIs(Scope.objects.get_roles_model(), ScopeRole)

    def test_with_role(self) -> None:
        self.scenario.scope_owners.users.add(self.scenario.user)
        other = self.playground.create_user("other")

        for user, expected in (
            (None, []),
            (AnonymousUser(), []),
            (other, []),
            (self.scenario.user, [self.scenario.scope]),
        ):
            with self.subTest(user=user):
                self.assertQuerySetEqual(
                    Scope.objects.with_role(user, Scope.Roles.OWNER), expected
                )


@override_settings(LANGUAGE_CODE="en-us")
class ScopeTests(TestCase):
    """Tests for the Scope class."""

    scenario = scenarios.DefaultScopeUser()

    def assertHasRole(
        self,
        *,
        user: User | None = None,
        scope: Scope | None = None,
        hits_database: bool = False,
    ) -> None:
        """Ensure the user has OWNER role on the scope."""
        scope = scope or self.scenario.scope
        user = user or self.scenario.user
        expected_queries = 1 if hits_database else 0
        with self.assertNumQueries(expected_queries):
            self.assertTrue(scope.has_role(user, Scope.Roles.OWNER))

    def assertHasNoRole(
        self,
        *,
        user: User | None = None,
        scope: Scope | None = None,
        hits_database: bool = False,
    ) -> None:
        """Ensure the user does not have OWNER role on the scope."""
        scope = scope or self.scenario.scope
        user = user or self.scenario.user
        expected_queries = 1 if hits_database else 0
        with self.assertNumQueries(expected_queries):
            self.assertFalse(scope.has_role(user, Scope.Roles.OWNER))

    def test_create(self) -> None:
        """Test basic behavior."""
        scope = Scope.objects.create(name="test")
        self.assertEqual(scope.name, "test")
        self.assertEqual(str(scope), "test")

    def test_unique(self) -> None:
        """Check that scope names and labels are unique."""
        Scope.objects.create(name="test", label="Test")
        with transaction.atomic():
            with self.assertRaisesRegex(
                IntegrityError,
                # Only match constraint name to support non-english locales
                # "duplicate key value violates unique constraint"
                r"(?s)duplicate key value violates unique constraint.+"
                r"Key \(name\)=\(test\) already exists",
            ):
                Scope.objects.create(name="test", label="Other test")
        with transaction.atomic():
            with self.assertRaisesRegex(
                IntegrityError,
                # Only match constraint name to support non-english locales
                # "duplicate key value violates unique constraint"
                r"(?s)duplicate key value violates unique constraint.+"
                r"Key \(label\)=\(Test\) already exists",
            ):
                Scope.objects.create(name="test1", label="Test")
        Scope.objects.create(name="test1", label="Test1")

    def test_fallback(self) -> None:
        """The fallback scope for migrations exists."""
        scope = Scope.objects.get(name="debusine")
        self.assertEqual(scope.name, "debusine")

    def test_is_valid_scope_name(self) -> None:
        """Test is_valid_scope_name."""
        valid_names = (
            "debian",
            "debian-lts",
            "debusine",
            "c",
            "c++",
            "foo_bar",
            "foo.bar",
            "foo+bar",
            "tail_",
            "c--",
        )
        invalid_names = (
            "api",
            "admin",
            "user",
            "+tag",
            "-tag",
            ".profile",
            "_reserved",
            "foo:bar",
        )

        for name in valid_names:
            with self.subTest(name=name):
                self.assertTrue(is_valid_scope_name(name))

        for name in invalid_names:
            with self.subTest(name=name):
                self.assertFalse(is_valid_scope_name(name))

    def test_scope_name_validation(self) -> None:
        """Test validation for scope names."""
        Scope(name="foo", label="Foo").full_clean()
        Scope(name="foo_", label="Foo").full_clean()
        with self.assertRaises(ValidationError) as exc:
            Scope(name="_foo", label="Foo").full_clean()
        self.assertEqual(
            exc.exception.message_dict,
            {'name': ["'_foo' is not a valid scope name"]},
        )

    def test_get_group_roles_not_authenticated(self) -> None:
        """Test Scope.get_group_roles when not authenticated."""
        scope = Scope.objects.create(name="Scope")
        user = AnonymousUser()
        self.assertQuerySetEqual(scope.get_group_roles(user), [])

    def test_get_group_roles(self) -> None:
        """Test Scope.get_group_roles."""
        scope = Scope.objects.create(name="Scope")
        group = self.playground.create_group_role(scope, ScopeRole.Roles.OWNER)

        user = self.scenario.user
        self.assertQuerySetEqual(scope.get_group_roles(user), [])

        self.playground.add_user(group, user)
        self.assertEqual(
            list(scope.get_group_roles(user)), [ScopeRole.Roles.OWNER]
        )

    def test_get_group_roles_multiple_groups(self) -> None:
        """Test Scope.get_group_roles with the user in multiple groups."""
        scope = Scope.objects.create(name="Scope")
        group1 = self.playground.create_group_role(
            scope, ScopeRole.Roles.OWNER, name="Group1"
        )
        group2 = self.playground.create_group_role(
            scope, ScopeRole.Roles.OWNER, name="Group2"
        )

        user = self.scenario.user
        self.assertQuerySetEqual(scope.get_group_roles(user), [])

        self.playground.add_user(group1, user)
        self.assertEqual(
            list(scope.get_group_roles(user)), [ScopeRole.Roles.OWNER]
        )

        self.playground.add_user(group2, user)
        self.assertEqual(
            list(scope.get_group_roles(user)), [ScopeRole.Roles.OWNER]
        )

    def test_has_role_anonymous_user(self) -> None:
        """Text has_role with anonymous users."""
        for user in (None, AnonymousUser()):
            with self.subTest(user=user), self.assertNumQueries(0):
                self.assertFalse(
                    self.scenario.scope.has_role(user, Scope.Roles.OWNER)
                )

    def test_has_role_no_context(self) -> None:
        """Test has_role with no context set."""
        self.assertHasNoRole(hits_database=True)
        self.scenario.scope_owners.users.add(self.scenario.user)
        self.assertHasRole(hits_database=True)

    def test_has_role_user_not_in_context(self) -> None:
        """Test has_role with a different user than in context."""
        self.scenario.set_current()
        user = self.playground.create_user("test")
        self.assertHasNoRole(user=user, hits_database=True)
        self.scenario.scope_owners.users.add(user)
        self.assertHasRole(user=user, hits_database=True)

    def test_has_role_scope_not_in_context(self) -> None:
        """Test has_role with a different scope than in context."""
        self.scenario.set_current()
        scope = self.playground.get_or_create_scope("test")
        self.assertHasNoRole(scope=scope, hits_database=True)
        self.playground.create_group_role(
            scope, Scope.Roles.OWNER, users=[self.scenario.user]
        )
        self.assertHasRole(scope=scope, hits_database=True)

    def test_has_role_from_context(self) -> None:
        """Test has_role with information from context."""
        with context.local():
            self.scenario.set_current()
            self.assertHasNoRole(hits_database=False)

        self.scenario.scope_owners.users.add(self.scenario.user)

        with context.local():
            self.scenario.set_current()
            self.assertHasRole(hits_database=False)

    def test_upload_file_stores(self) -> None:
        """`upload_file_stores` returns stores in the correct order."""
        fileobj = self.playground.create_file(contents=b"test")
        scope = self.scenario.scope
        first_store = scope.file_stores.get()
        read_only_store = FileStore.objects.create(
            name="read_only",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "read_only"},
        )
        scope.file_stores.add(
            read_only_store, through_defaults={"read_only": True}
        )
        other_store = FileStore.objects.create(
            name="other",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "other"},
        )
        max_size_store = FileStore.objects.create(
            name="full",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "full"},
            max_size=5,
        )
        scope.file_stores.add(other_store, max_size_store)

        self.assertQuerySetEqual(
            scope.upload_file_stores(fileobj),
            [first_store, other_store, max_size_store],
        )
        self.assertEqual(
            scope.upload_file_backend(fileobj).db_store,
            first_store.get_backend_object().db_store,
        )

        # If a store's max_size would be exceeded by adding the new file, it
        # is not eligible to accept the upload.
        self.playground.create_file_in_backend(
            max_size_store.get_backend_object(), contents=b"abcd"
        )

        max_size_store.refresh_from_db()
        self.assertEqual(max_size_store.total_size, 4)
        self.assertQuerySetEqual(
            scope.upload_file_stores(fileobj), [first_store, other_store]
        )
        self.assertEqual(
            scope.upload_file_backend(fileobj).db_store,
            first_store.get_backend_object().db_store,
        )

        # The default store has an upload priority of 100.  Test ordering
        # relative to that.
        other_store_extra = scope.filestoreinscope_set.get(
            file_store=other_store
        )
        other_store_extra.upload_priority = 200
        other_store_extra.save()

        self.assertQuerySetEqual(
            scope.upload_file_stores(fileobj), [other_store, first_store]
        )
        self.assertEqual(
            scope.upload_file_backend(fileobj).db_store,
            other_store.get_backend_object().db_store,
        )

        other_store_extra.upload_priority = 90
        other_store_extra.save()

        self.assertQuerySetEqual(
            scope.upload_file_stores(fileobj), [first_store, other_store]
        )
        self.assertEqual(
            scope.upload_file_backend(fileobj).db_store,
            first_store.get_backend_object().db_store,
        )

    def test_upload_file_stores_enforce_soft_limits(self) -> None:
        """`upload_file_stores` can optionally enforce soft limits."""
        fileobj = self.playground.create_file(contents=b"test")
        scope = self.scenario.scope
        first_store = scope.file_stores.get()
        soft_max_size_store = FileStore.objects.create(
            name="near-full",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "near-full"},
            soft_max_size=5,
            max_size=10,
        )
        scope.file_stores.add(soft_max_size_store)

        # If a store's soft_max_size would be exceeded by adding the new
        # file, and soft limits are enforced, it is not eligible to accept
        # the upload.
        self.playground.create_file_in_backend(
            soft_max_size_store.get_backend_object(), contents=b"abcd"
        )

        soft_max_size_store.refresh_from_db()
        self.assertEqual(soft_max_size_store.total_size, 4)
        self.assertQuerySetEqual(
            scope.upload_file_stores(fileobj),
            [first_store, soft_max_size_store],
        )
        self.assertQuerySetEqual(
            scope.upload_file_stores(fileobj, enforce_soft_limits=True),
            [first_store],
        )
        self.assertEqual(
            scope.upload_file_backend(fileobj).db_store,
            first_store.get_backend_object().db_store,
        )

    def test_upload_file_stores_include_write_only(self) -> None:
        """`upload_file_stores` can optionally include write-only stores."""
        fileobj = self.playground.create_file(contents=b"test")
        scope = self.scenario.scope
        first_store = scope.file_stores.get()
        write_only_store = FileStore.objects.create(
            name="write_only",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "write_only"},
        )
        scope.file_stores.add(
            write_only_store, through_defaults={"write_only": True}
        )

        self.assertQuerySetEqual(
            scope.upload_file_stores(fileobj), [first_store]
        )
        self.assertQuerySetEqual(
            scope.upload_file_stores(fileobj, include_write_only=True),
            [first_store, write_only_store],
        )

    def test_download_file_stores(self) -> None:
        """`download_file_stores` returns stores in the correct order."""
        fileobj = self.playground.create_file()
        scope = self.scenario.scope
        first_store = scope.file_stores.get()
        write_only_store = FileStore.objects.create(
            name="write_only",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "write_only"},
        )
        scope.file_stores.add(
            write_only_store, through_defaults={"write_only": True}
        )
        other_store = FileStore.objects.create(
            name="other",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "other"},
        )
        scope.file_stores.add(other_store)

        self.assertQuerySetEqual(scope.download_file_stores(fileobj), [])
        self.assertRaises(IndexError, scope.download_file_backend, fileobj)

        FileInStore.objects.create(file=fileobj, store=first_store)
        FileInStore.objects.create(file=fileobj, store=write_only_store)

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [first_store]
        )
        self.assertEqual(
            scope.download_file_backend(fileobj).db_store,
            first_store.get_backend_object().db_store,
        )

        FileInStore.objects.create(file=fileobj, store=other_store)

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [first_store, other_store]
        )
        self.assertEqual(
            scope.download_file_backend(fileobj).db_store,
            first_store.get_backend_object().db_store,
        )

        # The default store has upload and download priorities of 100.  Test
        # ordering relative to that.
        other_store_extra = scope.filestoreinscope_set.get(
            file_store=other_store
        )
        other_store_extra.download_priority = 200
        other_store_extra.save()

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [other_store, first_store]
        )
        self.assertEqual(
            scope.download_file_backend(fileobj).db_store,
            other_store.get_backend_object().db_store,
        )

        other_store_extra.download_priority = 100
        other_store_extra.upload_priority = 200
        other_store_extra.save()

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [other_store, first_store]
        )
        self.assertEqual(
            scope.download_file_backend(fileobj).db_store,
            other_store.get_backend_object().db_store,
        )

        other_store_extra.upload_priority = 90
        other_store_extra.save()

        self.assertQuerySetEqual(
            scope.download_file_stores(fileobj), [first_store, other_store]
        )
        self.assertEqual(
            scope.download_file_backend(fileobj).db_store,
            first_store.get_backend_object().db_store,
        )

    def test_can_display(self) -> None:
        """Test the can_display predicate."""
        scope = self.scenario.scope
        scope2 = Scope.objects.create(name="Scope2")
        for user in (AnonymousUser(), self.scenario.user):
            with self.subTest(user=user):
                self.assertTrue(scope.can_display(user))
                self.assertTrue(scope2.can_display(user))
                self.assertQuerySetEqual(
                    Scope.objects.can_display(user),
                    [scope, scope2],
                    ordered=False,
                )

    def test_can_create_workspace(self) -> None:
        self.assertRolePredicate(
            self.scenario.scope,
            "can_create_workspace",
            Scope.Roles.OWNER,
            workers=Allow.NEVER,
        )


class ScopeRoleTests(TestCase):
    """Tests for the ScopeRole class."""

    scope1: ClassVar[Scope]
    scope2: ClassVar[Scope]
    group1: ClassVar[Group]
    group2: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up common test data."""
        super().setUpTestData()
        cls.scope1 = cls.playground.get_or_create_scope(name="Scope1")
        cls.scope2 = cls.playground.get_or_create_scope(name="Scope2")
        cls.group1 = cls.playground.create_group("Group1", scope=cls.scope1)
        cls.group2 = cls.playground.create_group("Group2", scope=cls.scope2)

    def test_str(self) -> None:
        """Test stringification."""
        sr = ScopeRole(
            group=self.group1, resource=self.scope1, role=ScopeRole.Roles.OWNER
        )
        self.assertEqual(str(sr), "Scope1/Group1─owner⟶Scope1")

    def test_assign_multiple_groups(self) -> None:
        """Multiple group can share a role on a scope."""
        ScopeRole.objects.create(
            group=self.group1, resource=self.scope1, role=ScopeRole.Roles.OWNER
        )
        ScopeRole.objects.create(
            group=self.group2, resource=self.scope1, role=ScopeRole.Roles.OWNER
        )

    def test_assign_multiple_scopes(self) -> None:
        """A group can be given a role in different scopes."""
        ScopeRole.objects.create(
            group=self.group1, resource=self.scope1, role=ScopeRole.Roles.OWNER
        )
        ScopeRole.objects.create(
            group=self.group1, resource=self.scope2, role=ScopeRole.Roles.OWNER
        )

    def test_on_delete_cascade(self) -> None:
        """Test on delete cascade behaviour."""
        assignment = ScopeRole.objects.create(
            group=self.group1, resource=self.scope1, role=ScopeRole.Roles.OWNER
        )
        self.group1.delete()
        self.assertFalse(ScopeRole.objects.filter(pk=assignment.pk).exists())

        # We cannot test this case, because self.group2 references self.scope2
        # and prevents its deletion
        # assignment = ScopeRole.objects.create(
        #     group=self.group2, resource=self.scope2,
        #     role=ScopeRole.Roles.OWNER
        # )
        # self.scope2.delete()
        # self.assertFalse(ScopeRole.objects.filter(pk=assignment.pk).exists())


@override_settings(LANGUAGE_CODE="en-us")
class FileStoreInScopeTests(TestCase):
    """Tests for the FileStoreInScope class."""

    scenario = scenarios.DefaultScopeUser()

    def test_str(self) -> None:
        """`FileStoreInScope.__str__` returns basic information."""
        scope = self.scenario.scope
        file_store = scope.file_stores.get()
        file_store_extra = scope.filestoreinscope_set.get()

        self.assertEqual(
            file_store_extra.__str__(), f"{scope}/{file_store.name}"
        )

    def test_instance_wide(self) -> None:
        """Instance-wide file stores can be used by multiple scopes."""
        scope = self.scenario.scope
        other_scope = self.playground.get_or_create_scope(name="other")
        file_store = FileStore.objects.create(
            name="instance_wide",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "instance_wide"},
        )

        scope.file_stores.add(file_store)
        other_scope.file_stores.add(file_store)

        self.assertTrue(scope.file_stores.filter(pk=file_store.pk).exists())
        self.assertTrue(
            other_scope.file_stores.filter(pk=file_store.pk).exists()
        )

    def test_non_instance_wide(self) -> None:
        """Non-instance-wide file stores can only be used by a single scope."""
        scope = self.scenario.scope
        other_scope = self.playground.get_or_create_scope(name="other")
        file_store = FileStore.objects.create(
            name="just_one_scope",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "just_one_scope"},
            instance_wide=False,
        )

        scope.file_stores.add(file_store)
        with self.assertRaisesRegex(
            IntegrityError,
            r'duplicate key value violates unique constraint '
            r'"db_filestoreinscope_unique_file_store_not_instance_wide"',
        ):
            other_scope.file_stores.add(file_store)

    def test_change_instance_wide(self) -> None:
        """Changing `FileStore.instance_wide` checks constraints."""
        scope = self.scenario.scope
        other_scope = self.playground.get_or_create_scope(name="other")
        default_file_store = scope.file_stores.get()
        file_store = FileStore.objects.create(
            name="instance_wide",
            backend=FileStore.BackendChoices.MEMORY,
            configuration={"name": "instance_wide"},
        )

        scope.file_stores.add(file_store)
        file_store.instance_wide = False
        file_store.save()

        # The changed instance_wide value is mirrored to the field used by
        # the constraint.
        self.assertFalse(
            scope.filestoreinscope_set.get(
                file_store=file_store
            )._file_store_instance_wide
        )

        # The default file store's instance_wide setting is untouched.
        self.assertTrue(default_file_store.instance_wide)
        self.assertTrue(
            scope.filestoreinscope_set.get(
                file_store=default_file_store
            )._file_store_instance_wide
        )

        # Changing instance_wide fails if the constraint would be violated.
        file_store.instance_wide = True
        file_store.save()
        other_scope.file_stores.add(file_store)
        file_store.instance_wide = False
        with self.assertRaisesRegex(
            IntegrityError,
            r'duplicate key value violates unique constraint '
            r'"db_filestoreinscope_unique_file_store_not_instance_wide"',
        ):
            file_store.save()
