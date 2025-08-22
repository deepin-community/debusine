# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""View extension to display contents of files."""

import contextlib
import io
import mimetypes
import mmap
import os.path
from collections.abc import Generator, Iterable
from functools import cached_property
from pathlib import Path
from typing import Any, NamedTuple, Self, TYPE_CHECKING

import pygments
import pygments.formatters
import pygments.lexers
from django.http import FileResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from django.template import Context
from django.utils.http import parse_header_parameters
from django.utils.safestring import SafeString
from django.views.generic.base import View
from rest_framework import status

from debusine.db.models import FileInArtifact
from debusine.server.views import ProblemResponse
from debusine.utils import parse_range_header
from debusine.web.views.base import Widget

if TYPE_CHECKING:
    from _typeshed import SupportsRead

    SupportsRead  # fake usage for vulture
    HtmlFormatter = pygments.formatters.HtmlFormatter
else:
    # pygments doesn't support generic types at run-time yet.
    class _HtmlFormatter:
        def __class_getitem__(*args):
            return pygments.formatters.HtmlFormatter

    HtmlFormatter = _HtmlFormatter


# Above this file size, only offer to view or download raw
MAX_FILE_SIZE = 2 * 1024 * 1024


class FileUI(NamedTuple):
    """Information about how to display a file."""

    #: content type to use for downloads
    content_type: str
    #: tag identifying specialised view functions to display the file
    widget_class: type["FileWidget"]
    #: pygments lexer to use (default: autodetect from mimetype)
    pygments_lexer: str | None = None

    @classmethod
    def from_file_in_artifact(cls, file: FileInArtifact) -> Self:
        """Get a FileUI for a file."""
        content_type: str
        widget_class: type["FileWidget"]
        if file.content_type is not None:
            content_type = file.content_type
            main_type, _ = parse_header_parameters(content_type)
            if main_type.startswith("text/") or main_type == "application/json":
                if main_type not in {
                    "text/plain",
                    "text/markdown",
                    "application/json",
                }:
                    # Serve other text types as text/plain; clients should
                    # not be allowed to tell Debusine to serve arbitrary
                    # JavaScript or similar.
                    content_type = "text/plain"
                widget_class = TextFileWidget
            else:
                if main_type not in {
                    "application/gzip",
                    "application/octet-stream",
                    "application/vnd.debian.binary-package",
                    "application/x-bzip2",
                    "application/x-tar",
                    "application/x-xz",
                    "application/zstd",
                }:
                    # Serve other binary types as application/octet-stream,
                    # to avoid attacks where clients try to tell Debusine to
                    # serve something like malicious images that browsers
                    # may try to interpret.
                    content_type = "application/octet-stream"

                widget_class = BinaryFileWidget
        else:
            match os.path.splitext(file.path)[1]:
                case ".buildinfo" | ".dsc" | ".changes":
                    content_type = "text/plain; charset=utf-8"
                    widget_class = TextFileWidget
                case ".txt" | ".log" | ".build" | ".buildlog" | ".sources":
                    content_type = "text/plain; charset=utf-8"
                    widget_class = TextFileWidget
                case '.md':
                    content_type = "text/markdown; charset=utf-8"
                    widget_class = TextFileWidget
                case _:
                    # Logic taken from django FileResponse.set_headers
                    encoding_map = {
                        'bzip2': 'application/x-bzip',
                        'gzip': 'application/gzip',
                        'xz': 'application/x-xz',
                    }
                    _content_type, encoding = mimetypes.guess_type(file.path)
                    content_type = _content_type or 'application/octet-stream'
                    # Encoding isn't set to prevent browsers from automatically
                    # uncompressing files.
                    if encoding:
                        content_type = encoding_map.get(encoding, content_type)
                    widget_class = BinaryFileWidget

        pygments_lexer: str | None = None
        match os.path.splitext(file.path)[1]:
            case ".buildinfo" | ".dsc" | ".changes":
                pygments_lexer = "debcontrol"

        if file.file.size > MAX_FILE_SIZE:
            widget_class = TooBigFileWidget

        return cls(content_type, widget_class, pygments_lexer)


class LinenoHtmlFormatter(HtmlFormatter[Any]):
    """HtmlFormatter that keeps a count of line numbers."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Keep a count of line numbers."""
        super().__init__(*args, **kwargs)
        self.line_count = 0

    def wrap(
        self, *args: Any, **kwargs: Any
    ) -> Generator[tuple[int, str], None, None]:
        """Count rendered lines."""
        # mypy complains that pygments.formatters.HtmlFormatter.wrap is
        # untyped, which is true, but we can't fix that here.
        for val in super().wrap(
            *args, **kwargs
        ):  # type: ignore[no-untyped-call]
            self.line_count += val[0]
            yield val

    def render_linenos(self, file_tag: str | None = None) -> str:
        """Render line numbers to go alongside rendered contents."""
        width = len(str(self.line_count))
        suffix = f"-{file_tag}" if file_tag else ""
        return SafeString(
            "\n".join(
                f"<a id='L{n}{suffix}' href='#L{n}{suffix}'>"
                f"{str(n).rjust(width)}</a>"
                for n in range(1, self.line_count + 1)
            )
        )


class FileWidget(Widget):
    """Widget that displays a file."""

    template_name: str

    def __init__(
        self,
        file_in_artifact: FileInArtifact,
        *,
        file_ui: FileUI,
        file_tag: str | None = None,
    ) -> None:
        """
        Initialize object with a file to display.

        @param file_tag: there might be pages with more
          than one FileWidget (even referring to the same file). In order
          to create unique anchors to the lines, it's possible to pass a
          file_tag. The anchors will contain it, currently in the form of:
          "#L{line_number}-{file_tag}"
        """
        self.file_in_artifact = file_in_artifact
        self.file_ui = file_ui or FileUI.from_file_in_artifact(file_in_artifact)
        self.file_tag = file_tag

    @classmethod
    def create(
        cls,
        file_in_artifact: FileInArtifact,
        *,
        file_ui: FileUI | None = None,
        file_tag: str | None = None,
    ) -> "FileWidget":
        """Create a file widget for the given file."""
        file_ui = file_ui or FileUI.from_file_in_artifact(file_in_artifact)
        return file_ui.widget_class(
            file_in_artifact,
            file_ui=file_ui,
            file_tag=file_tag,
        )

    @contextlib.contextmanager
    def _open_file(self) -> Generator["SupportsRead[bytes]"]:
        assert self.file_in_artifact is not None
        scope = self.file_in_artifact.artifact.workspace.scope
        file_backend = scope.download_file_backend(self.file_in_artifact.file)
        with file_backend.get_stream(self.file_in_artifact.file) as stream:
            yield stream

    def get_context_data(self) -> dict[str, Any]:
        """Return the context for this widget."""
        return {
            "file_in_artifact": self.file_in_artifact,
            "file_ui": self.file_ui,
        }

    def render(self, context: Context) -> str:
        """Render the widget."""
        assert context.template is not None
        template = context.template.engine.get_template(self.template_name)
        with context.update(self.get_context_data()):
            return template.render(context)


class TextFileWidget(FileWidget):
    """Show text files."""

    template_name = "web/_file_text.html"

    # add_context_data_* names match FileViewTypes enum options

    def get_context_data(self) -> dict[str, Any]:
        """Add context data for text files."""
        context = super().get_context_data()

        if self.file_tag is None:
            div_id = "file-contents"
        else:
            div_id = f"file-contents-{self.file_tag}"

        context["file_contents_div_id"] = div_id

        if self.file_in_artifact.complete:
            # Generate CSS with:
            # python3 -m pygments -S github-dark -f html \
            #     > debusine/web/static/web/css/debusine-code-highlight.css
            # See https://pygments.org/styles/ for a list of styles
            if self.file_ui.pygments_lexer:
                lexer = pygments.lexers.get_lexer_by_name(
                    self.file_ui.pygments_lexer
                )
            else:
                lexer = None
                try:
                    lexer = pygments.lexers.get_lexer_for_mimetype(
                        self.file_ui.content_type
                    )
                except pygments.util.ClassNotFound:
                    # Try using the main_type only
                    main_type, _ = parse_header_parameters(
                        self.file_ui.content_type
                    )
                    try:
                        lexer = pygments.lexers.get_lexer_for_mimetype(
                            main_type
                        )
                    except pygments.util.ClassNotFound:
                        pass

                if lexer is None:
                    lexer = pygments.lexers.get_lexer_for_mimetype("text/plain")

            formatter = LinenoHtmlFormatter(
                cssclass="file_highlighted",
                linenos=False,
            )

            with self._open_file() as stream:
                formatted = pygments.highlight(stream.read(), lexer, formatter)

            context["file_linenumbers"] = SafeString(
                formatter.render_linenos(self.file_tag)
            )
            context["file_content"] = SafeString(formatted)

        return context


class BinaryFileWidget(FileWidget):
    """Show binary files."""

    template_name = "web/_file_binary.html"


class TooBigFileWidget(FileWidget):
    """Show files that are too big to be loaded."""

    template_name = "web/_file_too_big.html"

    def get_context_data(self) -> dict[str, Any]:
        """Add context data for files too big to display."""
        context = super().get_context_data()
        context["file_max_size"] = MAX_FILE_SIZE
        return context


class FileDownloadMixin(View):
    """File streaming functions for views."""

    def stream_file(
        self,
        file_in_artifact: FileInArtifact,
        ui_info: FileUI,
        download: bool = True,
    ) -> HttpResponseBase:
        """Return a response that streams the_given file."""
        if not file_in_artifact.complete:
            return ProblemResponse(
                "Cannot download incomplete file",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        scope = file_in_artifact.artifact.workspace.scope
        file_backend = scope.download_file_backend(file_in_artifact.file)
        entry = file_backend.get_entry(file_in_artifact.file)

        url = entry.get_url(content_type=ui_info.content_type)
        if url is not None:
            # The client can download the file from the backend
            # TODO: this does not allow to set content-disposition, that is, to
            # distinguish between "view raw" or "download". Not sure if it can
            # be solved.
            return redirect(url)

        with entry.get_stream() as file:
            file_size = file_in_artifact.file.size
            try:
                content_range = parse_range_header(
                    self.request.headers, file_size
                )
            except ValueError as exc:
                # It returns ProblemResponse because ranges are not used
                # by end users directly
                return ProblemResponse(str(exc))

            status_code: int
            start: int | None
            end: int | None
            if content_range is None:
                # Whole file
                status_code = status.HTTP_200_OK
                start = 0
                end = file_size - 1
            else:
                # Part of a file
                status_code = status.HTTP_206_PARTIAL_CONTENT
                start = content_range["start"]
                end = content_range["end"]

                # It returns ProblemResponse because ranges are not used
                # by end users directly
                if start > file_size:
                    return ProblemResponse(
                        f"Invalid Content-Range start: {start}. "
                        f"File size: {file_size}"
                    )

                elif end >= file_size:
                    return ProblemResponse(
                        f"Invalid Content-Range end: {end}. "
                        f"File size: {file_size}"
                    )

            # Use mmap:
            # - No support for content-range or file chunk in Django
            #   as of 2023, so create filelike object of the right chunk
            # - Prevents FileResponse.file_to_stream.name from taking
            #   precedence over .filename and break mimestype
            file_partitioned: Iterable[object]
            if file_size == 0:
                # cannot mmap an empty file
                file_partitioned = io.BytesIO(b"")
            else:
                file_partitioned = mmap.mmap(
                    file.fileno(), end + 1, prot=mmap.PROT_READ
                )
                file_partitioned.seek(start)

            filename = Path(file_in_artifact.path)

            response = FileResponse(
                file_partitioned,
                filename=filename.name,
                status=status_code,
            )

            response["Accept-Ranges"] = "bytes"
            response["Content-Length"] = end - start + 1
            if file_size > 0:
                response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            if download:
                disposition = "attachment"
            else:
                disposition = "inline"
            response["Content-Disposition"] = (
                f'{disposition}; filename="{filename.name}"'
            )
            response["Content-Type"] = ui_info.content_type

            return response


class PathMixin(View):
    """View that accepts a path kwarg."""

    @staticmethod
    def normalize_path(path: str) -> str:
        """
        Normalize a path used as a subdirectory prefix.

        It will also constrain paths not to point above themselves by extra ../
        components
        """
        path = os.path.normpath(path).strip("/")
        while path.startswith("../"):
            path = path[3:]
        if path in ("", ".", ".."):
            return "/"
        return f"/{path}/"

    @cached_property
    def path(self) -> str:
        """
        Return the current subdirectory as requested by the user.

        This returns a path relative to the root of the artifact, with ""
        standing for the whole artifact
        """
        if not (path := self.kwargs.get("path")):
            return ""
        return self.normalize_path(path)[1:]
