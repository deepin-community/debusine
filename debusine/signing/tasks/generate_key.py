# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Signing task to generate a new key."""

import base64
from pathlib import Path

from debusine.assets import AssetCategory, SigningKeyData
from debusine.signing.db.models import Key
from debusine.signing.tasks import BaseSigningTask
from debusine.signing.tasks.models import GenerateKeyData
from debusine.tasks import DefaultDynamicData
from debusine.tasks.models import BaseDynamicTaskData


class GenerateKey(
    BaseSigningTask[GenerateKeyData, BaseDynamicTaskData],
    DefaultDynamicData[GenerateKeyData],
):
    """Task that generates a new key."""

    _key: Key | None = None

    def run(self, execute_directory: Path) -> bool:  # noqa: U100
        """Execute the task."""
        assert self.work_request_id is not None
        assert self.debusine is not None

        with self.open_debug_log_file("cmd-output.log", mode="wb") as log_file:
            self._key = Key.objects.generate(
                Key.Purpose(self.data.purpose),
                self.data.description,
                self.work_request_id,
                log_file=log_file,
            )

        return True

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Create a signing key asset."""
        assert self.debusine is not None
        assert self.workspace_name is not None
        # run() always either returns True or raises an exception; in the
        # latter case we won't get here.
        assert execution_success
        assert self._key is not None

        self.debusine.asset_create(
            category=AssetCategory.SIGNING_KEY,
            data=SigningKeyData(
                purpose=self.data.purpose,
                fingerprint=self._key.fingerprint,
                public_key=base64.b64encode(self._key.public_key).decode(),
                description=self.data.description,
            ),
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )

    def get_label(self) -> str:
        """Return the task label."""
        return f"generate {self.data.purpose} key"
