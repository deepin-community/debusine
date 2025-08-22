# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Signing task to sign an upload using `debsign`.

This is separate from the `Sign` task because signing uploads is a
customized operation involving signing multiple files and possibly updating
checksums in the `.changes` file to match the signed versions of other
files.
"""

import os
import shutil
from pathlib import Path
from typing import Any

from debusine import utils
from debusine.artifacts import Upload
from debusine.artifacts.models import ArtifactCategory
from debusine.assets import AssetCategory, KeyPurpose
from debusine.client.models import (
    AssetPermissionCheckResponse,
    FilesResponseType,
    RelationType,
)
from debusine.signing.db.models import Key, sign
from debusine.signing.models import SigningMode
from debusine.signing.tasks import BaseSigningTask
from debusine.signing.tasks.models import DebsignData, DebsignDynamicData
from debusine.tasks.server import TaskDatabaseInterface


class Debsign(BaseSigningTask[DebsignData, DebsignDynamicData]):
    """Task that signs an upload using a key and `debsign`."""

    TASK_VERSION = 1

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize object."""
        super().__init__(task_data, dynamic_task_data)

        self._files: FilesResponseType | None = None
        self._key: Key | None = None
        self._signed_changes_path: Path | None = None
        self._permission_check: AssetPermissionCheckResponse | None = None

    @classmethod
    def analyze_worker(cls) -> dict[str, Any]:
        """Report metadata for this task on this worker."""
        metadata = super().analyze_worker()

        metadata[cls.prefix_with_task_name("available")] = (
            utils.is_command_available("debsign")
        )

        return metadata

    def can_run_on(self, worker_metadata: dict[str, Any]) -> bool:
        """Check if the specified worker can run the task."""
        if not super().can_run_on(worker_metadata):
            return False

        available_key = self.prefix_with_task_name("available")
        if not worker_metadata.get(available_key, False):
            return False

        return True

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> DebsignDynamicData:
        """Resolve artifact lookups for this task."""
        return DebsignDynamicData(
            unsigned_id=task_database.lookup_single_artifact(
                self.data.unsigned
            ).id,
        )

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of input artifact IDs used by this task."""
        if not self.dynamic_data:
            return []
        return [
            self.dynamic_data.unsigned_id,
        ]

    def fetch_input(self, destination: Path) -> bool:
        """Fetch artifacts and database objects needed by the task."""
        assert self.debusine is not None
        assert self.dynamic_data is not None
        assert self.work_request_id is not None
        assert self.workspace_name is not None

        unsigned = self.fetch_artifact(
            self.dynamic_data.unsigned_id, destination
        )
        if unsigned.category != ArtifactCategory.UPLOAD:
            self.append_to_log_file(
                "fetch_input.log",
                [
                    f"Expected unsigned artifact of category "
                    f"{ArtifactCategory.UPLOAD}; got {unsigned.category}"
                ],
            )
            return False
        self._files = unsigned.files

        asset_slug = f"openpgp:{self.data.key}"
        permission_check = self.debusine.asset_permission_check(
            asset_category=AssetCategory.SIGNING_KEY,
            asset_slug=asset_slug,
            permission_name="sign_with",
            artifact_id=unsigned.id,
            work_request_id=self.work_request_id,
            workspace=self.workspace_name,
        )
        self._permission_check = permission_check
        if not permission_check.has_permission:
            self.append_to_log_file(
                "fetch_input.log",
                [
                    f"User {permission_check.username} does not have "
                    f"permission to sign {unsigned.id} with {asset_slug}."
                ],
            )
            return False

        try:
            self._key = Key.objects.get(
                purpose=KeyPurpose.OPENPGP, fingerprint=self.data.key
            )
        except Key.DoesNotExist:
            self.append_to_log_file(
                "fetch_input.log",
                [f"Signing key openpgp:{self.data.key} does not exist"],
            )
            return False

        return True

    def configure_for_execution(
        self, download_directory: Path  # noqa: U100
    ) -> bool:
        """Configure task variables."""
        # Nothing to do here; fetch_input does it all.
        return True

    def prepare_to_run(
        self, download_directory: Path, execute_directory: Path
    ) -> None:
        """Copy downloaded files into the execution directory."""
        # download_directory and execute_directory are always on the same
        # file system.
        shutil.copytree(
            download_directory,
            execute_directory / "input",
            copy_function=os.link,
        )

    def run(self, execute_directory: Path) -> bool:
        """Sign all the requested files."""
        assert self.work_request_id is not None
        assert self._files is not None
        assert self._key is not None
        assert self._permission_check is not None

        with self.open_debug_log_file("cmd-output.log", mode="wb") as log_file:
            # Upload.files_contain_changes checks that there is exactly one
            # .changes file.
            [changes_file] = [
                file for file in self._files if file.endswith(".changes")
            ]
            data_path = execute_directory / "input" / changes_file
            signature_path = execute_directory / "output" / changes_file
            signature_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                sign(
                    [self._key],
                    data_path,
                    signature_path,
                    SigningMode.DEBSIGN,
                    self.work_request_id,
                    username=self._permission_check.username,
                    user_id=self._permission_check.user_id,
                    resource=self._permission_check.resource,
                    log_file=log_file,
                )
            except Exception as e:
                log_file.write(f"Debsign failed: {e}\n".encode())
                raise
            else:
                self._signed_changes_path = signature_path

        return True

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload artifacts for the task."""
        assert self.dynamic_data is not None
        assert self.work_request_id is not None
        assert self.debusine is not None
        assert self._signed_changes_path is not None
        # run() always either returns True or raises an exception; in the
        # latter case we won't get here.
        assert execution_success

        upload_artifact = Upload.create(changes_file=self._signed_changes_path)
        uploaded_upload_artifact = self.debusine.upload_artifact(
            upload_artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )
        self.debusine.relation_create(
            uploaded_upload_artifact.id,
            self.dynamic_data.unsigned_id,
            RelationType.RELATES_TO,
        )

    def get_label(self) -> str:
        """Return the task label."""
        return "sign upload"
