# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the FileUploader class."""

import io
import logging
import mmap
from collections.abc import Callable
from typing import Any
from unittest import mock

import requests
import responses

from debusine.client import exceptions
from debusine.client.file_uploader import (
    FileUploadError,
    FileUploader,
    UnexpectedStatusCodeError,
)
from debusine.test import TestCase
from debusine.utils import parse_range_header


def body_matcher(
    expected_body: bytes | None,
) -> Callable[..., tuple[bool, str]]:  # pragma: no cover
    """Matcher for the body's content of a request. Used by responses.add."""

    def match(request: requests.models.PreparedRequest) -> tuple[bool, str]:
        """Return True if request_body matches expected_body."""
        if isinstance(request.body, mmap.mmap):
            position = request.body.tell()
            contents = request.body.read()
            request.body.seek(position)
        else:
            contents = request.body

        matched = expected_body == contents

        if matched:
            reason = "Bodies matched"
        else:
            reason = "Bodies are different"

        return matched, reason

    return match


class UnexpectedStatusCodeErrorTests(TestCase):
    """Tests for UnexpectedStatusCodeError."""

    def setUp(self) -> None:
        """Set up object with fake response."""
        mocked_request = mock.create_autospec(spec=requests.models.Request)
        mocked_request.method = "PUT"
        mocked_request.headers = {}
        mocked_request.url = "http://localhost:8088/test"

        self.mocked_response = mock.create_autospec(
            spec=requests.models.Response
        )
        self.mocked_response.request = mocked_request
        self.mocked_response.status_code = requests.codes.bad_request
        self.mocked_response.text = "Error from the server"

    def test_str_without_content_range(self) -> None:
        """Test for the human-readable string: content-range was not sent."""
        status_code_error = UnexpectedStatusCodeError(self.mocked_response)

        mocked_response = self.mocked_response
        mocked_request = self.mocked_response.request

        self.assertEqual(
            str(status_code_error),
            f"Client HTTP {mocked_request.method} {mocked_request.url} "
            f"Received unexpected status code: {mocked_response.status_code} "
            f"Response body:\n{mocked_response.text}",
        )

    def test_str_with_content_range(self) -> None:
        """Test for the human-readable string: content-range was in headers."""
        self.mocked_response.request.headers = {"Content-Range": "*/4"}

        status_code_error = UnexpectedStatusCodeError(self.mocked_response)

        mocked_response = self.mocked_response
        mocked_request = self.mocked_response.request

        self.assertEqual(
            str(status_code_error),
            f"Client HTTP {mocked_request.method} {mocked_request.url} "
            f"with Content-Range {mocked_request.headers['Content-Range']} "
            f"Received unexpected status code: {mocked_response.status_code} "
            f"Response body:\n{mocked_response.text}",
        )


class FileUploaderTests(TestCase):
    """Tests for the debusine command line interface."""

    def setUp(self) -> None:
        """Initialize test."""
        self.url = "https://localhost:8011/1.0/artifact/2/README.md"
        self.token = "21efb7587bd10b8eb2c56b4280b4c4a92a9"

        self.file_uploader = FileUploader(
            self.token, logger=logging.getLogger(__name__)
        )

        self.file_content = b"test-content"
        self.file_path = self.create_temporary_file(contents=self.file_content)

    def add_partial_response(self, status: int, response_range: str) -> None:
        """
        Add response: for PUT in self.url with empty body.

        :param status: response's status code.
        :param response_range: response's "Range" header.
        """
        responses.add(
            responses.PUT,
            self.url,
            status=status,
            adding_headers={"Range": f"bytes={response_range}"},
            match=[body_matcher(None)],
        )

    def setup_partial_file_uploaded(
        self,
        /,
        uploaded_size: int,
        status_code_file_status: int = requests.codes.partial_content,
        status_code_upload: int = requests.codes.created,
    ) -> str:
        """Set up responses for a file partially uploaded."""
        file_size = len(self.file_content)

        self.add_partial_response(
            status_code_file_status, f"0-{uploaded_size}/{file_size}"
        )

        responses.add(
            responses.PUT,
            self.url,
            status=status_code_upload,
            match=[
                body_matcher(self.file_content[uploaded_size:file_size]),
            ],
        )

        # Missing part to upload
        content_range = f"bytes {uploaded_size}-{file_size - 1}/{file_size}"
        return content_range

    @responses.activate
    def test_file_uploader_check_file_was_completed(self) -> None:
        """
        Client sends PUT Content-Range: */file_size. Server return HTTP 200.

        The client does not upload anything.
        """  # noqa: RST213
        responses.add(responses.PUT, self.url)
        self.file_uploader.upload(self.file_path, self.url)

        file_size = len(self.file_content)

        request = responses.calls[0].request

        # The client requested to know the missing part of the file
        self.assertEqual(
            request.headers["Content-Range"], f"bytes */{file_size}"
        )

        # The response was HTTP 200 (requests.codes.ok) so no more HTTP
        # requests: nothing to do
        self.assertEqual(len(responses.calls), 1)

        # Nothing was uploaded to the server
        self.assertIsNone(request.body)

    @responses.activate
    def test_file_uploader_uploads_part_of_file(self) -> None:
        """Client check and need to upload part of a file."""
        content_range_to_upload = self.setup_partial_file_uploaded(
            uploaded_size=2
        )
        self.file_uploader.upload(self.file_path, self.url)

        # Client checked if the file is already upload, the server
        # returned partial-content and the client uploads the missing part
        self.assertEqual(len(responses.calls), 2)

        check_range_to_upload_headers = responses.calls[0].request.headers
        self.assertEqual(
            check_range_to_upload_headers["Content-Range"],
            f"bytes */{len(self.file_content)}",
        )

        file_upload_headers = responses.calls[1].request.headers
        self.assertEqual(
            file_upload_headers["Content-Range"], content_range_to_upload
        )

    @responses.activate
    def test_file_upload_file_status_raise_unexpected_status(self) -> None:
        """
        Client check status of the file, server return unexpected code.

        UnexpectedStatusCodeError is raised.
        """
        self.setup_partial_file_uploaded(
            uploaded_size=2, status_code_file_status=requests.codes.teapot
        )

        with self.assertRaises(UnexpectedStatusCodeError):
            self.file_uploader.upload(self.file_path, self.url)

    @responses.activate
    def test_file_uploader_uploads_part_of_file_unexpected_status(self) -> None:
        """
        Client try to upload a file, server return unexpected code.

        UnexpectedStatusCodeError is raised.
        """
        self.setup_partial_file_uploaded(
            uploaded_size=2, status_code_upload=requests.codes.teapot
        )

        with self.assertRaises(UnexpectedStatusCodeError):
            self.file_uploader.upload(self.file_path, self.url)

    @responses.activate
    def test_file_upload_conflict_uploading_file(self) -> None:
        """Client upload file, there is a conflict and uploads it again."""

        def succeeds_after_one_attempt(
            request: requests.PreparedRequest,  # noqa: U100
        ) -> tuple[int, dict[str, str], None]:
            first_attempt = getattr(succeeds_after_one_attempt, "first", True)

            status_code: int
            if first_attempt:
                status_code = requests.codes.conflict
            else:
                status_code = requests.codes.ok

            setattr(succeeds_after_one_attempt, "first", False)

            return status_code, {}, None

        uploaded_size = 2
        file_size = len(self.file_content)

        # Missing part to upload
        response_range = f"{uploaded_size}-{file_size}"

        self.add_partial_response(
            requests.codes.partial_content, response_range
        )

        responses.add_callback(
            responses.PUT, self.url, callback=succeeds_after_one_attempt
        )

        self.file_uploader.upload(self.file_path, self.url)

    def add_response(
        self, start_position: int, status: int, /, skip_range: bool = False
    ) -> int:
        """Add a response matching self.file_content (size=chunk size)."""
        end_position = start_position + self.file_uploader._MAX_CHUNK_SIZE_BYTES

        if skip_range:
            headers = {}
        else:
            headers = {"Range": f"bytes={start_position}-{end_position}"}

        responses.add(
            responses.PUT,
            self.url,
            status=status,
            adding_headers=headers,
            match=[
                body_matcher(self.file_content[start_position:end_position]),
            ],
        )
        return min(end_position, len(self.file_content))

    @responses.activate
    def test_upload_server_does_not_include_content_range_response(
        self,
    ) -> None:
        """
        Client upload a chunk, server does not include range header.

        FileUploader.upload raise RuntimeError.
        """
        self.file_uploader._MAX_CHUNK_SIZE_BYTES = 5

        self.add_partial_response(requests.codes.partial_content, "0-0")

        self.add_response(0, requests.codes.ok, skip_range=True)

        with self.assertRaises(RuntimeError):
            self.file_uploader.upload(self.file_path, self.url)

    @responses.activate
    def test_upload_file_in_chunks(self) -> None:
        """Client upload the file in chunks."""
        self.file_uploader._MAX_CHUNK_SIZE_BYTES = 5

        # Nothing has been uploaded yet: from 0 to 0 uploaded
        self.add_partial_response(requests.codes.partial_content, "0-0")

        end_position = self.add_response(0, requests.codes.ok)
        end_position = self.add_response(end_position, requests.codes.ok)
        self.add_response(end_position, requests.codes.created)

        self.file_uploader.upload(self.file_path, self.url)

    def test_parse_range_or_raise_exception_return_range(self) -> None:
        """Return the range (range is in the header and well formatted)."""
        response = mock.create_autospec(
            spec=requests.models.Response, headers={"Range": "bytes=0-20/20"}
        )

        self.assertEqual(
            FileUploader._parse_range_or_raise_exception(response),
            parse_range_header(response.headers),
        )

    def test_parse_range_or_raise_exception_exception_no_header(self) -> None:
        """Method raise exception: expected header not found."""
        method = "PUT"
        url = "https://test.com"
        request = mock.create_autospec(
            spec=requests.models.Request, method=method, url=url
        )
        response = mock.create_autospec(
            spec=requests.models.Response, request=request, headers={}
        )

        with self.assertRaisesRegex(
            RuntimeError,
            f"^Required range header not in response or invalid for: {method} "
            f"{url} content-range header: None$",
        ):
            FileUploader._parse_range_or_raise_exception(response)

    def test_parse_range_or_raise_exception_exception_invalid_header(
        self,
    ) -> None:
        """Method raise exception: header "range" is not valid."""
        response = mock.create_autospec(
            spec=requests.models.Response,
            request=mock.create_autospec(
                spec=requests.models.Request,
                url="https://example.com",
                method="GET",
            ),
            headers={"Range": "invalid-range"},
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "^Required range header not in response or invalid for: ",
        ):
            FileUploader._parse_range_or_raise_exception(response)

    @responses.activate
    def test_file_upload_server_does_not_return_range(self) -> None:
        """
        Client check status of the file, server return invalid response.

        Server returned "partial content" status code without the Range header.
        """
        responses.add(
            responses.PUT,
            self.url,
            status=requests.codes.partial_content,
            match=[body_matcher(None)],
        )
        with self.assertRaises(RuntimeError):
            self.file_uploader.upload(self.file_path, self.url)

    @responses.activate
    def test_file_upload_conflict_always_conflict(self) -> None:
        """Client upload file, there is always a conflict. Raise exception."""
        self.setup_partial_file_uploaded(
            uploaded_size=2, status_code_upload=requests.codes.conflict
        )

        error_msg = r"Error uploading .* to {} \({} retries\)".format(
            self.url,
            self.file_uploader._MAX_FILE_UPLOAD_RETRIES,
        )
        with self.assertRaisesRegex(FileUploadError, error_msg):
            self.file_uploader.upload(self.file_path, self.url)

        url_called_times = self.file_uploader._MAX_FILE_UPLOAD_RETRIES

        # two calls per attempt: to check what needs
        # to be uploaded (server return the range and
        # HTTP 206) and the data upload
        url_called_times *= 2

        responses.assert_call_count(self.url, url_called_times)

    @responses.activate
    def test_file_upload_logs(self) -> None:
        """The uploader logs a message."""
        responses.add(responses.PUT, self.url)
        with self.assertLogsContains(
            f"Uploading {self.file_path} to {self.url}",
            logger=__name__,
            level=logging.INFO,
        ):
            self.file_uploader.upload(self.file_path, self.url)

    def test_upload_file_put_fails(self) -> None:
        """FileUpload._upload_file() requests.put() raise exception."""
        request = mock.create_autospec(
            spec=requests.models.Request, url="https://test.com"
        )

        self.patch_requests_put(
            requests.exceptions.RequestException(request=request)
        )

        with (
            self.assertRaises(exceptions.ClientConnectionError),
            open(self.file_path, "rb") as file,
        ):
            self.file_uploader._upload_file(
                file, len(self.file_content), "https://test.com"
            )

    def patch_sleep(self) -> mock.MagicMock:
        """Patch time.sleep(), return its mock."""
        patcher = mock.patch("time.sleep", autospec=True)
        mocked_sleep = patcher.start()

        self.addCleanup(patcher.stop)

        return mocked_sleep

    def patch_requests_put(self, side_effect: Any) -> mock.MagicMock:
        """Patch requests.put(), return its mock."""
        patcher = mock.patch("requests.put", autospec=True)
        mocked_put = patcher.start()
        mocked_put.side_effect = side_effect

        self.addCleanup(patcher.stop)

        return mocked_put

    def test_upload_chunk_retries(self) -> None:
        """FileUploader._upload_chunk() called twice: once failed."""
        mocked_sleep = self.patch_sleep()

        return_value = "request_result"
        request = mock.create_autospec(
            spec=requests.models.Request, url="https://test.com"
        )
        mocked_put = self.patch_requests_put(
            [
                requests.exceptions.RequestException(request=request),
                return_value,
            ]
        )

        r = self.file_uploader._upload_chunk(
            "some-url", "bytes=1-2/2", io.BytesIO(b"some-data")
        )
        self.assertEqual(mocked_put.call_count, 2)
        self.assertEqual(r, return_value)
        self.assertEqual(mocked_sleep.call_count, 1)
        mocked_sleep.assert_called_with(
            self.file_uploader._CHUNK_UPLOAD_RETRY_WAIT_SECS
        )

    def test_upload_chunk_raise_clientconnectionerror(self) -> None:
        """FileUploader._upload_chunk() called MAX_CHUNK_UPLOAD_RETIRES."""
        mocked_sleep = self.patch_sleep()

        request = mock.create_autospec(
            spec=requests.models.Request, url="https://test.com"
        )
        mocked_put = self.patch_requests_put(
            requests.exceptions.RequestException(request=request)
        )

        with self.assertRaises(exceptions.ClientConnectionError):
            self.file_uploader._upload_chunk(
                "some-url", "bytes=1-2/2", io.BytesIO(b"some-data")
            )

        self.assertEqual(
            mocked_put.call_count, FileUploader._MAX_CHUNK_UPLOAD_RETRIES
        )
        self.assertEqual(mocked_sleep.call_count, 2)
        mocked_sleep.assert_called_with(
            self.file_uploader._CHUNK_UPLOAD_RETRY_WAIT_SECS
        )
