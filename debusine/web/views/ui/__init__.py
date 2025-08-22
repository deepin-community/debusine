# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""UI helpers for model objects."""

# Import modules so their helpers can register themselves
from debusine.web.views.ui import work_request  # noqa: F401
from debusine.web.views.ui.base import UI

__all__ = ["UI"]
