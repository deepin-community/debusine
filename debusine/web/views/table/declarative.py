# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Infrastructure for declarative table definitions."""


class Attribute:
    """
    Base for declarative table attributes, like columns and filters.

    Attributes have a name, which is usually taken from the table member the
    attribute is assigned to. It can optionally be explicitly given in the
    attribute to have names like ``for`` or ``sorted`` which would conflict
    with Python statements or existing names.
    """

    name: str

    def __init__(self, *, name: str | None = None):
        """Optionally set a table name."""
        if name is not None:
            self.name = name
