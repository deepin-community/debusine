# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Infrastructure to create mock artifact data."""

import email.utils
import hashlib
import re
import subprocess
import tempfile
import textwrap
from collections.abc import Set
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

import debian.deb822 as deb822

from debusine.artifacts.local_artifact import (
    BinaryPackage,
    PackageBuildLog,
    SourcePackage,
    Upload,
    deb822dict_to_dict,
)


class ArtifactPlayground:
    """Create mock artifact data."""

    @classmethod
    def create_source_package(
        cls,
        destdir: Path,
        *,
        name: str = "hello",
        binaries: list[str] | None = None,
        version: str = "1.0-1",
        architectures: set[str] | None = None,
    ) -> SourcePackage:
        """Create a SourcePackage artifact."""
        dsc = destdir / f"{name}_{version}.dsc"
        sources: list[Path] = []
        v_parts = version.split("-", 1)
        if len(v_parts) == 2:
            sources.append(destdir / f"{name}_{version}.debian.tar.xz")
            sources.append(destdir / f"{name}_{v_parts[0]}.orig.tar.gz")
        else:
            sources.append(destdir / f"{name}_{version}.tar.gz")
        for source in sources:
            source.write_bytes(source.name.encode())
        cls.write_deb822_file(
            deb822.Dsc,
            dsc,
            sources,
            source=name,
            binaries=binaries,
            version=version,
            architectures=architectures,
        )
        return SourcePackage.create(
            name=name, version=version, files=[dsc] + sources
        )

    @classmethod
    def create_binary_package(
        cls,
        destdir: Path,
        *,
        name: str = "hello",
        version: str = "1.0-1",
        architecture: str = "all",
        source_name: str | None = None,
        source_version: str | None = None,
    ) -> BinaryPackage:
        """Create a BinaryPackage artifact."""
        deb = destdir / f"{name}_{version}_{architecture}.deb"
        cls.write_deb_file(
            deb,
            source_name=source_name or name,
            source_version=source_version or version,
        )
        return BinaryPackage.create(file=deb)

    @classmethod
    def create_upload(
        cls,
        destdir: Path,
        *,
        source: bool = True,
        src_name: str = "hello",
        version: str = "1.0-1",
        binary: bool = False,
        binaries: list[str] | None = None,
        bin_architecture: str = "all",
    ) -> Upload:
        """Create an Upload artifact."""
        architectures: set[str] = set()
        files: list[Path] = []
        if binaries is None:
            binaries = [src_name]
        if source:
            architectures.add("source")
            dsc = destdir / f"{src_name}_{version}.dsc"
            src_files: list[Path] = []
            v_parts = version.split("-", 1)
            if len(v_parts) == 2:
                src_files.append(
                    destdir / f"{src_name}_{version}.debian.tar.xz"
                )
                src_files.append(
                    destdir / f"{src_name}_{v_parts[0]}.orig.tar.gz"
                )
            else:
                src_files.append(destdir / f"{src_name}_{version}.tar.gz")
            for src_file in src_files:
                src_file.write_bytes(src_file.name.encode())
            cls.write_deb822_file(
                deb822.Dsc,
                dsc,
                src_files,
                source=src_name,
                binaries=binaries,
                version=version,
            )
            files += [dsc] + src_files
        if binary:
            architectures.add(bin_architecture)
            for bin_name in binaries:
                deb = destdir / f"{bin_name}_{version}_{bin_architecture}.deb"
                cls.write_deb_file(
                    deb, source_name=src_name, source_version=version
                )
                files.append(deb)
        else:
            binaries = None
        arch_tag = "+".join(sorted(architectures))
        changes = destdir / f"{src_name}_{version}_{arch_tag}.changes"
        cls.write_deb822_file(
            deb822.Changes,
            changes,
            files,
            source=src_name,
            binaries=binaries,
            version=version,
        )
        return Upload.create(changes_file=changes)

    @classmethod
    def create_package_build_log(
        cls,
        file: Path,
        *,
        source: str | None = None,
        version: str | None = None,
        architecture: str | None = None,
    ) -> PackageBuildLog:
        """Create a PackageBuildLog artifact."""
        if source is None or version is None:
            if m := re.match(
                r"([a-z][a-z0-9+-.]+)"
                r"_([0-9][A-Za-z0-9+-.~:]*)"
                r"_([a-z0-9-]+).build$",
                file.name,
            ):
                if source is None:
                    source = m.group(1)
                if version is None:
                    version = m.group(2)
            else:
                raise ValueError(
                    "source or version not provided"
                    " and cannot be inferred from file name"
                )

        return PackageBuildLog.create(
            file=file,
            source=source,
            version=version,
            architecture=architecture,
        )

    @classmethod
    def write_deb_file(
        cls,
        path: Path,
        *,
        source_name: str | None = None,
        source_version: str | None = None,
        control_file_names: list[str] | None = None,
        maintainer: str = "Example Maintainer <example@example.org>",
        data_files: dict[PurePath, bytes] | None = None,
        data_symlinks: dict[PurePath, PurePath] | None = None,
    ) -> dict[str, Any]:
        """Write a debian control file."""
        with tempfile.TemporaryDirectory() as tempdir:
            build_directory = Path(tempdir)
            (build_directory / "DEBIAN").mkdir()
            if m := re.match(
                r"([a-z][a-z0-9+-.]+)"
                r"_([0-9][A-Za-z0-9+-.~:]*)"
                r"_([a-z0-9-]+).u?deb$",
                path.name,
            ):
                package, version, architecture = m.groups()
            else:  # pragma: no cover
                raise ValueError(f"Badly-formed .deb file name: {path}")
            source = source_name if source_name is not None else package
            if source_version is not None and source_version != version:
                source += f" ({source_version})"

            control_contents = textwrap.dedent(
                f"""\
                Package: {package}
                Version: {version}
                Architecture: {architecture}
                Maintainer: {maintainer}
                Description: Example description
                """
            )
            if source != package:
                control_contents += f"Source: {source}\n"
            (build_directory / "DEBIAN" / "control").write_text(
                control_contents
            )
            for name in control_file_names or []:
                (build_directory / "DEBIAN" / name).touch()
            if data_files is not None:
                for data_path, data_contents in data_files.items():
                    assert not data_path.is_absolute()
                    (build_directory / data_path).parent.mkdir(
                        parents=True, exist_ok=True
                    )
                    (build_directory / data_path).write_bytes(data_contents)
            if data_symlinks is not None:
                for data_path, target in data_symlinks.items():
                    assert not data_path.is_absolute()
                    (build_directory / data_path).parent.mkdir(
                        parents=True, exist_ok=True
                    )
                    (build_directory / data_path).symlink_to(target)
            subprocess.run(
                [
                    "dpkg-deb",
                    "--root-owner-group",
                    "--build",
                    build_directory,
                    path,
                ],
                stdout=subprocess.DEVNULL,
            )

            return deb822dict_to_dict(deb822.Deb822(control_contents.encode()))

    @classmethod
    def write_deb822_file(
        cls,
        file_type: type[deb822.Deb822],
        path: Path,
        files: list[Path],
        *,
        format_version: str | None = None,
        source: str = "hello-traditional",
        binaries: list[str] | None = None,
        version: str,
        architectures: set[str] | None = None,
        binnmu: bool = False,
        maintainer: str = "Example Maintainer <example@example.org>",
    ) -> dict[str, Any]:
        """Write dsc or changes file with files information."""
        architectures = set(architectures or set())
        match file_type:
            case deb822.Dsc:
                architectures.add("any")
                format_version = format_version or "3.0 (quilt)"
            case deb822.Changes:
                for file in files:
                    name = file.name
                    if name.endswith(".dsc"):
                        architectures.add("source")
                    elif name.endswith((".deb", ".udeb")):
                        if m := re.match(r".*_([a-z0-9-]+).u?deb$", name):
                            architectures.add(m.group(1))
                format_version = format_version or "1.8"
            case _ as unreachable:
                raise NotImplementedError(
                    f"{unreachable!r} not supported:"
                    " only Dsc and Changes are supported"
                )

        if binnmu:
            source += f" ({version})"
            version += "+b1"

        changes_contents = textwrap.dedent(
            f"""\
            Format: {format_version}
            Source: {source}
            Architecture: {' '.join(sorted(architectures))}
            Version: {version}
            Maintainer: {maintainer}
            """
        )
        match file_type:
            case deb822.Dsc:
                if binaries is None:
                    binaries = [source]
                changes_contents += (
                    textwrap.dedent(
                        f"""\
                        Binary: {', '.join(binaries)}
                        Homepage: http://www.gnu.org/software/{source}/
                        Standards-Version: 4.3.0
                        Package-List:
                        """
                    )
                    + "".join(
                        f" {binary} deb devel optional arch=any\n"
                        for binary in binaries
                    )
                )
            case deb822.Changes:
                timestamp = datetime(
                    2024, 12, 1, 0, 0, 0, tzinfo=UTC
                ).timestamp()
                changes_contents += textwrap.dedent(
                    f"""\
                    Date: {email.utils.formatdate(timestamp)}
                    Distribution: unstable
                    Urgency: medium
                    Changes:
                      {source} ({version}) unstable; urgency=medium
                      .
                        * Test upload.
                    """
                )
                if binaries:
                    changes_contents += (
                        f"Binary: {' '.join(binaries)}\n"
                        + "Description:\n"
                        + "\n".join(
                            f" {binary} - A Description" for binary in binaries
                        )
                        + "\n"
                    )
                # bookworm's coverage.py seems to get confused here
                pass  # pragma: no cover
            case _ as unreachable:
                raise NotImplementedError(
                    f"{unreachable!r} not supported:"
                    " only Dsc and Changes are supported"
                )
        changes_contents += cls.hash_deb822_files(
            file_type, {file.name: file.read_bytes() for file in files}
        )
        path.write_text(changes_contents)

        return deb822dict_to_dict(file_type(path.read_bytes()))

    @staticmethod
    def hash_deb822_files(
        file_type: type[deb822.Deb822],
        file_contents: dict[str, bytes],
        break_hashes: Set[str] = frozenset(),
        break_sizes: Set[str] = frozenset(),
    ) -> str:
        """
        Build the file-manifest parts of a Deb822 file that we're generating.

        Hash the files in file_contents, a dict of {filename: file_contents}.
        file_type refers to the kind of Deb822 object that we're generating.
        For any hash named in break_hashes, the relevant hashes will be
        incorrect. break_sizes works similarly, but alters the size of the
        file, in the named hash block.

        The returned string is a block of deb822-formatted file content.
        """
        output = []
        for hash_ in ("sha256", "sha1", "md5"):
            if hash_ == "md5":
                output.append("Files:")
            else:
                output.append(f"Checksums-{hash_.title()}:")
            for filename, contents in file_contents.items():
                hasher = hashlib.new(hash_)
                hasher.update(contents)
                size = len(contents)
                if hash_ in break_hashes:
                    hasher.update(b"oops!")
                if hash_ in break_sizes:
                    size += 1
                if hash_ == "md5" and file_type == deb822.Changes:
                    output.append(
                        f" {hasher.hexdigest()} {size} unknown optional "
                        f"{filename}"
                    )
                else:
                    output.append(f" {hasher.hexdigest()} {size} {filename}")
        return "\n".join(output)
