# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for signing task models."""

from unittest import TestCase

from debusine.signing.models import SigningMode
from debusine.signing.tasks.models import SignRepositoryIndexData


class SignRepositoryIndexDataTests(TestCase):
    """Tests for SignRepositoryIndex task data."""

    def test_mode_detached(self) -> None:
        """``mode=SigningMode.DETACHED`` is accepted."""
        data = SignRepositoryIndexData(
            suite_collection=1,
            unsigned=2,
            mode=SigningMode.DETACHED,
            signed_name="Release.gpg",
        )
        self.assertEqual(data.dict()["mode"], SigningMode.DETACHED)

    def test_mode_clear(self) -> None:
        """``mode=SigningMode.CLEAR`` is accepted."""
        data = SignRepositoryIndexData(
            suite_collection=1,
            unsigned=2,
            mode=SigningMode.CLEAR,
            signed_name="InRelease",
        )
        self.assertEqual(data.dict()["mode"], SigningMode.CLEAR)

    def test_mode_not_detached_or_clear(self) -> None:
        """``mode`` must be either ``DETACHED`` or ``CLEAR``."""
        with self.assertRaisesRegex(
            ValueError,
            "Signing mode must be 'detached' or 'clear'; got 'attached'",
        ):
            SignRepositoryIndexData(
                suite_collection=1,
                unsigned=2,
                mode=SigningMode.ATTACHED,
                signed_name="Release.signed",
            )

    def test_signed_name_not_single_path_segment(self) -> None:
        """``signed_name`` must be a single path segment."""
        with self.assertRaisesRegex(
            ValueError,
            "Signed name must be a single path segment; got '../naughty'",
        ):
            SignRepositoryIndexData(
                suite_collection=1,
                unsigned=2,
                mode=SigningMode.DETACHED,
                signed_name="../naughty",
            )
