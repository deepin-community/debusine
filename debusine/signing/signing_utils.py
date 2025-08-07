# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utilities used by debusine.signing."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import AnyStr

from django.core.exceptions import ImproperlyConfigured
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey


# TODO: The signing worker's systemd unit must use something along the lines
# of "TemporaryFileSystem=/tmp:noswap /var/tmp:noswap" so that private keys
# don't end up on disk.  The "noswap" option was added in Linux 6.4, so we'd
# either have to skip it and accept the possibility that private keys might
# be swapped to disk under memory pressure, or use a backported kernel for
# signing workers.
class SensitiveTemporaryDirectory(TemporaryDirectory[AnyStr]):
    """
    A temporary directory that may contain sensitive data.

    This class does nothing special right now, but it provides a marker for
    places where we might need to add additional safety checks.
    """

    def __repr__(self) -> str:
        """
        Adjust repr to note that this instance contains sensitive data.

        This has the effect that resource warnings about instances of this
        class not being cleaned up correctly will have something noticeable
        in their text.
        """
        return super().__repr__() + " (SENSITIVE)"


def read_private_key(private_key_path: Path) -> PrivateKey:
    """Read a NaCl private key."""
    try:
        if private_key_path.stat().st_mode & 0o077:
            raise ImproperlyConfigured(
                f"Permission too open for {private_key_path}. "
                f"Make sure that the file is not accessible by group or others."
            )
        private_key_bytes = private_key_path.read_bytes()
    except OSError as e:
        raise ImproperlyConfigured(f"Cannot read {private_key_path}: {e}")
    try:
        return PrivateKey(private_key_bytes)
    except CryptoError as e:
        raise ImproperlyConfigured(
            f"Cannot load key from {private_key_path}: {e}"
        )
