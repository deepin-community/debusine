# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utilities used by debusine.project."""

import os

from django.core.exceptions import ImproperlyConfigured


def read_secret_key(secret_key_file: str | os.PathLike[str]) -> str:
    """
    Return the content of secret_key_file.

    If secret_key_file cannot be read, it raises ImproperlyConfigured.
    """
    try:
        if bool(os.stat(secret_key_file).st_mode & 0o077):
            raise ImproperlyConfigured(
                f'Permission too open for {secret_key_file}. '
                'Make sure that the file is not accessible by '
                'group or others'
            )
        with open(secret_key_file) as f:
            return f.read().strip()
    except OSError as exc:
        raise ImproperlyConfigured(f"Cannot read {secret_key_file}: {exc}")
