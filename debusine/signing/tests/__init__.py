# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the signing application."""

import random


def make_fake_fingerprint(length: int = 64) -> str:
    """Generate a random string in the style of a fingerprint."""
    return "".join(random.choices("0123456789ABCDEF", k=length))
