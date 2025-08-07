# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for tar related classes."""

import io
import tarfile
from pathlib import Path

from debusine.db.context import context
from debusine.server.tar import TarArtifact
from debusine.test.django import TestCase


class TarArtifactTests(TestCase):
    """Tests for TarArtifact class."""

    playground_memory_file_store = False

    @context.disable_permission_checks()
    def test_tar_small_chunks(self) -> None:
        """Assert that TarArtifact return data in small chunks."""
        file_paths = ["README"]

        artifact, files_contents = self.playground.create_artifact(
            file_paths, create_files=True
        )

        tar_artifact = TarArtifact(artifact=artifact)

        tar_artifact._max_chunk_size = max_chunk_size = 5

        for chunk in tar_artifact:
            self.assertLessEqual(len(chunk), max_chunk_size)

        self.assertTrue(tar_artifact._tar_file.closed)

    @context.disable_permission_checks()
    def test_tar_subdirectory(self) -> None:
        """Test creating a tar for an artifact, only files from a subdir."""
        file_paths = ["README", "doc/README", "doc/README2", "src/main.c"]

        artifact, files_contents = self.playground.create_artifact(
            file_paths, create_files=True
        )

        tar_artifact = TarArtifact(artifact=artifact, subdirectory="doc/")

        result = io.BytesIO(b"".join(tar_artifact))
        result.seek(0)

        tar_artifact_result = tarfile.open(fileobj=result, mode="r:gz")

        self.assertEqual(
            tar_artifact_result.getnames(), ["doc/README", "doc/README2"]
        )

        self.assertTrue(tar_artifact._tar_file.closed)

    @context.disable_permission_checks()
    def test_tar(self) -> None:
        """Test creating a tar for an artifact."""
        file_paths = ["README", "README2", "doc/README3"]

        artifact, files_contents = self.playground.create_artifact(
            file_paths, create_files=True
        )

        tar_artifact = TarArtifact(artifact=artifact)

        result = io.BytesIO()

        # Verify that TarArtifact generate multiple chunks (at least
        # one per file) and that the file in disk decreased it size
        # (because the returned data is removed from the file)
        loops = 0

        for chunk in tar_artifact:
            result.write(chunk)
            loops += 1

        # Assert that TarArtifact generated at least as many
        # chunks as number of files
        self.assertGreaterEqual(loops, len(file_paths))

        result.seek(0)

        tar_file = tarfile.open(fileobj=result, mode="r:gz")

        # Assert file's contents are correct
        for path in file_paths:
            file = tar_file.extractfile(path)
            assert file is not None
            self.assertEqual(file.read(), files_contents[path])

        # Assert file names are ordered
        self.assertEqual(
            tar_file.getnames(),
            list(
                artifact.fileinartifact_set.all()
                .values_list("path", flat=True)
                .order_by("path")
            ),
        )

        # Assert mtime of each file is artifact.created_at
        for member in tar_file.getmembers():
            self.assertEqual(member.mtime, artifact.created_at.timestamp())

        # Assert TarArtifact deleted the temporary directory when it finished
        # iterating
        self.assertFalse(Path(tar_artifact._temp_directory).exists())

        self.assertTrue(tar_artifact._tar_file.closed)

    @context.disable_permission_checks()
    def test_tar_excludes_incomplete(self) -> None:
        """Creating a tar for an artifact excludes incomplete files."""
        file_paths = ["README", "README2", "doc/README3"]

        artifact, files_contents = self.playground.create_artifact(
            file_paths, create_files=True
        )
        artifact.fileinartifact_set.filter(path="README").update(complete=False)

        tar_artifact = TarArtifact(artifact=artifact)

        result = io.BytesIO(b"".join(tar_artifact))
        result.seek(0)

        tar_artifact_result = tarfile.open(fileobj=result, mode="r:gz")

        self.assertCountEqual(
            tar_artifact_result.getnames(), ["README2", "doc/README3"]
        )

        self.assertTrue(tar_artifact._tar_file.closed)

    @context.disable_permission_checks()
    def test_next_raise_stop_iteration_file_not_found(self) -> None:
        """Cannot read a file: exc logged, StopIteration raised."""
        file_paths = ["README"]

        artifact, files_contents = self.playground.create_artifact(
            file_paths, create_files=True
        )

        fileobj = artifact.fileinartifact_set.all()[0].file
        file_backend = artifact.workspace.scope.download_file_backend(fileobj)

        entry = file_backend.get_entry(fileobj)
        entry.remove()

        tar_artifact = TarArtifact(artifact=artifact)

        with (
            self.assertLogsContains(
                str(entry.get_local_path()), expected_count=2
            ),
            self.assertRaises(StopIteration),
        ):
            tar_artifact.__next__()

        self.assertTrue(tar_artifact._tar_file.closed)
