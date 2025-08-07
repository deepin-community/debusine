# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Page navigation widget."""

from typing import Any, TYPE_CHECKING

from django.template.context import BaseContext
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

from debusine.web.views.base import Widget

if TYPE_CHECKING:
    from debusine.web.views.table.paginator import Paginator


class PageNavigation(Widget):
    """Render the page navigation bar."""

    def __init__(self, paginator: "Paginator[Any]") -> None:
        """Build the PageNavigation widget."""
        self.paginator = paginator
        self.table = paginator.table

    def render(self, context: BaseContext) -> str:  # noqa: U100
        """Render the widget."""
        paginator = self.paginator
        page_obj = self.paginator.page_obj
        elided_page_range = paginator.get_elided_page_range(page_obj.number)
        chunks: list[SafeString] = []
        if paginator.num_pages > 1:
            chunks.append(SafeString("<nav aria-label='pagination'>"))
            chunks.append(SafeString("<ul class='pagination'>"))
            for page_number in elided_page_range:
                if page_number == page_obj.number:
                    chunks.append(
                        format_html(
                            "<li class='page-item active' aria-current='page'>"
                            "<span class='page-link'>{page_number}</span>"
                            "</li>",
                            page_number=page_number,
                        )
                    )
                elif (
                    page_number  # type: ignore[comparison-overlap]
                    is page_obj.paginator.ELLIPSIS
                ):
                    chunks.append(
                        format_html(
                            "<li class='page-item'>"
                            "<span class='page-link'>{page_number}</span>"
                            "</li>",
                            page_number=page_number,
                        )
                    )
                else:
                    query = self.table.request.GET.copy()
                    query[f"{self.table.prefix}page"] = str(page_number)
                    chunks.append(
                        format_html(
                            "<li class='page-item'>"
                            "<a class='page-link' href='?{query}'>"
                            "{page_number}</a>"
                            "</li>",
                            query=query.urlencode(),
                            page_number=page_number,
                        )
                    )
            chunks.append(SafeString("</ul>"))
            chunks.append(SafeString("</nav>"))
        return mark_safe(SafeString("\n").join(chunks))
