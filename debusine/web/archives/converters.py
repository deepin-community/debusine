# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Custom path converters for package archives."""

from datetime import datetime, timezone

from django.urls import register_converter


class SnapshotConverter:
    """Match a snapshot ID.  See sources.list(5)."""

    regex = r"[0-9]{8}T[0-9]{6}Z"
    _timestamp_format = "%Y%m%dT%H%M%SZ"

    def to_python(self, value: str) -> datetime:
        """Convert the matched string to a timestamp."""
        return datetime.strptime(value, self._timestamp_format).replace(
            tzinfo=timezone.utc
        )

    def to_url(self, value: datetime) -> str:
        """Convert a timestamp into a string to be used in a URL."""
        return value.strftime(self._timestamp_format)


register_converter(SnapshotConverter, "snapshot")
