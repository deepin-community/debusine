# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""View mixins for pagination."""

from typing import Any, Generic, TYPE_CHECKING, TypeVar

from django.db.models import Model
from django.views.generic import View

from debusine.web.views.base import MultipleObjectMixinBase
from debusine.web.views.table.paginator import Paginator
from debusine.web.views.table.table import Table

if TYPE_CHECKING:
    from django.core.paginator import _SupportsPagination

    _SupportsPagination  # fake usage for vulture

M = TypeVar("M", bound=Model)


class TableMixin(MultipleObjectMixinBase[M], Generic[M], View):
    """Mixin for ListViews using Debusine's Paginator."""

    table_class: type[Table[M]]

    def get_paginator(
        self,
        queryset: "_SupportsPagination[M]",
        per_page: int,
        orphans: int = 0,
        allow_empty_first_page: bool = True,
        **kwargs: Any,
    ) -> Paginator[M]:
        """Return an instance of the paginator for this view."""
        table = self.table_class(self.request, queryset)
        return table.get_paginator(
            per_page,
            orphans=orphans,
            allow_empty_first_page=allow_empty_first_page,
            **kwargs,
        )
