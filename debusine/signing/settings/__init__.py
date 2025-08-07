# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Debusine signing settings.

The settings are created dynamically by first importing defaults
values from :py:mod:`debusine.signing.settings.defaults` and then
values from :py:mod:`debusine.signing.settings.local` (or from
:py:mod:`debusine.signing.settings.selected` if the latter
has not been created by the administrator).
"""

import sys

from debusine.signing.settings.defaults import *  # noqa: F403

if sys.argv[1:2] == ['test']:
    from debusine.signing.settings.test import *  # noqa: F403
else:
    try:
        from debusine.signing.settings.local import *  # noqa: F403
    except ModuleNotFoundError as e:
        if e.name != "debusine.signing.settings.local":
            raise
        from debusine.signing.settings.selected import *  # noqa: F403

compute_default_settings(globals())  # noqa: F405
