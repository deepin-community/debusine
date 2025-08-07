# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Typing utilities."""

from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar, cast

T = TypeVar('T')
P = ParamSpec('P')


# See https://stackoverflow.com/questions/71253495/how-to-annotate-the-type-of-arguments-forwarded-to-another-function  # noqa: E501
def copy_signature_from(
    _origin: Callable[P, T],
) -> Callable[[Callable[..., Any]], Callable[P, T]]:
    """Copy function signature from another one."""

    def decorator(target: Callable[..., Any]) -> Callable[P, T]:
        return cast(Callable[P, T], target)

    return decorator
