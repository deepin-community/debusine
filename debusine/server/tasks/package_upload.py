# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Task to upload Debian packages to an upload queue."""

import shutil
import tempfile
from datetime import timedelta
from ftplib import FTP
from pathlib import Path
from typing import cast

import tenacity
from fabric import Connection

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic  # type: ignore

from debusine.artifacts.models import ArtifactCategory
from debusine.db.models import Artifact
from debusine.server.tasks import BaseServerTask
from debusine.server.tasks.models import (
    PackageUploadData,
    PackageUploadDynamicData,
    PackageUploadTarget,
)
from debusine.tasks.server import TaskDatabaseInterface

# At some point it might be useful to make this configurable, but hardcoding
# it will do for now.
_TIMEOUT = timedelta(seconds=15)


class PackageUpload(
    BaseServerTask[PackageUploadData, PackageUploadDynamicData]
):
    """Task that uploads Debian packages to an upload queue."""

    TASK_VERSION = 1

    def build_dynamic_data(
        self,
        task_database: TaskDatabaseInterface,
    ) -> PackageUploadDynamicData:
        """Resolve artifact lookups for this task."""
        return PackageUploadDynamicData(
            input_upload_id=task_database.lookup_single_artifact(
                self.data.input.upload
            ).id,
        )

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of input artifact IDs used by this task."""
        if not self.dynamic_data:
            return []
        return [self.dynamic_data.input_upload_id]

    def fetch_upload(self, destination: Path) -> list[Path] | None:
        """Download the required artifacts."""
        assert self.dynamic_data

        upload = Artifact.objects.get(id=self.dynamic_data.input_upload_id)
        if upload.category != ArtifactCategory.UPLOAD:
            self.append_to_log_file(
                "fetch_upload.log",
                [
                    f"Expected input.upload of category "
                    f"{ArtifactCategory.UPLOAD}; got {upload.category}"
                ],
            )
            return None

        upload_paths: list[Path] = []
        for file_in_artifact in upload.fileinartifact_set.select_related(
            "file"
        ):
            file_path = destination / file_in_artifact.path
            # Should be checked by LocalArtifact, but let's make sure.
            if not file_path.resolve().is_relative_to(destination):
                raise AssertionError(
                    f"{file_in_artifact.path} escapes directory"
                )
            file = file_in_artifact.file
            file_backend = upload.workspace.scope.download_file_backend(file)
            with (
                file_backend.get_stream(file) as infile,
                file_path.open(mode="wb") as outfile,
            ):
                shutil.copyfileobj(infile, outfile)
            upload_paths.append(file_path)

        return sorted(
            upload_paths,
            # Some upload queues use the appearance of the .changes file as
            # an indication that the upload is complete, so sort it to the
            # end.
            key=lambda path: (path.name.endswith(".changes"), path),
        )

    def _make_target(self) -> PackageUploadTarget:
        """Make the full upload target URL."""
        full_target = str(self.data.target)
        if self.data.delayed_days is not None:
            full_target = (
                f"{full_target.rstrip('/')}/"
                f"DELAYED/{self.data.delayed_days}-day"
            )
        # TODO: mypy should be able to recognize that this returns an
        # instance of PackageUploadTarget, but for some reason it can't.
        # Maybe the pydantic v2 API will fix this once we're able to switch
        # to it?
        return cast(
            PackageUploadTarget,
            pydantic.parse_obj_as(PackageUploadTarget, full_target),
        )

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        retry=(
            tenacity.retry_if_exception_type(OSError)
            | tenacity.retry_if_exception_type(TimeoutError)
        ),
        reraise=True,
    )
    def _upload_ftp(
        self, target: PackageUploadTarget, upload_paths: list[Path]
    ) -> None:
        """Make an upload using FTP."""
        assert target.host is not None

        try:
            with FTP(timeout=_TIMEOUT.total_seconds()) as ftp:
                ftp.connect(host=target.host, port=int(target.port or 0))
                ftp.login(user=target.user or "", passwd=target.password or "")
                if target.path:
                    ftp.cwd(target.path)
                for path in upload_paths:
                    self.append_to_log_file(
                        "package-upload.log", [f"Uploading {path.name}"]
                    )
                    with path.open(mode="rb") as f:
                        ftp.storbinary(f"STOR {path.name}", f)
        except Exception as e:
            self.append_to_log_file(
                "package-upload.log", [f"Upload failed: {e}"]
            )
            raise

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        retry=(
            tenacity.retry_if_exception_type(OSError)
            | tenacity.retry_if_exception_type(TimeoutError)
        ),
        reraise=True,
    )
    def _upload_sftp(
        self, target: PackageUploadTarget, upload_paths: list[Path]
    ) -> None:
        """Make an upload using SFTP."""
        assert target.host is not None

        try:
            with Connection(
                host=target.host,
                user=target.user,
                port=None if target.port is None else int(target.port),
                connect_timeout=_TIMEOUT.total_seconds(),
            ) as connection:
                for path in upload_paths:
                    self.append_to_log_file(
                        "package-upload.log", [f"Uploading {path.name}"]
                    )
                    connection.put(
                        path, str(Path(target.path or "", path.name))
                    )
        except Exception as e:
            self.append_to_log_file(
                "package-upload.log", [f"Upload failed: {e}"]
            )
            raise

    def _execute(self) -> bool:
        """Execute the task."""
        with tempfile.TemporaryDirectory(
            prefix="debusine-package-upload-"
        ) as temp_dir:
            temp_path = Path(temp_dir)
            upload_paths = self.fetch_upload(temp_path)
            if upload_paths is None:
                return False
            target = self._make_target()

            self.append_to_log_file(
                "package-upload.log", [f"Uploading to {target}"]
            )
            match target.scheme:
                case "ftp":
                    self._upload_ftp(target, upload_paths)
                case "sftp":
                    self._upload_sftp(target, upload_paths)
                case _ as unreachable:
                    raise AssertionError(
                        f"Unexpected URL scheme: {unreachable}"
                    )
            self.append_to_log_file("package-upload.log", ["Upload succeeded"])
            return True

    def get_label(self) -> str:
        """Return the task label."""
        return f"upload to {self._make_target()}"
