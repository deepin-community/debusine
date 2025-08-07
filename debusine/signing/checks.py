# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine checks using Django checks framework."""

from functools import partial

from django.core.checks import register

from debusine.django.checks import all_migrations_applied

__all__ = [
    "all_migrations_applied",
]

register(partial(all_migrations_applied, "debusine-signing"))
