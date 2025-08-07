# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utility Functions used by debusine client."""

import hashlib
import logging
import os
import re
import sys
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeAlias, Union
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from debian.deb822 import Changes, Deb822, Dsc
from requests_file import FileAdapter

from debusine.artifacts import (
    BinaryPackage,
    BinaryPackages,
    LocalArtifact,
    SourcePackage,
    Upload,
)
from debusine.client import exceptions

SOURCE_PACKAGE_HASHES = ("sha256", "sha1", "md5")
log = logging.getLogger(__name__)
DownloadedFileStats: TypeAlias = dict[str, Union[str, int]]


def requests_put_or_connection_error(
    *args: Any, **kwargs: Any
) -> requests.models.Response:
    r"""Return requests.put(\*args, \*\*kwargs) or ClientConnectionError."""
    try:
        return requests.put(*args, **kwargs)
    except requests.exceptions.RequestException as exc:
        if exc.request is not None:
            raise exceptions.ClientConnectionError(
                f"Cannot connect to {exc.request.url}. Error: {str(exc)}"
            )
        else:
            raise exceptions.ClientConnectionError(
                f"Cannot connect. Error: {str(exc)}"
            )


def download_file(
    url: str,
    destination: Path,
    hashes: Iterable[str] = SOURCE_PACKAGE_HASHES,
) -> DownloadedFileStats:
    """
    Download url into destination.

    Return all the hashes specified and size, as a dict.
    """
    log.info("Downloading %s...", url)
    hashers = {hash_name: hashlib.new(hash_name) for hash_name in hashes}
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        if "Content-Length" in r.headers:
            log.info("Size: %.2f MiB", int(r.headers["Content-Length"]) / 2**20)
        with destination.open("xb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                for hasher in hashers.values():
                    hasher.update(chunk)
            size = f.tell()
    stats: dict[str, str | int] = {"size": size}
    for hash_name, hasher in hashers.items():
        stats[hash_name] = hasher.hexdigest()
    return stats


def get_url_contents_sha256sum(
    url: str, max_size: int, allow_file: bool = False
) -> tuple[bytes, str]:
    """
    Fetch URL, return contents and sha256sum.

    :param url: the URL to fetch.
    :param max_size: the maximum number of bytes to read from the URL.
    :param allow_file: if True, allow reading `file://` URLs; the caller
      must previously have checked the URL path to ensure that it is in an
      allowed part of the file system.
    :raise exceptions.ClientContentTooLargeError: content is bigger than
      max_size.

    :return: tuple with content and its sha256sum.
    """
    sha256sum = hashlib.sha256()
    contents: list[bytes] = []
    size = 0
    with requests.Session() as session:
        if allow_file:
            session.mount("file://", FileAdapter())
        with session.get(url, stream=True) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=min(1024 * 1024, max_size)):
                sha256sum.update(chunk)
                contents.append(chunk)
                size += len(chunk)
                if size >= max_size:
                    raise exceptions.ContentTooLargeError(
                        "Content size exceeds maximum "
                        f"allowed size of {max_size} bytes."
                    )

    return b"".join(contents), sha256sum.hexdigest()


class DGet:
    """
    Download url into workdir.

    If it's a .changes or .dsc file, download the referenced files and check
    their hashes. The workdir is expected to be clean and any existing files
    that don't match hashes in a .changes or .dsc will raise an exception.
    """

    base_url: str
    known_files: dict[str, DownloadedFileStats]
    downloaded: set[str]
    queue: list[str]
    workdir: Path

    def __init__(self, url: str, workdir: Path) -> None:
        """Set up for a single set of downloads."""
        parsed_url = urlparse(url)
        # If we are downloading multiple files, we need a logical structure
        if (
            parsed_url.query != ""
            or parsed_url.fragment != ""
            or parsed_url.path.endswith("/")
        ):
            raise AssertionError("DGet can only operate on simple URLs")
        url_path = Path(parsed_url.path)
        self.base_url = urlunparse(
            parsed_url._replace(path=str(url_path.parent) + "/")
        )
        self.known_files = {}
        self.downloaded = set()
        self.queue = [url_path.name]
        self.workdir = workdir

    def download(self) -> None:
        """Download all queued files."""
        while self.queue:
            self.download_file(self.queue.pop(0))

    def download_file(self, filename: str) -> None:
        """Download one filename into workdir."""
        if filename in self.downloaded:  # pragma: no cover
            return
        file_url = urljoin(self.base_url, filename)
        destination = self.workdir / filename
        stats = download_file(file_url, destination)
        self._record_download(filename, stats)
        if destination.suffix in (".dsc", ".changes"):
            self._add_referenced_files(destination)

    def _record_download(
        self, filename: str, stats: DownloadedFileStats
    ) -> None:
        self.downloaded.add(filename)
        self.known_files.setdefault(filename, {})
        for stat_name, value in stats.items():
            self._add_stat("Downloaded file", filename, stat_name, value)

    def _add_stat(
        self,
        source_name: str,
        filename: str,
        stat_name: str,
        value: str | int,
    ) -> None:
        stats = self.known_files[filename]
        if stat_name in stats:
            if stats[stat_name] != value:
                raise exceptions.ContentValidationError(
                    f"{source_name} has mis-matching {stat_name} for "
                    f"{filename} ({value} != {stats[stat_name]})"
                )
        else:
            stats[stat_name] = value

    def _add_referenced_files(self, path: Path) -> None:
        for name, size, hash_name, hash_digest in self._iter_referenced_files(
            path
        ):
            if "/" in name:
                raise exceptions.ContentValidationError(
                    f"{path.name} contains invalid file name {name}"
                )
            if name not in self.known_files:
                self.known_files[name] = {}
                self.queue.append(name)
            self._add_stat(path.name, name, "size", size)
            self._add_stat(path.name, name, hash_name, hash_digest)

    def _iter_referenced_files(
        self, path: Path
    ) -> Generator[tuple[str, int, str, str], None, None]:
        parser: type[Deb822]
        if path.suffix == ".dsc":
            parser = Dsc
        elif path.suffix == ".changes":
            parser = Changes
        else:  # pragma: no cover
            raise ValueError("Only .dsc and .changes files are accepted")

        with path.open() as f:
            parsed = parser(f)

        for hash_name in SOURCE_PACKAGE_HASHES:
            field_name = f"checksums-{hash_name}"
            if hash_name == "md5":
                field_name = "files"
            if field_name not in parsed:
                continue

            for record in parsed[field_name]:
                name = record["name"]
                size = int(record["size"])
                hash_digest = record[
                    "md5sum" if hash_name == "md5" else hash_name
                ]
                yield name, size, hash_name, hash_digest


def dget(url: str, workdir: Path) -> None:
    """Execute a download with DGet."""
    DGet(url, workdir).download()


@contextmanager
def get_debian_package(path: str) -> Generator[Path, None, None]:
    """
    Context Manager to get a Path to a specified package URL.

    Download if necessary, into a temporary directory.
    Referenced supporting files will be downloaded into the same directory.
    The (possible) temporary directory will be cleaned up at block exit.
    """
    parsed = urlparse(path)
    if parsed.scheme == "":
        yield Path(path)
    elif parsed.scheme == "file":
        yield Path(parsed.path)
    elif parsed.scheme in ("http", "https"):
        with TemporaryDirectory(prefix="debusine-import-") as td:
            work_path = Path(td)
            dget(path, work_path)
            yield work_path / os.path.basename(parsed.path)
    else:
        raise ValueError(f"Not a supported URL scheme: {path}")


def prepare_changes_for_upload(changes: Path) -> list[LocalArtifact[Any]]:
    """Prepare artifacts to upload based on a ``.changes`` file."""
    with changes.open("rb") as f:
        parsed = Changes(f)
    architectures = parsed["Architecture"].split()
    artifact_factories: list[Callable[[], LocalArtifact[Any]]] = []
    for architecture in architectures:
        if architecture == "source":
            dscs = [
                file["name"]
                for file in parsed["Files"]
                if file["name"].endswith(".dsc")
            ]
            if len(dscs) != 1:
                print(
                    f"Expecting exactly 1 .dsc in source package, "
                    f"found {len(dscs)}",
                    file=sys.stderr,
                )
                raise SystemExit(3)
            artifact_factories.append(
                partial(prepare_dsc_for_upload, changes.parent / dscs[0])
            )
        else:
            debs = [
                changes.parent / file["name"]
                for file in parsed["Files"]
                if file["name"].endswith(
                    (
                        f"_{architecture}.deb",
                        f"_{architecture}.udeb",
                    )
                )
            ]
            if len(debs) < 1:
                print(
                    "Expecting at least one .deb per arch in binary packages",
                    file=sys.stderr,
                )
                raise SystemExit(3)

            srcpkg_name = parsed["Source"]
            srcpkg_version = parsed["Version"]
            m = re.match(r"^(?P<name>\S+) \((?P<version>\S+)\)$", srcpkg_name)
            if m:
                srcpkg_name = m.group("name")
                srcpkg_version = m.group("version")

            # Create artifacts for each individual .deb.
            for deb in debs:
                artifact_factories.append(partial(prepare_deb_for_upload, deb))
            # For convenience, also create a grouped artifact for all
            # the .debs.
            artifact_factories.append(
                partial(
                    BinaryPackages.create,
                    srcpkg_name=srcpkg_name,
                    srcpkg_version=srcpkg_version,
                    version=parsed["Version"],
                    architecture=architecture,
                    files=debs,
                )
            )
    artifacts: list[LocalArtifact[Any]] = [
        Upload.create(changes_file=changes)
    ] + [factory() for factory in artifact_factories]
    return artifacts


def prepare_dsc_for_upload(dsc: Path) -> SourcePackage:
    """Prepare an artifact to upload based on a ``.dsc`` file."""
    files = [dsc]
    directory = dsc.parent
    with dsc.open() as f:
        parsed = Dsc(f)
        name = parsed["source"]
        version = parsed["version"]
        for file in parsed["files"]:
            files.append(directory / file["name"])
    return SourcePackage.create(name=name, version=version, files=files)


def prepare_deb_for_upload(deb: Path) -> BinaryPackage:
    """Prepare an artifact to upload based on a ``.deb`` file."""
    return BinaryPackage.create(file=deb)
