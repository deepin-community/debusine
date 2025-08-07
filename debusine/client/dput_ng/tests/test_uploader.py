# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the Debusine dput-ng uploader."""

import logging
from unittest import mock

import debian.deb822 as deb822
from dput import upload
from dput.exceptions import UploadException

from debusine.artifacts.models import ArtifactCategory
from debusine.artifacts.playground import ArtifactPlayground
from debusine.client.dput_ng.tests.test_utils import (
    ignore_dput_ng_warnings,
    mock_client_config,
    use_local_dput_ng_config,
)
from debusine.client.exceptions import DebusineError
from debusine.client.models import RelationType
from debusine.test import TestCase
from debusine.test.test_utils import create_artifact_response


class TestDebusineUploader(TestCase):
    """Test the uploader."""

    def test_success(self) -> None:
        """Create debusine artifacts for an upload."""
        upload_path = self.create_temporary_directory()
        changes_path = ArtifactPlayground.create_upload(
            upload_path,
            source=True,
            src_name="hello",
            version="1.0-1",
            binary=False,
        ).files["hello_1.0-1_source.changes"]
        base_path = changes_path.parent
        names = [
            "hello_1.0-1.dsc",
            "hello_1.0-1.debian.tar.xz",
            "hello_1.0.orig.tar.gz",
            "hello_1.0-1_source.changes",
        ]

        with (
            use_local_dput_ng_config(include_core=True),
            ignore_dput_ng_warnings(),
            mock.patch("dput.hook.run_hook") as mock_run_hook,
            mock_client_config(
                "https://debusine.example.net/api", "some-token"
            ),
            mock.patch(
                "debusine.client.debusine.Debusine.artifact_create",
                side_effect=[
                    create_artifact_response(
                        id=artifact_id, files_to_upload=files_to_upload
                    )
                    for artifact_id, files_to_upload in ((1, names), (2, []))
                ],
            ) as mock_artifact_create,
            mock.patch(
                "debusine.client.file_uploader.FileUploader._upload_file",
                return_value=True,
            ) as mock_upload_file,
            mock.patch(
                "debusine.client.debusine.Debusine.relation_create"
            ) as mock_relation_create,
            self.assertLogs(logger="dput", level=logging.INFO) as log,
        ):
            upload(str(changes_path), "debusine.debian.net", no_upload_log=True)

        mock_run_hook.assert_has_calls(
            [
                mock.call(hook_name, mock.ANY, mock.ANY, mock.ANY)
                for hook_name in (
                    "debusine-check-workflow",
                    "checksum",
                    "suite-mismatch",
                    "gpg",
                    "debusine-create-workflow",
                )
            ]
        )
        self.assertEqual(
            [
                (args[0][0].category, args[1])
                for args in mock_artifact_create.call_args_list
            ],
            [
                (
                    ArtifactCategory.UPLOAD,
                    {
                        "workspace": "developers",
                        "work_request": None,
                        "expire_at": None,
                    },
                ),
                (
                    ArtifactCategory.SOURCE_PACKAGE,
                    {
                        "workspace": "developers",
                        "work_request": None,
                        "expire_at": None,
                    },
                ),
            ],
        )
        self.assertEqual(mock_upload_file.call_count, 4)
        mock_relation_create.assert_has_calls(
            [
                mock.call(1, 2, RelationType.EXTENDS),
                mock.call(1, 2, RelationType.RELATES_TO),
            ]
        )
        self.assertEqual(
            log.output,
            [
                "INFO:dput:Uploading hello using debusine to "
                "debusine.debian.net "
                "(host: debusine.debian.net; directory: /)",
                "INFO:dput:Not writing upload log upon request",
            ]
            + [f"INFO:dput:Uploading {name}" for name in names]
            + [
                f"INFO:dput:Uploading {base_path}/{name} to "
                f"https://debusine.example.net/api/1.0/artifact/1/files/{name}/"
                for name in names
            ]
            + [
                "INFO:dput:Created artifact: "
                "https://debusine.example.net/debian/developers/artifact/1/",
            ],
        )

    def test_multiple_changes_files(self) -> None:
        """Attempting to upload multiple .changes files is an error."""
        upload_path = self.create_temporary_directory()
        ArtifactPlayground.write_deb822_file(
            deb822.Changes,
            (inner_changes := upload_path / "inner_1_source.changes"),
            [],
            source="inner",
            version="1",
        )
        ArtifactPlayground.write_deb822_file(
            deb822.Changes,
            (outer_changes := upload_path / "outer_1_source.changes"),
            [inner_changes],
            source="outer",
            version="1",
        )

        with (
            use_local_dput_ng_config(include_core=True),
            ignore_dput_ng_warnings(),
            mock.patch("dput.hook.run_hook"),
            mock_client_config(
                "https://debusine.example.net/api", "some-token"
            ),
            mock.patch(
                "debusine.client.debusine.Debusine.upload_artifact",
                return_value=create_artifact_response(id=1),
            ),
            mock.patch("debusine.client.debusine.Debusine.relation_create"),
            self.assertLogs(logger="dput", level=logging.INFO) as log,
            self.assertRaisesRegex(
                RuntimeError, "Cannot upload multiple .changes files"
            ),
        ):
            upload(
                str(outer_changes), "debusine.debian.net", no_upload_log=True
            )

        self.assertEqual(
            log.output,
            [
                "INFO:dput:Uploading outer using debusine to "
                "debusine.debian.net "
                "(host: debusine.debian.net; directory: /)",
                "INFO:dput:Not writing upload log upon request",
                "INFO:dput:Uploading inner_1_source.changes",
                "INFO:dput:Created artifact: "
                "https://debusine.example.net/debian/developers/artifact/1/",
                "INFO:dput:Uploading outer_1_source.changes",
            ],
        )

    def test_upload_artifact_error(self) -> None:
        """
        Exceptions from ``upload_artifact`` are wrapped in an UploadException.

        This is handled cleanly by the ``dput`` command line.
        """
        upload_path = self.create_temporary_directory()
        changes_path = ArtifactPlayground.create_upload(
            upload_path,
            source=True,
            src_name="hello",
            version="1.0-1",
            binary=False,
        ).files["hello_1.0-1_source.changes"]
        error = DebusineError(
            {
                "title": "Something went wrong",
                "detail": "Something went badly wrong",
            }
        )

        with (
            use_local_dput_ng_config(include_core=True),
            ignore_dput_ng_warnings(),
            mock.patch("dput.hook.run_hook"),
            mock_client_config(
                "https://debusine.example.net/api", "some-token"
            ),
            mock.patch(
                "debusine.client.debusine.Debusine.upload_artifact",
                side_effect=error,
            ),
            self.assertLogs(logger="dput", level=logging.INFO),
            self.assertRaises(UploadException) as raised,
        ):
            upload(str(changes_path), "debusine.debian.net", no_upload_log=True)

        self.assertIsInstance(raised.exception, UploadException)
        self.assertEqual(str(raised.exception), str(error))
        self.assertEqual(raised.exception.__cause__, error)
