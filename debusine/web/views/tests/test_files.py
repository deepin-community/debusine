# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the artifact views."""

import json
import os.path
import unittest
from typing import ClassVar, NamedTuple, cast
from unittest import mock

from django.http.request import HttpHeaders
from django.http.response import HttpResponseBase, HttpResponseRedirectBase
from django.template import engines
from django.test import override_settings
from django.utils.datastructures import CaseInsensitiveMapping
from rest_framework import status

from debusine.db.context import context
from debusine.db.models import File, FileInArtifact
from debusine.server.views import ProblemResponse
from debusine.test.django import TestCase
from debusine.web.views import files
from debusine.web.views.files import (
    FileDownloadMixin,
    FileUI,
    FileWidget,
    MAX_FILE_SIZE,
    PathMixin,
)
from debusine.web.views.tests.utils import ViewTestMixin


class FileUITests(TestCase):
    """Tests for the FileUI class."""

    def assertRenders(
        self, file_ui: FileUI, file_in_artifact: FileInArtifact
    ) -> None:
        """Test that the given widget renders without errors."""
        template = engines["django"].from_string(
            "{% load debusine %}{% widget widget %}"
        )
        setattr(template, "engine", engines["django"])
        widget = file_ui.widget_class(file_in_artifact, file_ui=file_ui)
        # Set DEBUG=True to have render raise exceptions instead of logging
        # them
        with override_settings(DEBUG=True):
            template.render({"widget": widget})

    def test_from_file_in_artifact(self) -> None:
        """Test from_file_in_artifact."""

        class SubTest(NamedTuple):
            """Define a subtest."""

            filename: str
            widget_class: type[FileWidget]
            content_type: str
            size: int = 1024

        sub_tests = [
            SubTest(
                "build.changes",
                files.TextFileWidget,
                "text/plain; charset=utf-8",
            ),
            SubTest(
                "file.log", files.TextFileWidget, "text/plain; charset=utf-8"
            ),
            SubTest(
                "file.txt", files.TextFileWidget, "text/plain; charset=utf-8"
            ),
            SubTest(
                "hello.build", files.TextFileWidget, "text/plain; charset=utf-8"
            ),
            SubTest(
                "hello.buildinfo",
                files.TextFileWidget,
                "text/plain; charset=utf-8",
            ),
            SubTest(
                "file.sources",
                files.TextFileWidget,
                "text/plain; charset=utf-8",
            ),
            SubTest(
                "readme.md",
                files.TextFileWidget,
                "text/markdown; charset=utf-8",
            ),
            SubTest(
                "a.out", files.BinaryFileWidget, "application/octet-stream"
            ),
            SubTest(
                "big.bin",
                files.TooBigFileWidget,
                "application/octet-stream",
                2**32,
            ),
        ]

        for sub_test in sub_tests:
            with self.subTest(sub_test.filename):
                with context.disable_permission_checks():
                    artifact, _ = self.playground.create_artifact(
                        paths={sub_test.filename: b"test"}, create_files=True
                    )
                file_in_artifact = FileInArtifact.objects.get(artifact=artifact)
                file_in_artifact.file.size = sub_test.size
                fileui = FileUI.from_file_in_artifact(file_in_artifact)
                self.assertEqual(fileui.content_type, sub_test.content_type)
                self.assertEqual(fileui.widget_class, sub_test.widget_class)
                self.assertRenders(fileui, file_in_artifact)

    def test_compressed(self) -> None:
        """Test from_file_in_artifact with a compressed file."""
        file_in_artifact = FileInArtifact(path="file.md.gz")
        file_in_artifact.file = File(size=123)
        fileui = FileUI.from_file_in_artifact(file_in_artifact)
        self.assertEqual(fileui.content_type, "application/gzip")
        self.assertEqual(fileui.widget_class, files.BinaryFileWidget)


class PathMixinTests(ViewTestMixin, unittest.TestCase):
    """Tests for the ArtifactDetailView class."""

    def test_normalize_path(self) -> None:
        """Test PathMixin.normalize_path."""
        f = PathMixin.normalize_path
        self.assertEqual(f(""), "/")
        self.assertEqual(f("/"), "/")
        self.assertEqual(f("."), "/")
        self.assertEqual(f(".."), "/")
        self.assertEqual(f("../"), "/")
        self.assertEqual(f("../../.././../../"), "/")
        self.assertEqual(f("src/"), "/src/")
        self.assertEqual(f("src/.."), "/")
        self.assertEqual(f("/a/b/../c/./d//e/f/../g/"), "/a/c/d/e/g/")

    def test_path(self) -> None:
        """Test PathMixin.path."""
        view = self.instantiate_view_class(PathMixin, "/")
        self.assertEqual(view.path, "")

        view = self.instantiate_view_class(
            PathMixin, "/", path="/foo/bar/../baz"
        )
        self.assertEqual(view.path, "foo/baz/")


class FileDownloadMixinTests(ViewTestMixin, TestCase):
    """Test FileDownloadMixin."""

    playground_memory_file_store = False

    contents: ClassVar[dict[str, bytes]]
    empty: ClassVar[FileInArtifact]
    file: ClassVar[FileInArtifact]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up the common test fixture."""
        super().setUpTestData()
        cls.contents = {
            "empty.bin": b"",
            "file.md": bytes(range(256)),
        }
        artifact, _ = cls.playground.create_artifact(
            paths=cls.contents,
            create_files=True,
        )
        cls.empty = artifact.fileinartifact_set.get(path="empty.bin")
        cls.file = artifact.fileinartifact_set.get(path="file.md")

    def get_stream_response(
        self,
        file_in_artifact: FileInArtifact,
        range_header: tuple[int, int] | str | None = None,
        download: bool = True,
    ) -> HttpResponseBase:
        """Instantiate the view and get a streaming file response."""
        request = self.make_request("/")
        headers = {}
        match range_header:
            case str():
                headers["Range"] = range_header
            case [range_start, range_end]:
                headers["Range"] = f"bytes={range_start}-{range_end}"
        if headers:
            request.headers = cast(
                HttpHeaders,
                CaseInsensitiveMapping({**request.headers, **headers}),
            )

        ui_info = FileUI.from_file_in_artifact(file_in_artifact)
        view = self.instantiate_view_class(FileDownloadMixin, request)
        response = view.stream_file(file_in_artifact, ui_info, download)
        if isinstance(response, ProblemResponse):
            # This is needed by assertResponseProblem
            setattr(
                response,
                "json",
                lambda: json.loads(response.content.decode(response.charset)),
            )

        return response

    def assertFileResponse(
        self,
        response: HttpResponseBase,
        file_in_artifact: FileInArtifact,
        range_start: int = 0,
        range_end: int | None = None,
        disposition: str = "attachment",
    ) -> None:
        """Assert that response has the expected headers and content."""
        if range_start == 0 and range_end is None:
            self.assertEqual(response.status_code, status.HTTP_200_OK)
        else:
            self.assertEqual(
                response.status_code, status.HTTP_206_PARTIAL_CONTENT
            )

        file_contents = self.contents[file_in_artifact.path]
        if range_end is None:
            range_end = len(file_contents) - 1
        expected_contents = file_contents[range_start : range_end + 1]

        headers = response.headers
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(headers["Content-Length"], str(len(expected_contents)))

        if len(expected_contents) > 0:
            self.assertEqual(
                headers["Content-Range"],
                f"bytes {range_start}-{range_end}/{len(file_contents)}",
            )

        filename = os.path.basename(file_in_artifact.path)
        self.assertEqual(
            headers["Content-Disposition"],
            f'{disposition}; filename="{filename}"',
        )

        streaming_content = getattr(response, "streaming_content", None)
        assert streaming_content is not None
        self.assertEqual(b"".join(streaming_content), expected_contents)

    def test_get_file(self) -> None:
        """Get return the file."""
        response = self.get_stream_response(self.file)
        self.assertFileResponse(response, self.file)
        self.assertEqual(
            response.headers["content-type"], "text/markdown; charset=utf-8"
        )

    def test_get_file_inline(self) -> None:
        """Get return the file."""
        response = self.get_stream_response(self.file, download=False)
        self.assertFileResponse(response, self.file, disposition="inline")
        self.assertEqual(
            response.headers["content-type"], "text/markdown; charset=utf-8"
        )

    def test_get_empty_file(self) -> None:
        """Test empty downloadable file (which mmap doesn't support)."""
        response = self.get_stream_response(self.empty)
        self.assertFileResponse(response, self.empty)
        self.assertEqual(
            response.headers["content-type"],
            "application/octet-stream",
        )

    def test_get_incomplete_file(self) -> None:
        """Get returns 404 for an incomplete file."""
        self.file.complete = False
        self.file.save()
        response = self.get_stream_response(self.file)
        self.assertResponseProblem(
            response,
            "Cannot download incomplete file",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_file_range(self) -> None:
        """Get return part of the file (based on Range header)."""
        start, end = 10, 20
        response = self.get_stream_response(
            self.file, range_header=(start, end)
        )
        self.assertFileResponse(response, self.file, start, end)

    def test_get_file_content_range_to_end_of_file(self) -> None:
        """Server returns a file from a position to the end."""
        start, end = 5, len(self.contents["file.md"]) - 1
        response = self.get_stream_response(
            self.file, range_header=(start, end)
        )
        self.assertFileResponse(response, self.file, start, end)

    def test_get_file_content_range_invalid(self) -> None:
        """Get return an error: Range header was invalid."""
        invalid_range_header = "invalid-range"
        response = self.get_stream_response(
            self.file, range_header=invalid_range_header
        )
        self.assertResponseProblem(
            response, f'Invalid Range header: "{invalid_range_header}"'
        )

    def test_get_file_range_start_greater_file_size(self) -> None:
        """Get return 400: client requested an invalid start position."""
        file_size = len(self.contents["file.md"])
        start, end = file_size + 10, file_size + 20
        response = self.get_stream_response(
            self.file, range_header=(start, end)
        )
        self.assertResponseProblem(
            response,
            f"Invalid Content-Range start: {start}. " f"File size: {file_size}",
        )

    def test_get_file_range_end_is_file_size(self) -> None:
        """Get return 400: client requested and invalid end position."""
        end = len(self.contents["file.md"])
        response = self.get_stream_response(self.file, range_header=(0, end))
        self.assertResponseProblem(
            response,
            f"Invalid Content-Range end: {end}. File size: {end}",
        )

    def test_get_file_range_end_greater_file_size(self) -> None:
        """Get return 400: client requested an invalid end position."""
        file_size = len(self.contents["file.md"])
        end = file_size + 10
        response = self.get_stream_response(self.file, range_header=(0, end))
        self.assertResponseProblem(
            response,
            f"Invalid Content-Range end: {end}. " f"File size: {file_size}",
        )

    def test_get_file_url_redirect(self) -> None:
        """
        Get file response: redirect if get_url for the file is available.

        This would happen if the file is stored in a FileStore supporting
        get_url (e.g. an object storage) instead of being served from the
        server's file system.
        """
        destination_url = "https://some-backend.net/file?token=asdf"

        with mock.patch(
            "debusine.server.file_backend.local.LocalFileBackendEntry.get_url",
            autospec=True,
            return_value=destination_url,
        ) as mock_get_url:
            response = self.get_stream_response(self.file)
            self.assertEqual(response.status_code, status.HTTP_302_FOUND)
            assert isinstance(response, HttpResponseRedirectBase)
            self.assertEqual(response.url, destination_url)
            ui_info = FileUI.from_file_in_artifact(self.file)
            mock_get_url.assert_called_once_with(
                mock.ANY, content_type=ui_info.content_type
            )


class FileWidgetTests(ViewTestMixin, TestCase):
    """Test FileView."""

    contents: ClassVar[dict[str, bytes]]
    binary: ClassVar[FileInArtifact]
    dsc: ClassVar[FileInArtifact]
    empty: ClassVar[FileInArtifact]
    large: ClassVar[FileInArtifact]
    text: ClassVar[FileInArtifact]

    @classmethod
    @context.disable_permission_checks()
    def setUpTestData(cls) -> None:
        """Set up the common test fixture."""
        super().setUpTestData()
        cls.contents = {
            "file.md": b"# Title\nText",
            "file.dsc": b"Source: hello",
            "empty.bin": b"",
            "file.bin": bytes(range(256)),
            "largefile.bin": b"large",
        }
        artifact, _ = cls.playground.create_artifact(
            paths=cls.contents,
            create_files=True,
        )
        cls.empty = artifact.fileinartifact_set.get(path="empty.bin")
        cls.dsc = artifact.fileinartifact_set.get(path="file.dsc")
        cls.text = artifact.fileinartifact_set.get(path="file.md")
        cls.binary = artifact.fileinartifact_set.get(path="file.bin")
        cls.large = artifact.fileinartifact_set.get(path="largefile.bin")
        cls.large.file.size = 2**32
        cls.large.file.save()

    def test_context_data_text(self) -> None:
        """Test context for text files."""
        file = self.text
        context = FileWidget.create(file).get_context_data()

        file_linenumbers = context.pop("file_linenumbers")
        self.assertIn("L1", file_linenumbers)
        file_content = context.pop("file_content")
        self.assertIn("# Title", file_content)

        self.assertEqual(
            context,
            {
                "file_in_artifact": file,
                "file_ui": FileUI.from_file_in_artifact(file),
                "file_contents_div_id": "file-contents",
            },
        )

    def test_context_data_text_incomplete(self) -> None:
        """Test context for incomplete text files."""
        file = self.text
        file.complete = False
        file.save()
        context = FileWidget.create(file).get_context_data()

        self.assertNotIn("file_linenumbers", context)
        self.assertNotIn("file_content", context)

        self.assertEqual(
            context,
            {
                "file_in_artifact": file,
                "file_ui": FileUI.from_file_in_artifact(file),
                "file_contents_div_id": "file-contents",
            },
        )

    def test_context_data_dsc(self) -> None:
        """Test context for text files."""
        file = self.dsc
        context = FileWidget.create(file).get_context_data()

        file_linenumbers = context.pop("file_linenumbers")
        self.assertIn("L1", file_linenumbers)
        file_content = context.pop("file_content")
        self.assertIn('<span class="k">Source</span>', file_content)

        self.assertEqual(
            context,
            {
                "file_in_artifact": file,
                "file_ui": FileUI.from_file_in_artifact(file),
                "file_contents_div_id": "file-contents",
            },
        )

    def test_context_data_empty(self) -> None:
        """Test context for empty binary files."""
        file = self.empty
        context = FileWidget.create(file).get_context_data()
        self.assertEqual(
            context,
            {
                "file_in_artifact": file,
                "file_ui": FileUI.from_file_in_artifact(file),
            },
        )

    def test_context_data_binary(self) -> None:
        """Test context for binary files."""
        file = self.binary
        context = FileWidget.create(file).get_context_data()
        self.assertEqual(
            context,
            {
                "file_in_artifact": file,
                "file_ui": FileUI.from_file_in_artifact(file),
            },
        )

    def test_context_data_large(self) -> None:
        """Test context for files too large."""
        file = self.large
        context = FileWidget.create(file).get_context_data()
        self.assertEqual(
            context,
            {
                "file_in_artifact": file,
                "file_ui": FileUI.from_file_in_artifact(file),
                "file_max_size": MAX_FILE_SIZE,
            },
        )
