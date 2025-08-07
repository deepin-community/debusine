# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Useful support code for Debusine views."""

from typing import Any

import pygments
import pygments.formatters
import pygments.lexers
import yaml
from django.utils.safestring import SafeString


class UIDumper(yaml.SafeDumper):
    """A YAML dumper that represents multi-line strings in the literal style."""

    def represent_scalar(
        self, tag: str, value: Any, style: str | None = None
    ) -> yaml.ScalarNode:
        """Represent multi-line strings in the literal style."""
        if style is None and "\n" in value:
            style = "|"
        return super().represent_scalar(tag, value, style=style)


def format_yaml(data: Any) -> str:
    """Format YAML data as syntax highlighted HTML."""
    lexer = pygments.lexers.get_lexer_by_name("yaml")
    formatter = pygments.formatters.HtmlFormatter(
        cssclass="file_highlighted",
        linenos=False,
    )
    formatted = pygments.highlight(
        yaml.dump(data, Dumper=UIDumper), lexer, formatter
    )
    return SafeString(formatted)
