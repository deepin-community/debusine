# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Helpers for testing permissions."""

import itertools
from collections.abc import Iterable
from typing import Generic, TypeVar, cast

from debusine.db.models.permissions import RoleBase, Roles, WithImplies
from debusine.test.django import TestCase

R = TypeVar("R", bound=RoleBase, contravariant=True)


class RolesTestCase(TestCase, Generic[R]):
    """Helper functions for testing roles enums."""

    roles_class: type[R]

    @property
    def entries(self) -> list[RoleBase]:
        """List all roles defined in the class."""
        return list(cast(Iterable[R], self.roles_class))

    def assertPartialOrdering(self) -> None:
        """Ensure implications form a partial ordering."""
        # Ensure reflexive
        for a in self.entries:
            with self.subTest(a=a):
                assert isinstance(a, WithImplies)
                self.assertTrue(a.implies(a))

        if len(self.entries) <= 1:
            return

        # Ensure antisymmetric
        for a, b in itertools.product(self.entries, self.entries):
            with self.subTest(a=a, b=b):
                assert isinstance(a, WithImplies)
                assert isinstance(b, WithImplies)
                if a.implies(b) and b.implies(a):
                    self.assertEqual(a, b)

        # Ensure transitive
        for a, b, c in itertools.product(
            self.entries, self.entries, self.entries
        ):
            with self.subTest(a=a, b=b, c=c):
                assert isinstance(a, WithImplies)
                assert isinstance(b, WithImplies)
                assert isinstance(c, WithImplies)
                if a.implies(b) and b.implies(c):
                    self.assertTrue(a.implies(c))

    def assertRolesInvariants(self) -> None:
        """Check invariants in Roles enum."""
        self.assertPartialOrdering()

    def assertChoices(self) -> None:
        """Ensure choices returns the right values."""
        assert issubclass(self.roles_class, Roles)
        self.assertEqual(
            self.roles_class.choices, [(r.value, r.label) for r in self.entries]
        )
