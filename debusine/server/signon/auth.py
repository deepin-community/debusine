# Copyright 2020-2023 Enrico Zini <enrico@debian.org>
# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Authentication backend to mark signon-managed authentication."""

from django.contrib.auth.backends import ModelBackend


class SignonAuthBackend(ModelBackend):
    """
    Auth backend for external authentication.

    There is no specific functionality, and it is currently used to mark users
    authenticated via external signon providers.
    """

    pass
