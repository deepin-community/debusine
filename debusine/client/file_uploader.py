# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""File uploader."""

import logging
import mmap
import os
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING

import requests
from requests import status_codes

from debusine.client import exceptions
from debusine.client.client_utils import requests_put_or_connection_error
from debusine.utils import parse_range_header

if TYPE_CHECKING:
    from _typeshed import SupportsRead


class UnexpectedStatusCodeError(Exception):
    """Raised when the server did not return the expected status code."""

    def __init__(self, response: requests.models.Response) -> None:
        """Initialize exception."""
        self._response = response

    def __str__(self) -> str:
        """Return human-readable error message."""
        request = self._response.request

        error = f"Client HTTP {request.method} {request.url} "

        content_range = request.headers.get("Content-Range", None)

        if content_range is not None:
            error += f"with Content-Range {content_range} "

        error += (
            f"Received unexpected status code: {self._response.status_code} "
            f"Response body:\n{self._response.text}"
        )

        return error


class FileUploadError(Exception):
    """File could not be uploaded."""


class FileUploader:
    """
    Class used to upload files to debusine.

    Before uploading a file requests the server (via Content-Range header)
    which part of the file needs to be uploaded (or if all the file is
    already uploaded).
    """

    # Retries if a file upload fails (e.g. chunks cannot be uploaded or
    # the server's response is a file conflict, etc.)
    _MAX_FILE_UPLOAD_RETRIES = 3

    # Retries if a chunk upload fails (e.g. unexpected response from
    # the server)
    _MAX_CHUNK_UPLOAD_RETRIES = 3

    # Seconds to wait before retrying to upload a chunk again
    # (e.g. if it failed because connection was lost, unexpected response
    # from the server)
    _CHUNK_UPLOAD_RETRY_WAIT_SECS = 5

    # Size of the chunks. If changing it you might need to change Nginx's
    # client_max_body_size as well
    _MAX_CHUNK_SIZE_BYTES = 50 * 1024 * 1024

    def __init__(self, token: str, *, logger: logging.Logger) -> None:
        """Initialize object."""
        self._headers = {"Token": token}
        self._logger = logger

    def _upload_chunk(
        self, url: str, content_range: str, file: "SupportsRead[bytes]"
    ) -> requests.models.Response:
        attempt = 0

        while True:
            try:
                r = requests_put_or_connection_error(
                    url,
                    headers={**self._headers, "Content-Range": content_range},
                    data=file,
                )
                return r
            except exceptions.ClientConnectionError as exc:
                attempt += 1

                if attempt == self._MAX_CHUNK_UPLOAD_RETRIES:
                    raise exc

                time.sleep(self._CHUNK_UPLOAD_RETRY_WAIT_SECS)

    @staticmethod
    def _parse_range_or_raise_exception(
        response: requests.models.Response, file_size: int
    ) -> dict[str, int]:
        try:
            parsed_range = parse_range_header(response.headers, file_size)
            if parsed_range is not None:
                return parsed_range
        except ValueError:
            pass

        raise RuntimeError(
            "Required range header not in response or invalid for: "
            f"{response.request.method} "
            f"{response.request.url} content-range header: "
            f"{response.headers.get('content-range', None)}"
        )

    def _upload_file(self, file: IO[bytes], file_size: int, url: str) -> bool:
        content_range = f"bytes */{file_size}"

        r = requests_put_or_connection_error(
            url,
            headers={**self._headers, "Content-Range": content_range},
        )

        if r.status_code not in (
            status_codes.codes.ok,
            status_codes.codes.partial_content,
        ):
            raise UnexpectedStatusCodeError(r)

        if r.status_code == status_codes.codes.ok:
            # The file is already uploaded
            return True

        parsed_range = self._parse_range_or_raise_exception(r, file_size)

        start_position = parsed_range["end"]

        while True:
            end_position = min(
                file_size - 1, start_position + self._MAX_CHUNK_SIZE_BYTES - 1
            )

            # Create filelike object of the right chunk for requests.put
            file_mmaped = mmap.mmap(
                file.fileno(), end_position + 1, prot=mmap.PROT_READ
            )
            file_mmaped.seek(start_position)

            content_range = f"bytes {start_position}-{end_position}/{file_size}"

            r = self._upload_chunk(url, content_range, file_mmaped)

            if r.status_code == requests.codes.created:
                return True
            elif r.status_code == requests.codes.ok:
                parsed_range = self._parse_range_or_raise_exception(
                    r, file_size
                )
                start_position = parsed_range["end"]
            elif r.status_code == requests.codes.conflict:
                return False
            else:
                raise UnexpectedStatusCodeError(r)

    def upload(self, file_path: Path, url: str) -> None:
        """Upload the file used in this instance (see __init__())."""
        self._logger.info("Uploading %s to %s", file_path, url)

        upload_attempt = 1
        success = False

        while upload_attempt <= self._MAX_FILE_UPLOAD_RETRIES:
            with open(file_path, "rb") as file:
                success = self._upload_file(
                    file, os.stat(file_path).st_size, url
                )
                if success:
                    break
                upload_attempt += 1

        if not success:
            raise FileUploadError(
                f"Error uploading {file_path} to {url} "
                f"({self._MAX_FILE_UPLOAD_RETRIES} retries)"
            )
