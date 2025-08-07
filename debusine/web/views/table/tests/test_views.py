# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for tabular representation of querysets."""

import abc

from debusine.db.models import Worker
from debusine.test.django import TestCase
from debusine.web.views.base import ListViewBase
from debusine.web.views.table import TableMixin
from debusine.web.views.tables import WorkerTable
from debusine.web.views.tests.utils import ViewTestMixin


class TableMixinTests(ViewTestMixin, TestCase, abc.ABC):
    """Tests for :py:class:`TableMixin`."""

    def test_get_paginator(self) -> None:
        """Test get_paginator."""
        self.playground.create_worker()

        class TestView(TableMixin[Worker], ListViewBase[Worker]):
            model = Worker
            table_class = WorkerTable
            paginate_by = 10

        view = self.instantiate_view_class(TestView, "/")
        view.object_list = Worker.objects.all()
        ctx = view.get_context_data()
        paginator = ctx["paginator"]
        self.assertIsInstance(paginator.table, WorkerTable)
        self.assertEqual(paginator.per_page, 10)
