# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for override_query_parameters."""

from unittest import TestCase
from unittest.mock import MagicMock

from django.http import QueryDict

from debusine.web.templatetags.override_query_parameters import (
    override_query_parameters,
)


class OverrideQueryParametersTests(TestCase):
    """Tests for override_query_parameters() function."""

    def test_override_query_parameters(self) -> None:
        """Test override_query_parameters() return modified query parameters."""
        context = MagicMock()
        context.request.GET = QueryDict(query_string="page=1&order=id&asc=1")

        page = 2
        result = override_query_parameters(context, page=page)

        self.assertEqual(result, f"page={page}&order=id&asc=1")
