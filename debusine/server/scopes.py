# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.
"""Transparently add scopes to URLs."""

import contextlib
import re
import sys
import types
from collections.abc import Generator

from django.conf import settings
from django.urls import get_urlconf, set_urlconf

from debusine.project.urls import make_urlpatterns

scope_prefix_re = re.compile(r'^/([^/]+)(/|$)')


def get_scope_urlconf(scope_name: str) -> str:
    """Return a per-scope urlconf."""
    if scope_name == settings.DEBUSINE_DEFAULT_SCOPE:
        return "debusine.project.urls"

    module_name = f"debusine.server._urlconfs.{scope_name}"
    if module_name not in sys.modules:
        mod = types.ModuleType(scope_name, f"URLConf for scope {scope_name!r}")
        setattr(mod, "urlpatterns", make_urlpatterns(scope_name))
        sys.modules[module_name] = mod

    return module_name


@contextlib.contextmanager
def urlconf_scope(scope_name: str) -> Generator[None, None, None]:
    """Temporarily set the scope for reverse URL resolution."""
    orig = get_urlconf()
    try:
        # Note that this does the right thing also with threads and channels,
        # since Django's set_urlconf is backed by asgiref.local.Local.
        set_urlconf(get_scope_urlconf(scope_name))
        yield
    finally:
        set_urlconf(orig)
