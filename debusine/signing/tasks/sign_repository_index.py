# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Signing task to sign a repository index."""

import shutil
from pathlib import Path
from typing import Any

from debusine.artifacts import RepositoryIndex
from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.assets import AssetCategory, KeyPurpose
from debusine.client.models import AssetPermissionCheckResponse, RelationType
from debusine.signing.db.models import Key, sign
from debusine.signing.tasks import BaseSigningTask
from debusine.signing.tasks.models import (
    SignRepositoryIndexData,
    SignRepositoryIndexDynamicData,
)
from debusine.tasks.server import TaskDatabaseInterface


class SignRepositoryIndex(
    BaseSigningTask[SignRepositoryIndexData, SignRepositoryIndexDynamicData]
):
    """Task that signs a repository index using a key."""

    TASK_VERSION = 1

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize object."""
        super().__init__(task_data, dynamic_task_data)

        self._file_path: str | None = None
        self._keys: list[Key] = []
        self._permission_check: AssetPermissionCheckResponse | None = None
        self._signed_path: Path | None = None

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> SignRepositoryIndexDynamicData:
        """Resolve lookups for this task."""
        suite = task_database.lookup_single_collection(
            self.data.suite_collection
        )
        self.ensure_collection_category(
            configuration_key="suite_collection",
            category=suite.category,
            expected=CollectionCategory.SUITE,
        )
        signing_keys: list[str] | None = None
        if suite.data.get("signing_keys") is not None:
            signing_keys = suite.data["signing_keys"]
        else:
            # TODO: This doesn't check whether the suite is actually a child
            # of the archive, although this isn't too bad in practice since
            # we won't normally end up signing suites that aren't.
            archive = task_database.lookup_singleton_collection(
                CollectionCategory.ARCHIVE
            )
            if archive is not None:
                signing_keys = archive.data.get("signing_keys")

        unsigned = task_database.lookup_single_artifact(self.data.unsigned)
        self.ensure_artifact_categories(
            configuration_key="unsigned",
            category=unsigned.category,
            expected=(ArtifactCategory.REPOSITORY_INDEX,),
        )

        return SignRepositoryIndexDynamicData(
            signing_keys=signing_keys or [], unsigned_id=unsigned.id
        )

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of input artifact IDs used by this task."""
        if not self.dynamic_data:
            return []
        return [self.dynamic_data.unsigned_id]

    def fetch_input(self, destination: Path) -> bool:
        """Fetch artifacts and database objects needed by the task."""
        assert self.debusine is not None
        assert self.dynamic_data is not None
        assert self.work_request_id is not None
        assert self.workspace_name is not None

        if not self.dynamic_data.signing_keys:
            self.append_to_log_file(
                "fetch_input.log",
                [
                    f"No signing keys are configured for "
                    f"{self.data.suite_collection}."
                ],
            )
            return False

        unsigned = self.fetch_artifact(
            self.dynamic_data.unsigned_id, destination
        )
        # debian:repository-index artifacts always contain exactly one file.
        assert len(unsigned.files) == 1
        [self._file_path] = unsigned.files.keys()

        self._keys = []
        for key in self.dynamic_data.signing_keys:
            asset_slug = f"{KeyPurpose.OPENPGP}:{key}"
            permission_check = self.debusine.asset_permission_check(
                asset_category=AssetCategory.SIGNING_KEY,
                asset_slug=asset_slug,
                permission_name="sign_with",
                artifact_id=self.dynamic_data.unsigned_id,
                work_request_id=self.work_request_id,
                workspace=self.workspace_name,
            )
            if not permission_check.has_permission:
                self.append_to_log_file(
                    "fetch_input.log",
                    [
                        f"User {permission_check.username} does not have "
                        f"permission to sign {self.dynamic_data.unsigned_id} "
                        f"with {asset_slug}."
                    ],
                )
                return False
            if (
                self._permission_check is not None
                and permission_check != self._permission_check
            ):
                self.append_to_log_file(
                    "fetch_input.log",
                    [
                        f"Mismatching permission-check responses for "
                        f"different signing keys: "
                        f"{permission_check} != {self._permission_check}"
                    ],
                )
                return False
            self._permission_check = permission_check

            try:
                self._keys.append(
                    Key.objects.get(purpose=KeyPurpose.OPENPGP, fingerprint=key)
                )
            except Key.DoesNotExist:
                self.append_to_log_file(
                    "fetch_input.log",
                    [f"Signing key {asset_slug} does not exist"],
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
        """Sign the requested file."""
        assert self.work_request_id is not None
        assert self._file_path is not None
        assert self._permission_check is not None

        with self.open_debug_log_file("cmd-output.log", mode="wb") as log_file:
            data_path = execute_directory / "input" / self._file_path
            signed_path = execute_directory / "output" / self.data.signed_name
            signed_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                sign(
                    self._keys,
                    data_path,
                    signed_path,
                    self.data.mode,
                    self.work_request_id,
                    username=self._permission_check.username,
                    user_id=self._permission_check.user_id,
                    resource=self._permission_check.resource,
                    log_file=log_file,
                )
            except Exception as e:
                log_file.write(f"Signing failed: {e}\n".encode())
                raise
            else:
                self._signed_path = signed_path

        return True

    def upload_artifacts(
        self, execute_directory: Path, *, execution_success: bool  # noqa: U100
    ) -> None:
        """Upload artifacts for the task."""
        assert self.dynamic_data is not None
        assert self.work_request_id is not None
        assert self.debusine is not None
        assert self._signed_path is not None
        # run() always either returns True or raises an exception; in the
        # latter case we won't get here.
        assert execution_success

        signed_artifact = RepositoryIndex.create(
            file=self._signed_path, path=self.data.signed_name
        )
        uploaded_signed_artifact = self.debusine.upload_artifact(
            signed_artifact,
            workspace=self.workspace_name,
            work_request=self.work_request_id,
        )
        self.debusine.relation_create(
            uploaded_signed_artifact.id,
            self.dynamic_data.unsigned_id,
            RelationType.RELATES_TO,
        )

    def get_label(self) -> str:
        """Return the task label."""
        return f"sign {self.data.signed_name} for {self.data.suite_collection}"
