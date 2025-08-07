# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Signing task to sign the contents of an artifact."""

import shutil
from collections import defaultdict
from itertools import zip_longest
from pathlib import Path
from typing import Any, assert_never

from django.conf import settings

from debusine import utils
from debusine.artifacts import SigningOutputArtifact
from debusine.artifacts.models import SigningResult
from debusine.assets import AssetCategory, KeyPurpose
from debusine.client.models import (
    AssetPermissionCheckResponse,
    FilesResponseType,
    RelationType,
)
from debusine.signing.db.models import Key
from debusine.signing.models import SigningMode
from debusine.signing.tasks import BaseSigningTask
from debusine.signing.tasks.models import SignData, SignDynamicData
from debusine.tasks.server import TaskDatabaseInterface


class Sign(BaseSigningTask[SignData, SignDynamicData]):
    """Task that signs data using a key."""

    TASK_VERSION = 1

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize object."""
        super().__init__(task_data, dynamic_task_data)

        self._files: dict[int, FilesResponseType] | None = None
        self._key: Key | None = None
        self._permission_checks: dict[int, AssetPermissionCheckResponse] = {}
        self._results: dict[int, list[SigningResult]] = defaultdict(list)

    @classmethod
    def analyze_worker(cls) -> dict[str, Any]:
        """Report metadata for this task on this worker."""
        metadata = super().analyze_worker()

        metadata[cls.prefix_with_task_name("available:uefi")] = (
            utils.is_command_available("sbsign")
        )

        return metadata

    def can_run_on(self, worker_metadata: dict[str, Any]) -> bool:
        """Check if the specified worker can run the task."""
        if not super().can_run_on(worker_metadata):
            return False

        # OpenPGP support is implemented in Python and is always available.
        # Other key purposes require external tools.
        if self.data.purpose != KeyPurpose.OPENPGP:
            available_key = self.prefix_with_task_name(
                f"available:{self.data.purpose}"
            )
            if not worker_metadata.get(available_key, False):
                return False

        return True

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> SignDynamicData:
        """Resolve artifact lookups for this task."""
        unsigned = task_database.lookup_multiple_artifacts(self.data.unsigned)
        return SignDynamicData(
            unsigned_ids=unsigned.get_ids(),
            unsigned_binary_package_names=[
                getattr(item.data, "binary_package_name", None)
                for item in unsigned
            ],
        )

    def get_source_artifacts_ids(self) -> list[int]:
        """Return the list of source artifact IDs used by this task."""
        if not self.dynamic_data:
            return []
        return self.dynamic_data.unsigned_ids

    def fetch_input(self, destination: Path) -> bool:
        """Fetch artifacts and database objects needed by the task."""
        assert self.debusine is not None
        assert self.dynamic_data is not None
        assert self.work_request_id is not None
        assert self.workspace_name is not None

        asset_slug = f"{self.data.purpose}:{self.data.key}"
        self._files = {}
        for unsigned_id in self.dynamic_data.unsigned_ids:
            unsigned = self.fetch_artifact(unsigned_id, destination)
            for trusted_cert in unsigned.data.get("trusted_certs") or []:
                if trusted_cert not in settings.DEBUSINE_SIGNING_TRUSTED_CERTS:
                    self.append_to_log_file(
                        "fetch_input.log",
                        [
                            f"This installation cannot sign objects that trust "
                            f"certificate '{trusted_cert}'"
                        ],
                    )
                    return False
            self._files[unsigned_id] = unsigned.files

            permission_check = self.debusine.asset_permission_check(
                asset_category=AssetCategory.SIGNING_KEY,
                asset_slug=asset_slug,
                permission_name="sign_with",
                artifact_id=unsigned_id,
                work_request_id=self.work_request_id,
                workspace=self.workspace_name,
            )
            if not permission_check.has_permission:
                self.append_to_log_file(
                    "fetch_input.log",
                    [
                        f"User {permission_check.username} does not have "
                        f"permission to sign {unsigned_id} with {asset_slug}."
                    ],
                )
                return False
            self._permission_checks[unsigned_id] = permission_check

        try:
            self._key = Key.objects.get(
                purpose=self.data.purpose, fingerprint=self.data.key
            )
        except Key.DoesNotExist:
            self.append_to_log_file(
                "fetch_input.log",
                [
                    f"Signing key {self.data.purpose}:{self.data.key} "
                    f"does not exist"
                ],
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
        shutil.copytree(download_directory, execute_directory / "input")

    def run(self, execute_directory: Path) -> bool:
        """Sign all the requested files."""
        assert self.work_request_id is not None
        assert self._files is not None
        assert self._key is not None

        with self.open_debug_log_file("cmd-output.log", mode="wb") as log_file:
            signed_any = False
            failed = False
            for unsigned_id, files in self._files.items():
                for file_path, file_response in files.items():
                    data_path = execute_directory / "input" / file_path
                    signature_path = (
                        execute_directory / "output" / f"{file_path}.sig"
                    )
                    # TODO: Implement kernel module signing with kmodsign
                    # (#939392)
                    if data_path.suffix == ".ko" and KeyPurpose.UEFI:
                        self._results[unsigned_id].append(
                            SigningResult(
                                file=file_path,
                                error_message=(
                                    "Skipped kernel module (not yet supported)"
                                ),
                            )
                        )
                        continue
                    signature_path.parent.mkdir(parents=True, exist_ok=True)
                    # TODO: The signing mode could be specified in the task
                    # data.  For now we hardcode ones that make sense.
                    match self.data.purpose:
                        case KeyPurpose.UEFI:
                            mode = SigningMode.DETACHED
                        case KeyPurpose.OPENPGP:
                            mode = SigningMode.CLEAR
                        case _ as unreachable:
                            assert_never(unreachable)
                    permission_check = self._permission_checks[unsigned_id]
                    try:
                        self._key.sign(
                            data_path,
                            signature_path,
                            mode,
                            self.work_request_id,
                            username=permission_check.username,
                            user_id=permission_check.user_id,
                            resource=permission_check.resource,
                            log_file=log_file,
                        )
                        signed_any = True
                    except Exception as e:
                        failed = True
                        self._results[unsigned_id].append(
                            SigningResult(file=file_path, error_message=str(e))
                        )
                    else:
                        self._results[unsigned_id].append(
                            SigningResult(
                                file=file_path, output_file=f"{file_path}.sig"
                            )
                        )

        return signed_any and not failed

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload artifacts for the task."""
        assert self.dynamic_data is not None
        assert self.work_request_id is not None
        assert self.debusine is not None
        assert self._key is not None

        for unsigned_id, unsigned_binary_package_name in zip_longest(
            self.dynamic_data.unsigned_ids,
            self.dynamic_data.unsigned_binary_package_names,
        ):
            signing_output_artifact = SigningOutputArtifact.create(
                KeyPurpose(self._key.purpose),
                self._key.fingerprint,
                self._results[unsigned_id],
                [
                    execute_directory / "output" / result.output_file
                    for result in self._results[unsigned_id]
                    if result.output_file is not None
                ],
                execute_directory / "output",
                binary_package_name=unsigned_binary_package_name,
            )
            uploaded_signing_output_artifact = self.debusine.upload_artifact(
                signing_output_artifact,
                workspace=self.workspace_name,
                work_request=self.work_request_id,
            )
            self.debusine.relation_create(
                uploaded_signing_output_artifact.id,
                unsigned_id,
                RelationType.RELATES_TO,
            )

    def get_label(self) -> str:
        """Return the task label."""
        return f"sign data for {self.data.purpose}"
