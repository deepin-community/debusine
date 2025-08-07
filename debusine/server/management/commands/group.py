# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
debusine-admin command to manage groups.

Note: to make commands easier to be invoked from Ansible, we take care to make
them idempotent.
"""

from collections.abc import Callable
from typing import Any, NoReturn, cast

from django.core.exceptions import ValidationError
from django.core.management import CommandError, CommandParser
from django.core.management.base import OutputWrapper
from django.db import transaction

from debusine.db.context import context
from debusine.db.models import Group, Scope, User
from debusine.db.models.scopes import ScopeRole
from debusine.django.management.debusine_base_command import DebusineBaseCommand
from debusine.server.management.management_utils import Groups, Users, get_scope


def print_validation_error(error: ValidationError, file: OutputWrapper) -> None:
    """Print a ValidationError to the given file."""
    for field, errors in error.message_dict.items():
        if field == "__all__":
            lead = ""
        else:
            lead = f"{field}: "
        for msg in errors:
            file.write(f"* {lead}{msg}")


class Command(DebusineBaseCommand):
    """Command to manage groups."""

    help = "Manage groups"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments."""
        subparsers = parser.add_subparsers(dest="action", required=True)

        group_list = subparsers.add_parser(
            "list", help=self.handle_list.__doc__
        )
        group_list.add_argument(
            '--yaml', action="store_true", help="Machine readable YAML output"
        )
        group_list.add_argument(
            "scope",
            help="list groups for this scope",
        )

        create = subparsers.add_parser(
            "create", help=self.handle_create.__doc__
        )
        create.add_argument(
            "scope_group",
            metavar="scope/name",
            help="scope/name for the new group",
        )

        rename = subparsers.add_parser(
            "rename", help=self.handle_rename.__doc__
        )
        rename.add_argument(
            "scope_group",
            metavar="scope/name",
            help="scope/name of the group to rename",
        )
        rename.add_argument("name", help="New name for the group")

        delete = subparsers.add_parser(
            "delete", help=self.handle_delete.__doc__
        )
        delete.add_argument(
            "scope_group",
            metavar="scope/name",
            help="scope/name of the group to delete",
        )

        members = subparsers.add_parser(
            "members",
            # Override default usage message since there's otherwise no way
            # to persuade argparse to put the positional argument first:
            # https://github.com/python/cpython/issues/105947
            usage="%(prog)s scope/name [options]",
            help=self.handle_members.__doc__,
        )
        members.add_argument(
            '--yaml', action="store_true", help="Machine readable YAML output"
        )
        members.add_argument(
            "scope_group",
            metavar="scope/name",
            help=(
                "scope/name for the group "
                "(must come before --add/--remove/--set options)"
            ),
        )
        members_actions = members.add_mutually_exclusive_group()
        members_actions.add_argument(
            "--add", metavar="user", nargs="+", help="add users"
        )
        members_actions.add_argument(
            "--remove", metavar="user", nargs="+", help="remove users"
        )
        members_actions.add_argument(
            "--set",
            metavar="user",
            nargs="*",
            help="set members to listed users",
        )

    def get_scope_and_group_name(self, scope_group: str) -> tuple[Scope, str]:
        """Lookup a scopename/groupname string."""
        if "/" not in scope_group:
            raise CommandError(
                f"scope_group {scope_group!r} should be in the form"
                " 'scopename/groupname'",
                returncode=3,
            )
        scope_name, group_name = scope_group.split("/", 1)
        return get_scope(scope_name), group_name

    def get_scope_and_group(self, scope_group: str) -> tuple[Scope, Group]:
        """Lookup a scopename/groupname string."""
        scope, group_name = self.get_scope_and_group_name(scope_group)
        return scope, self.get_group(scope, group_name)

    def get_group(self, scope: Scope, group_name: str) -> Group:
        """Lookup a group in a scope."""
        try:
            group = Group.objects.get(scope=scope, name=group_name)
        except Group.DoesNotExist:
            raise CommandError(
                f"Group {group_name!r} not found in scope {scope.name!r}",
                returncode=3,
            )
        return group

    def get_users(self, usernames: list[str]) -> list[User]:
        """Resolve a list of usernames into a list of users."""
        users: list[User] = []
        usernames_ok: bool = True
        errors: list[str] = []
        for username in usernames:
            try:
                users.append(User.objects.get(username=username))
            except User.DoesNotExist:
                usernames_ok = False
                errors.append(f"User {username!r} does not exist")
        if not usernames_ok:
            raise CommandError(
                "\n".join(errors),
                returncode=3,
            )
        return users

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Dispatch the requested action."""
        func = cast(
            Callable[..., NoReturn],
            getattr(self, f"handle_{options['action']}", None),
        )
        if func is None:
            raise CommandError(
                f"Action {options['action']!r} not found", returncode=3
            )

        func(*args, **options)

    def handle_list(self, **options: Any) -> NoReturn:
        """List groups in a scope."""
        scope = get_scope(options["scope"])
        groups = Group.objects.filter(scope=scope)
        Groups(options["yaml"]).print(groups, self.stdout)
        raise SystemExit(0)

    @context.disable_permission_checks()
    def handle_create(self, *, scope_group: str, **options: Any) -> NoReturn:
        """
        Create a group, specified as scope/name.

        This is idempotent, and it makes sure the named group exists.
        """
        scope, group_name = self.get_scope_and_group_name(scope_group)
        with transaction.atomic():
            if Group.objects.filter(scope=scope, name=group_name).exists():
                raise SystemExit(0)
            group = Group(scope=scope, name=group_name)
            try:
                group.full_clean()
            except ValidationError as exc:
                self.stderr.write("Created group would be invalid:")
                print_validation_error(exc, file=self.stderr)
                raise SystemExit(3)
            group.save()
        raise SystemExit(0)

    @context.disable_permission_checks()
    def handle_rename(
        self, *, scope_group: str, name: str, **options: Any
    ) -> NoReturn:
        """Rename a group."""
        scope, group = self.get_scope_and_group(scope_group)

        if name == group.name:
            raise SystemExit(0)

        group.name = name
        try:
            group.full_clean()
        except ValidationError as exc:
            self.stderr.write("Renamed group would be invalid:")
            print_validation_error(exc, file=self.stderr)
            raise SystemExit(3)

        group.save()

        raise SystemExit(0)

    def handle_delete(self, *, scope_group: str, **options: Any) -> NoReturn:
        """Delete a group."""
        scope, group_name = self.get_scope_and_group_name(scope_group)

        with transaction.atomic():
            if not Group.objects.filter(scope=scope, name=group_name).exists():
                raise SystemExit(0)
            group = self.get_group(scope, group_name)
            ScopeRole.objects.filter(group=group).delete()
            group.delete()

        raise SystemExit(0)

    @context.disable_permission_checks()
    def handle_members(self, *, scope_group: str, **options: Any) -> NoReturn:
        """Display or change group membership."""
        scope, group = self.get_scope_and_group(scope_group)

        # Change membership
        change_requested: bool = False
        with transaction.atomic():
            if (usernames := options["set"]) is not None:
                change_requested = True
                group.users.set(self.get_users(usernames))
            if usernames := options["add"]:
                change_requested = True
                for user in self.get_users(usernames):
                    group.add_user(user)
            if usernames := options["remove"]:
                change_requested = True
                for user in self.get_users(usernames):
                    group.remove_user(user)

        if not change_requested:
            # List users
            Users(options["yaml"]).print(group.users.all(), self.stdout)
            raise SystemExit(0)
        raise SystemExit(0)
