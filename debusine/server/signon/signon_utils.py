# Copyright 2016-2023 Enrico Zini <enrico@debian.org>
# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Helper functions for the signon module."""


def split_full_name(name: str) -> tuple[str, str]:
    """
    Arbitrary split a full name into (first_name, last_name).

    This is better than nothing, but not a lot better than that.
    """
    # See http://www.kalzumeus.com/2010/06/17/falsehoods-programmers-believe-about-names/  # noqa
    fn = name.split()
    if len(fn) == 1:
        return fn[0], ""
    elif len(fn) == 2:
        return fn[0], fn[1]
    elif len(fn) == 3:
        return " ".join(fn[0:2]), fn[2]
    else:
        middle = len(fn) // 2
        return " ".join(fn[:middle]), " ".join(fn[middle:])
