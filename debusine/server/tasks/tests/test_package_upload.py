# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for uploading Debian packages to an upload queue."""

import logging
import os
from pathlib import Path
from unittest import mock

from debusine.artifacts.models import ArtifactCategory, DebianUpload
from debusine.db.context import context
from debusine.db.models import Artifact, File
from debusine.server.tasks import PackageUpload
from debusine.server.tasks.models import PackageUploadDynamicData
from debusine.server.tasks.tests.ftp_server import FakeFTPServerProcess
from debusine.server.tasks.tests.sftp_server import FakeSFTPServerProcess
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import FakeTaskDatabase, TaskHelperMixin
from debusine.test.django import TestCase


class PackageUploadTests(TaskHelperMixin[PackageUpload], TestCase):
    """Tests for :py:class:`PackageUpload`."""

    SAMPLE_TASK_DATA = {
        "input": {"upload": "internal@collections/name:upload-source"},
        "target": "ftp://upload.example.org/queue/",
    }

    def setUp(self) -> None:
        """Initialize test."""
        self.configure_task()

        # Silence logging from pyftpdlib.
        pyftpdlib_logger = logging.getLogger("pyftpdlib")
        self.addCleanup(setattr, pyftpdlib_logger, "handlers", [])
        pyftpdlib_logger.addHandler(logging.NullHandler())

    def tearDown(self) -> None:
        """Delete debug log files directory if it exists."""
        if self.task._debug_log_files_directory:
            self.task._debug_log_files_directory.cleanup()

    @context.disable_permission_checks()
    def create_source_upload(self) -> Artifact:
        """Create a minimal source upload."""
        filenames = [
            "foo_1.0.orig.tar.xz",
            "foo_1.0-1.debian.tar.xz",
            "foo_1.0-1.dsc",
        ]
        artifact, _ = self.playground.create_artifact(
            paths=["foo_1.0-1_source.changes", *filenames],
            category=ArtifactCategory.UPLOAD,
            data={
                "type": "dpkg",
                "changes_fields": [
                    {"name": filename} for filename in filenames
                ],
            },
            create_files=True,
        )
        return artifact

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        task_db = FakeTaskDatabase(
            single_lookups={
                # input.upload
                ("internal@collections/name:upload-source", None): ArtifactInfo(
                    id=10,
                    category=ArtifactCategory.UPLOAD,
                    data=DebianUpload(
                        type="dpkg",
                        changes_fields={
                            "Architecture": "source",
                            "Files": [{"name": "hello_1.0.dsc"}],
                        },
                    ),
                )
            }
        )

        self.assertEqual(
            self.task.compute_dynamic_data(task_db),
            PackageUploadDynamicData(input_upload_id=10),
        )

    @context.disable_permission_checks()
    def test_fetch_upload_wrong_category(self) -> None:
        """fetch_upload checks the category of the input.upload artifact."""
        self.task.set_work_request(self.playground.create_work_request())
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST
        )
        self.task.dynamic_data = PackageUploadDynamicData(
            input_upload_id=artifact.id
        )

        self.assertIsNone(
            self.task.fetch_upload(self.create_temporary_directory())
        )

        assert self.task._debug_log_files_directory is not None
        self.assertEqual(
            Path(
                self.task._debug_log_files_directory.name, "fetch_upload.log"
            ).read_text(),
            "Expected input.upload of category debian:upload; got "
            "debusine:test\n",
        )

    @context.disable_permission_checks()
    def test_fetch_upload_gathers_upload_paths(self) -> None:
        """fetch_upload gathers information from the input.upload artifact."""
        debian_upload_artifact = self.create_source_upload()
        self.task.set_work_request(self.playground.create_work_request())
        self.task.dynamic_data = PackageUploadDynamicData(
            input_upload_id=debian_upload_artifact.id
        )
        destination = self.create_temporary_directory()
        expected_upload_hashes = {
            destination / fia.path: fia.file.hash_digest
            for fia in debian_upload_artifact.fileinartifact_set.select_related(
                "file"
            )
        }

        upload_paths = self.task.fetch_upload(destination)

        assert upload_paths is not None
        # The files are downloaded with the correct contents.
        self.assertEqual(
            {path: File.calculate_hash(path) for path in upload_paths},
            expected_upload_hashes,
        )
        # The .changes file is sorted to the end of upload_paths.
        self.assertTrue(upload_paths[-1].name.endswith(".changes"))

    @context.disable_permission_checks()
    def test_execute_no_upload_paths(self) -> None:
        """execute() returns False if fetch_upload returned no paths."""
        self.task.set_work_request(self.playground.create_work_request())
        artifact, _ = self.playground.create_artifact(
            category=ArtifactCategory.TEST
        )
        self.task.dynamic_data = PackageUploadDynamicData(
            input_upload_id=artifact.id
        )

        self.assertFalse(self.task.execute())

    @context.disable_permission_checks()
    def test_execute_ftp(self) -> None:
        """execute() can make an upload using FTP."""
        username = "user"
        remote_home_directory = self.create_temporary_directory()
        (remote_home_directory / "queue").mkdir()
        server = FakeFTPServerProcess(username, remote_home_directory)
        server.start()
        self.addCleanup(server.stop)
        host, port = server.address
        self.configure_task(
            override={"target": f"ftp://{username}@{host}:{port}/queue/"}
        )
        self.task.set_work_request(self.playground.create_work_request())
        directory = self.create_temporary_directory()
        deb_path = directory / "foo_1.0-1_amd64.deb"
        changes_path = directory / "foo_1.0-1_amd64.changes"
        self.write_deb_file(deb_path)
        self.write_changes_file(changes_path, [deb_path])

        with mock.patch.object(
            self.task, "fetch_upload", return_value=[deb_path, changes_path]
        ):
            self.assertTrue(self.task.execute())

        for path in (deb_path, changes_path):
            self.assertEqual(
                (remote_home_directory / "queue" / path.name).read_bytes(),
                path.read_bytes(),
            )

    @context.disable_permission_checks()
    def test_execute_ftp_no_path(self) -> None:
        """execute() handles FTP URLs with no path."""
        username = "user"
        remote_home_directory = self.create_temporary_directory()
        server = FakeFTPServerProcess(username, remote_home_directory)
        server.start()
        self.addCleanup(server.stop)
        host, port = server.address
        self.configure_task(
            override={"target": f"ftp://{username}@{host}:{port}"}
        )
        self.task.set_work_request(self.playground.create_work_request())
        directory = self.create_temporary_directory()
        deb_path = directory / "foo_1.0-1_amd64.deb"
        changes_path = directory / "foo_1.0-1_amd64.changes"
        self.write_deb_file(deb_path)
        self.write_changes_file(changes_path, [deb_path])

        with mock.patch.object(
            self.task, "fetch_upload", return_value=[deb_path, changes_path]
        ):
            self.assertTrue(self.task.execute())

        for path in (deb_path, changes_path):
            self.assertEqual(
                (remote_home_directory / path.name).read_bytes(),
                path.read_bytes(),
            )

    @context.disable_permission_checks()
    def test_execute_ftp_delayed(self) -> None:
        """execute() can make a delayed upload using FTP."""
        username = "user"
        remote_home_directory = self.create_temporary_directory()
        (remote_home_directory / "queue" / "DELAYED" / "3-day").mkdir(
            parents=True
        )
        server = FakeFTPServerProcess(username, remote_home_directory)
        server.start()
        self.addCleanup(server.stop)
        host, port = server.address
        self.configure_task(
            override={
                "target": f"ftp://{username}@{host}:{port}/queue/",
                "delayed_days": 3,
            }
        )
        self.task.set_work_request(self.playground.create_work_request())
        directory = self.create_temporary_directory()
        deb_path = directory / "foo_1.0-1_amd64.deb"
        changes_path = directory / "foo_1.0-1_amd64.changes"
        self.write_deb_file(deb_path)
        self.write_changes_file(changes_path, [deb_path])

        with mock.patch.object(
            self.task, "fetch_upload", return_value=[deb_path, changes_path]
        ):
            self.assertTrue(self.task.execute())

        for path in (deb_path, changes_path):
            self.assertEqual(
                (
                    remote_home_directory
                    / "queue"
                    / "DELAYED"
                    / "3-day"
                    / path.name
                ).read_bytes(),
                path.read_bytes(),
            )

    def test_execute_ftp_retries_timeouts(self) -> None:
        """execute() retries FTP upload timeouts."""
        self.task.set_work_request(self.playground.create_work_request())
        directory = self.create_temporary_directory()
        changes_path = directory / "foo_1.0-1_amd64.changes"
        self.write_changes_file(changes_path, [])

        with (
            mock.patch.object(
                self.task, "fetch_upload", return_value=[changes_path]
            ),
            mock.patch(
                "debusine.server.tasks.package_upload.FTP",
                side_effect=[TimeoutError(f"Boom {i}") for i in range(3)],
            ),
            self.assertRaisesRegex(TimeoutError, "Boom 2"),
        ):
            self.task.execute()

        assert self.task._debug_log_files_directory is not None
        self.assertEqual(
            Path(
                self.task._debug_log_files_directory.name, "package-upload.log"
            ).read_text(),
            "".join(
                [
                    f"Uploading to {self.task.data.target}\n",
                    "Upload failed: Boom 0\n",
                    "Upload failed: Boom 1\n",
                    "Upload failed: Boom 2\n",
                ]
            ),
        )

    @context.disable_permission_checks()
    def test_execute_sftp(self) -> None:
        """execute() can make an upload using SFTP."""
        queue_directory = self.create_temporary_directory()
        server = FakeSFTPServerProcess()
        home_directory = self.create_temporary_directory()
        (home_directory / ".ssh").mkdir()
        (id_ed25519 := home_directory / ".ssh" / "id_ed25519").write_text(
            server.user_key.private_openssh
        )
        (home_directory / ".ssh" / "config").write_text(
            f"IdentityFile {id_ed25519}\n"
        )
        server.start()
        self.addCleanup(server.stop)
        host, port = server.address
        self.configure_task(
            override={"target": f"sftp://user@{host}:{port}{queue_directory}"}
        )
        self.task.set_work_request(self.playground.create_work_request())
        directory = self.create_temporary_directory()
        deb_path = directory / "foo_1.0-1_amd64.deb"
        changes_path = directory / "foo_1.0-1_amd64.changes"
        self.write_deb_file(deb_path)
        self.write_changes_file(changes_path, [deb_path])

        with (
            mock.patch.object(
                self.task, "fetch_upload", return_value=[deb_path, changes_path]
            ),
            mock.patch.dict(os.environ, {"HOME": str(home_directory)}),
        ):
            self.assertTrue(self.task.execute())

        for path in (deb_path, changes_path):
            self.assertEqual(
                (queue_directory / path.name).read_bytes(), path.read_bytes()
            )

    @context.disable_permission_checks()
    def test_execute_sftp_delayed(self) -> None:
        """execute() can make a delayed upload using SFTP."""
        queue_directory = self.create_temporary_directory()
        (queue_directory / "DELAYED" / "3-day").mkdir(parents=True)
        server = FakeSFTPServerProcess()
        home_directory = self.create_temporary_directory()
        (home_directory / ".ssh").mkdir()
        (id_ed25519 := home_directory / ".ssh" / "id_ed25519").write_text(
            server.user_key.private_openssh
        )
        (home_directory / ".ssh" / "config").write_text(
            f"IdentityFile {id_ed25519}\n"
        )
        server.start()
        self.addCleanup(server.stop)
        host, port = server.address
        self.configure_task(
            override={
                "target": f"sftp://user@{host}:{port}{queue_directory}",
                "delayed_days": 3,
            }
        )
        self.task.set_work_request(self.playground.create_work_request())
        directory = self.create_temporary_directory()
        deb_path = directory / "foo_1.0-1_amd64.deb"
        changes_path = directory / "foo_1.0-1_amd64.changes"
        self.write_deb_file(deb_path)
        self.write_changes_file(changes_path, [deb_path])

        with (
            mock.patch.object(
                self.task, "fetch_upload", return_value=[deb_path, changes_path]
            ),
            mock.patch.dict(os.environ, {"HOME": str(home_directory)}),
        ):
            self.assertTrue(self.task.execute())

        for path in (deb_path, changes_path):
            self.assertEqual(
                (
                    queue_directory / "DELAYED" / "3-day" / path.name
                ).read_bytes(),
                path.read_bytes(),
            )

    def test_execute_sftp_retries_timeouts(self) -> None:
        """execute() retries SFTP upload timeouts."""
        self.configure_task(
            override={"target": "sftp://upload.example.org/queue/"}
        )
        self.task.set_work_request(self.playground.create_work_request())
        directory = self.create_temporary_directory()
        changes_path = directory / "foo_1.0-1_amd64.changes"
        self.write_changes_file(changes_path, [])

        with (
            mock.patch.object(
                self.task, "fetch_upload", return_value=[changes_path]
            ),
            mock.patch(
                "debusine.server.tasks.package_upload.Connection",
                side_effect=[TimeoutError(f"Boom {i}") for i in range(3)],
            ),
            self.assertRaisesRegex(TimeoutError, "Boom 2"),
        ):
            self.task.execute()

        assert self.task._debug_log_files_directory is not None
        self.assertEqual(
            Path(
                self.task._debug_log_files_directory.name, "package-upload.log"
            ).read_text(),
            "".join(
                [
                    f"Uploading to {self.task.data.target}\n",
                    "Upload failed: Boom 0\n",
                    "Upload failed: Boom 1\n",
                    "Upload failed: Boom 2\n",
                ]
            ),
        )

    @context.disable_permission_checks()
    def test_execute_end_to_end(self) -> None:
        """End-to-end execution works."""
        username = "user"
        remote_home_directory = self.create_temporary_directory()
        (remote_home_directory / "queue").mkdir()
        server = FakeFTPServerProcess(username, remote_home_directory)
        server.start()
        self.addCleanup(server.stop)
        host, port = server.address
        self.configure_task(
            override={"target": f"ftp://{username}@{host}:{port}/queue/"}
        )
        self.task.set_work_request(self.playground.create_work_request())
        assert self.task.workspace is not None
        debian_upload_artifact = self.create_source_upload()
        self.task.dynamic_data = PackageUploadDynamicData(
            input_upload_id=debian_upload_artifact.id
        )
        directory = self.create_temporary_directory()
        non_changes_paths = [
            directory / fia.path
            for fia in debian_upload_artifact.fileinartifact_set.all()
            if not fia.path.endswith(".changes")
        ]
        for path in non_changes_paths:
            path.write_text(path.name)
        [changes_path] = [
            directory / fia.path
            for fia in debian_upload_artifact.fileinartifact_set.all()
            if fia.path.endswith(".changes")
        ]
        self.write_changes_file(changes_path, non_changes_paths)

        self.assertTrue(self.task.execute())

        debug_logs_artifact = Artifact.objects.get(
            category=ArtifactCategory.WORK_REQUEST_DEBUG_LOGS,
            created_by_work_request=self.task.work_request,
        )
        package_upload_log = debug_logs_artifact.fileinartifact_set.get()
        self.assertEqual(package_upload_log.path, "package-upload.log")
        fileobj = package_upload_log.file
        file_backend = self.task.workspace.scope.download_file_backend(fileobj)
        with file_backend.get_stream(fileobj) as file:
            self.assertEqual(
                file.read().decode(),
                "".join(
                    [f"Uploading to {self.task.data.target}\n"]
                    + [
                        f"Uploading {path.name}\n"
                        for path in [*non_changes_paths, changes_path]
                    ]
                    + ["Upload succeeded\n"]
                ),
            )

    def test_label(self) -> None:
        """Test get_label."""
        self.assertEqual(
            self.task.get_label(), "upload to ftp://upload.example.org/queue/"
        )

    def test_label_delayed(self) -> None:
        """Test get_label for a delayed upload."""
        self.configure_task(override={"delayed_days": 3})

        self.assertEqual(
            self.task.get_label(),
            "upload to ftp://upload.example.org/queue/DELAYED/3-day",
        )
