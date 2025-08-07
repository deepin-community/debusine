# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Worker client to connect to Debusine."""

import sys
from typing import cast

from debusine.worker._worker import Worker

# Import the documentation from where the code lives
__doc__ = cast(str, sys.modules[Worker.__module__].__doc__)

__all__ = [
    'Worker',
]
