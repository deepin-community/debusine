# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Local artifact's representation.

See docs/design/ontology.html for the semantics of the Artifacts.
"""
import abc
import json
import re
from collections.abc import Iterable, Sequence
from json import JSONDecodeError
from pathlib import Path, PurePath
from typing import Any, ClassVar, Generic, Self, TypeVar, cast, overload

from debian import deb822, debfile

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic  # type: ignore

import debusine.artifacts.models as data_models
from debusine import utils
from debusine.artifacts.artifact_utils import files_in_meta_file_match_files
from debusine.assets import KeyPurpose
from debusine.client.models import FileRequest
from debusine.utils import extract_generic_type_arguments

AD = TypeVar("AD", bound=data_models.ArtifactData)


class LocalArtifact(pydantic.BaseModel, Generic[AD], abc.ABC):
    """Represent an artifact locally."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid

    #: Artifact type
    category: data_models.ArtifactCategory
    # Keys are paths in the artifact. Values the paths in the local system
    files: dict[str, Path] = pydantic.Field(default_factory=dict)
    # Keys are paths in the artifact. Values are FileRequests for files that do
    # not exist locally but can be expected to already exist on the server.
    remote_files: dict[str, FileRequest] = pydantic.Field(default_factory=dict)
    # Keys are paths in the artifact. Values are MIME types, suitable for
    # Content-Type headers.
    content_types: dict[str, str] = pydantic.Field(default_factory=dict)
    #: Artifact data
    data: AD
    # TODO: it would be great to not have to redefine data in subclasses, but
    # it needs pydantic's Generics support, which might require work to work
    # with extract_generic_type_arguments

    #: Default value for category
    _category: ClassVar[data_models.ArtifactCategory]

    #: Class used as the in-memory representation of artifact data.
    _data_type: type[AD] = pydantic.PrivateAttr()
    # data_type is marked as PrivateAttr to make mypy happy. Setting
    # underscore_attrs_are_private to True does not seem to be enough

    _local_artifacts_category_to_class: dict[
        str, type["LocalArtifact['Any']"]
    ] = {}

    def __init__(self, **kwargs: Any) -> None:
        """Set category to _category by default."""
        kwargs.setdefault("category", self._category)
        if "data" in kwargs:
            if isinstance(d := kwargs["data"], dict):
                kwargs["data"] = self.create_data(d)
        super().__init__(**kwargs)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Register subclass into LocalArtifact._local_artifacts_category_to_class.

        Allow to list possible valid options (in the client or server).
        """
        super().__init_subclass__(**kwargs)

        # The task data type, computed by introspecting the type argument
        # used to specialize this generic class.
        [cls._data_type] = extract_generic_type_arguments(cls, LocalArtifact)

        LocalArtifact._local_artifacts_category_to_class[cls._category] = cls

    @classmethod
    def create_data(cls, data_dict: dict[str, Any]) -> AD:
        """Instantiate a data model from a dict."""
        return cast(AD, cls._data_type(**data_dict))

    @staticmethod
    def artifact_categories() -> list[str]:
        """Return list of artifact categories."""
        return sorted(LocalArtifact._local_artifacts_category_to_class.keys())

    @pydantic.validator("category", pre=True)
    @classmethod
    def _validate_category(cls, category: str) -> str:
        """Validate that the category is known."""
        if category not in cls._local_artifacts_category_to_class:
            raise ValueError(
                f"Invalid category: '{category}'. Expected one of "
                f"{', '.join(sorted(cls._local_artifacts_category_to_class))}."
            )
        return category

    @staticmethod
    def class_from_category(category: str) -> type["LocalArtifact['Any']"]:
        """Return class sub_local_artifact."""
        category = LocalArtifact._validate_category(category)
        return LocalArtifact._local_artifacts_category_to_class[category]

    def _check_existing_file(
        self, path_in_artifact: str, new_file: Path | FileRequest
    ) -> None:
        """Check whether the artifact already contains a given file."""
        all_files = self.files | self.remote_files
        if path_in_artifact in all_files:
            raise ValueError(
                f"File with the same path ({path_in_artifact}) "
                f"is already in the artifact "
                f'("{all_files[path_in_artifact]}" and "{new_file}")'
            )

    def add_local_file(
        self,
        file: Path,
        *,
        artifact_base_dir: Path | None = None,
        override_name: str | None = None,
        content_type: str | None = None,
    ) -> None:
        """
        Add a local file in the artifact.

        :param file: file in the local file system that is added
           to the artifact
        :param artifact_base_dir: base directory of the artifact. Must be
           an absolute path.
           If it's None: file is added in the root of the artifact.
           If it's not None: file is added with the relative path of the
           file with the artifact_base_dir. E.g.
           file=/tmp/artifact/dir1/file1
           artifact_base_dir=/tmp/artifact
           Path of this file in the artifact: dir1/file1
        :param override_name: if not None: use it instead of file.name
        :param content_type: if not None: record this as the
          ``Content-Type`` to be used when serving the file, rather than
          guessing
        :raises ValueError: artifact_base_dir is not absolute or is not
          a directory; file does not exist; the path in the artifact already
          had a file.
        """
        if artifact_base_dir is not None:
            if not artifact_base_dir.is_absolute():
                raise ValueError(f'"{artifact_base_dir}" must be absolute')
            if not artifact_base_dir.is_dir():
                raise ValueError(
                    f'"{artifact_base_dir}" does not exist or '
                    f'is not a directory'
                )

            if not file.is_absolute():
                file = artifact_base_dir.joinpath(file)

            path_in_artifact = file.relative_to(artifact_base_dir).as_posix()
        else:
            path_in_artifact = file.name

        if override_name is not None:
            path_in_artifact = override_name

        if not file.exists():
            raise ValueError(f'"{file}" does not exist')

        if not file.is_file():
            raise ValueError(f'"{file}" is not a file')

        file_absolute = file.absolute()

        self._check_existing_file(path_in_artifact, file_absolute)
        self.files[path_in_artifact] = file_absolute
        if content_type is not None:
            self.content_types[path_in_artifact] = content_type

    def add_remote_file(
        self, path_in_artifact: str, file_request: FileRequest
    ) -> None:
        """
        Add a remote file to the artifact.

        This may be used when a file does not exist locally, but can be
        expected to already exist on the server when uploading the rest of
        the artifact.

        :param path_in_artifact: path of the file in the artifact
        :param file_request: description of the remote file
        """
        self._check_existing_file(path_in_artifact, file_request)
        self.remote_files[path_in_artifact] = file_request

    def validate_model(self) -> None:
        """Raise ValueError with an error if the model is not valid."""
        *_, error = pydantic.validate_model(self.__class__, self.__dict__)

        if error is not None:
            raise ValueError(f"Model validation failed: {error}")

    @classmethod
    def _validate_files_length(
        cls, files: dict[str, Path], number_of_files: int
    ) -> dict[str, Path]:
        """Raise ValueError if number of files is not number_of_files."""
        if (actual_number_files := len(files)) != number_of_files:
            raise ValueError(
                f"Expected number of files: {number_of_files} "
                f"Actual: {actual_number_files}"
            )
        return files

    @classmethod
    def _validate_files_end_in(
        cls, files: dict[str, Path], suffixes: Sequence[str]
    ) -> dict[str, Path]:
        """Raise ValueError if any file does not end in one of suffixes."""
        for file_name in files.keys():
            if not file_name.endswith(tuple(suffixes)):
                raise ValueError(
                    f'Valid file suffixes: {suffixes}. '
                    f'Invalid filename: "{file_name}"'
                )
        return files

    @classmethod
    def _validate_exactly_one_file_ends_in(
        cls, files: dict[str, Path], suffix: str
    ) -> dict[str, Path]:
        """Raise ValueError if files doesn't have exactly 1 file with suffix."""
        changes_files = sum(1 for file in files if file.endswith(suffix))
        if changes_files != 1:
            raise ValueError(
                f"Expecting 1 {suffix} file in {sorted(files.keys())}"
            )
        return files


class WorkRequestDebugLogs(LocalArtifact[data_models.EmptyArtifactData]):
    """
    WorkRequestDebugLogs: help debugging issues executing the task.

    Log files for debusine users in order to debug possible problems in their
    WorkRequests.
    """

    _category = data_models.ArtifactCategory.WORK_REQUEST_DEBUG_LOGS
    data: data_models.EmptyArtifactData = pydantic.Field(
        default_factory=data_models.EmptyArtifactData
    )

    @classmethod
    def create(cls, *, files: Iterable[Path]) -> Self:
        """Return a WorkRequestDebugLogs."""
        artifact = cls(category=cls._category)

        for file in files:
            artifact.add_local_file(
                file, content_type="text/plain; charset=utf-8"
            )

        return artifact


class DebusineTest(LocalArtifact[data_models.EmptyArtifactData]):
    """DebusineTest: noop artifact used in Debusine tests."""

    _category = data_models.ArtifactCategory.TEST
    data: data_models.EmptyArtifactData = pydantic.Field(
        default_factory=data_models.EmptyArtifactData
    )


class PackageBuildLog(LocalArtifact[data_models.DebianPackageBuildLog]):
    """PackageBuildLog: represents a build log file."""

    _category = data_models.ArtifactCategory.PACKAGE_BUILD_LOG

    @classmethod
    def create(
        cls,
        *,
        file: Path,
        source: str,
        version: str,
        architecture: str | None = None,
        bd_uninstallable: data_models.DoseDistCheck | None = None,
    ) -> Self:
        """Return a PackageBuildLog."""
        artifact = cls(
            category=cls._category,
            data=data_models.DebianPackageBuildLog(
                source=source,
                version=version,
                architecture=architecture,
                filename=file.name,
                bd_uninstallable=bd_uninstallable,
            ),
        )

        artifact.add_local_file(file, content_type="text/plain; charset=utf-8")

        return artifact

    @pydantic.validator("files")
    @classmethod
    def validate_files_length_is_one(
        cls, files: dict[str, Path]
    ) -> dict[str, Path]:
        """Validate that artifact has only one file."""
        return super()._validate_files_length(files, 1)

    @pydantic.validator("files")
    @classmethod
    def file_must_end_in_build(cls, files: dict[str, Path]) -> dict[str, Path]:
        """Raise ValueError if the file does not end in .build."""
        return super()._validate_files_end_in(files, [".build"])


@overload
def deb822dict_to_dict(
    element: dict[Any, Any] | deb822.Deb822Dict,
) -> dict[Any, Any]: ...


@overload
def deb822dict_to_dict(element: Any) -> Any: ...


def deb822dict_to_dict(element: Any) -> Any:
    """
    Traverse recursively element converting Deb822Dict to dict.

    deb822.Changes() return a Deb822Dict with some other inner-elements
    being Deb822Dict. This function traverse it and converts any elements
    being a Deb822Dict to a Python dict.

    Reason is to simplify is that json module cannot encode Deb822Dict. To
    simplify let's convert Deb822Dict as soon as possible to dict.
    """
    result: Any
    if isinstance(element, (dict, deb822.Deb822Dict)):
        result = {}
        for key, value in element.items():
            result[key] = deb822dict_to_dict(value)
    elif isinstance(element, list):
        result = []
        for item in element:
            result.append(deb822dict_to_dict(item))
    else:
        result = element

    return result


class Upload(LocalArtifact[data_models.DebianUpload]):
    """Upload: encapsulate a .changes and files listed in it."""

    _category = data_models.ArtifactCategory.UPLOAD

    @classmethod
    def create(
        cls,
        *,
        changes_file: Path,
        exclude_files: frozenset[Path] | set[Path] = frozenset(),
        allow_remote: bool = False,
    ) -> Self:
        """
        Return a Upload. Add the changes_files and files listed in it.

        :param changes_file: a .changes file. Parsed by deb822.Changes.
        :param exclude_files: do not add them in files even if listed in the
          Files section in the changes_file.
        :param allow_remote: if True, allow files referenced by the .changes
          file to be missing locally; uploading this artifact will require
          them to already exist on the server.
        """
        with changes_file.open() as changes_obj:
            data = data_models.DebianUpload(
                type="dpkg",
                changes_fields=deb822dict_to_dict(deb822.Changes(changes_obj)),
            )

        artifact = cls.construct(category=cls._category, data=data)

        artifact.add_local_file(
            changes_file, content_type="text/plain; charset=utf-8"
        )

        # Add any files referenced by .changes (excluding the exclude_files)
        base_directory = changes_file.parent
        for file in data.changes_fields.get("Checksums-Sha256", []):
            path = base_directory / file["name"]

            if path not in exclude_files:
                if allow_remote and not path.exists():
                    artifact.add_remote_file(
                        file["name"],
                        FileRequest(
                            size=file["size"],
                            checksums={"sha256": file["sha256"]},
                            type="file",
                        ),
                    )
                else:
                    artifact.add_local_file(path)

        return artifact

    @pydantic.root_validator(pre=True, allow_reuse=True)
    @classmethod
    def files_contain_changes(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Raise ValueError when files does not have exactly 1 .changes file."""
        cls._validate_exactly_one_file_ends_in(
            values.get("files", {}) | values.get("remote_files", {}),
            ".changes",
        )
        return values

    @pydantic.root_validator(pre=True, allow_reuse=True)
    @classmethod
    def files_contains_files_in_changes(
        cls, values: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Validate that set(files) == set(files_in_changes_file).

        Exception: The .changes file must be in files but not in the .changes
        file.
        """
        files_in_meta_file_match_files(
            ".changes",
            deb822.Changes,
            values.get("files", {}) | values.get("remote_files", {}),
        )
        return values


class SourcePackage(LocalArtifact[data_models.DebianSourcePackage]):
    """SourcePackage: contains source code to be built into BinaryPackages."""

    _category = data_models.ArtifactCategory.SOURCE_PACKAGE

    @classmethod
    def create(cls, *, name: str, version: str, files: list[Path]) -> Self:
        """Return a SourcePackage setting files and data."""
        dsc_fields = {}
        for file in files:
            if file.suffix == ".dsc":
                dsc = utils.read_dsc(file)
                if dsc is None:
                    raise ValueError(f"{file} is not a valid .dsc file")
                dsc_fields = deb822dict_to_dict(dsc)

        data = data_models.DebianSourcePackage(
            name=name,
            version=version,
            type="dpkg",
            dsc_fields=dsc_fields,
        )

        artifact = cls(category=cls._category, data=data)

        for file in files:
            artifact.add_local_file(file)

        return artifact

    @pydantic.validator("files")
    @classmethod
    def files_contain_one_dsc(cls, files: dict[str, Path]) -> dict[str, Path]:
        """Raise ValueError when files does not have exactly 1 .dsc file."""
        return cls._validate_exactly_one_file_ends_in(files, ".dsc")

    @pydantic.validator("files")
    @classmethod
    def files_contains_files_in_dsc(
        cls, files: dict[str, Path]
    ) -> dict[str, Path]:
        """
        Validate that set(files) == set(files_in_dsc_file).

        Exception: The .dsc file must be in files but not in the .dsc file.
        """
        files_in_meta_file_match_files(".dsc", deb822.Dsc, files)
        return files


class BinaryPackage(LocalArtifact[data_models.DebianBinaryPackage]):
    r"""BinaryPackage: encapsulates a single \*.deb / \*.udeb."""

    _category = data_models.ArtifactCategory.BINARY_PACKAGE

    @classmethod
    def create(cls, *, file: Path) -> Self:
        """Return a BinaryPackage setting file and data."""
        pkg = debfile.DebFile(file)
        try:
            control = pkg.control.debcontrol()
            control_files = sorted(
                name.lstrip("./")
                for name in pkg.control
                if name.startswith("./")
            )
        finally:
            pkg.close()
        # Work around Debian #1082838
        srcpkg_name = control.get("Source", control["Package"])
        if (m := re.match(r"^(.*) \((.*)\)$", srcpkg_name)) is not None:
            srcpkg_name, srcpkg_version = m.groups()
        else:
            srcpkg_version = control["Version"]
        data = data_models.DebianBinaryPackage(
            srcpkg_name=srcpkg_name,
            srcpkg_version=srcpkg_version,
            deb_fields=deb822dict_to_dict(control),
            deb_control_files=control_files,
        )

        artifact = cls(category=cls._category, data=data)

        artifact.add_local_file(file)

        return artifact

    @pydantic.validator("files")
    @classmethod
    def files_must_end_in_deb_or_udeb(
        cls, files: dict[str, Path]
    ) -> dict[str, Path]:
        """Raise ValueError if a file does not end in .deb or .udeb."""
        return super()._validate_files_end_in(files, [".deb", ".udeb"])

    @pydantic.validator("files")
    @classmethod
    def files_exactly_one(cls, files: dict[str, Path]) -> dict[str, Path]:
        """Raise ValueError if len(files) != 1."""
        if len(files) != 1:
            raise ValueError("Must have exactly one file")

        return files


class BinaryPackages(LocalArtifact[data_models.DebianBinaryPackages]):
    r"""BinaryPackages: encapsulates a group of \*.deb / \*.udeb."""

    _category = data_models.ArtifactCategory.BINARY_PACKAGES

    @classmethod
    def create(
        cls,
        *,
        srcpkg_name: str,
        srcpkg_version: str,
        version: str,
        architecture: str,
        files: list[Path],
    ) -> Self:
        """Return a BinaryPackages setting files and data."""
        data = data_models.DebianBinaryPackages(
            srcpkg_name=srcpkg_name,
            srcpkg_version=srcpkg_version,
            version=version,
            architecture=architecture,
            # It might be better to get this from the metadata in the
            # package, but that would be rather more effort and isn't
            # currently vital.
            packages=[path.name.split("_", 1)[0] for path in files],
        )

        artifact = cls(category=cls._category, data=data)

        for file in files:
            artifact.add_local_file(file)

        return artifact

    @pydantic.validator("files")
    @classmethod
    def files_must_end_in_deb_or_udeb(
        cls, files: dict[str, Path]
    ) -> dict[str, Path]:
        """Raise ValueError if a file does not end in .deb or .udeb."""
        return super()._validate_files_end_in(files, [".deb", ".udeb"])

    @pydantic.validator("files")
    @classmethod
    def files_more_than_zero(cls, files: dict[str, Path]) -> dict[str, Path]:
        """Raise ValueError if len(files) == 0."""
        if len(files) == 0:
            raise ValueError("Must have at least one file")

        return files


class LintianArtifact(LocalArtifact[data_models.DebianLintian]):
    """LintianArtifact: encapsulate result of the Lintian run."""

    _category = data_models.ArtifactCategory.LINTIAN

    @classmethod
    def create(
        cls,
        analysis: Path,
        lintian_output: Path,
        architecture: str,
        summary: data_models.DebianLintianSummary,
    ) -> Self:
        """Return a LintianArtifact with the files set."""
        data = data_models.DebianLintian(
            architecture=architecture, summary=summary
        )

        artifact = cls(category=cls._category, data=data)

        artifact.add_local_file(analysis, override_name="analysis.json")
        artifact.add_local_file(lintian_output, override_name="lintian.txt")

        return artifact

    @pydantic.validator("files")
    @classmethod
    def _validate_required_files(
        cls, files: dict[str, Path]
    ) -> dict[str, Path]:
        """Artifact contain "analysis.json, "lintian.txt"."""
        # The .create() method already enforces having the correct files
        # But the artifact can be created using debusine.client or a web form,
        # which do not use the .create() of LintianArtifact but the
        # LocalArtifact. That's the reason that is needed to validate
        # that the required files are attached
        required_files = {"analysis.json", "lintian.txt"}
        if files.keys() != required_files:
            raise ValueError(f"Files required: {sorted(required_files)}")

        return files

    @staticmethod
    def _file_is_json_or_raise_value_error(
        file_name: str, files: dict[str, Path]
    ) -> None:
        """Raise ValueError() if file_name in files is not valid JSON."""
        with files[file_name].open() as file:
            try:
                json.load(file)
            except JSONDecodeError as exc:
                raise ValueError(f"{file_name} is not valid JSON: {exc}")

    @pydantic.validator("files")
    @classmethod
    def _validate_file_analysis_is_json(
        cls, files: dict[str, Path]
    ) -> dict[str, Path]:
        """Validate that "analysis.json" is valid JSON."""
        cls._file_is_json_or_raise_value_error("analysis.json", files)
        return files


class AutopkgtestArtifact(LocalArtifact[data_models.DebianAutopkgtest]):
    """Autopkgtest: encapsulate result of the Autopkgtest run."""

    _category = data_models.ArtifactCategory.AUTOPKGTEST

    @classmethod
    def create(
        cls, artifact_directory: Path, data: data_models.DebianAutopkgtest
    ) -> Self:
        """Return AutopkgtestArtifact with the files and data set."""
        artifact = cls(category=cls._category, data=data)

        for file in artifact_directory.rglob("*"):
            if not file.is_file():
                # Only add files
                continue

            if file.is_relative_to(artifact_directory / "binaries"):
                # Skip binaries/
                continue

            artifact.add_local_file(file, artifact_base_dir=artifact_directory)

        return artifact


class DebianSystemTarballArtifact(
    LocalArtifact[data_models.DebianSystemTarball]
):
    """
    Contain system.tar.xz file with a Debian.

    Can be used by a chroot, container, etc.
    """

    _category = data_models.ArtifactCategory.SYSTEM_TARBALL

    @classmethod
    def create(cls, tarball: Path, data: dict[str, Any]) -> Self:
        """Return a DebianSystemTarballArtifact with the tarball file."""
        data = data.copy()
        data["filename"] = tarball.name
        artifact = cls(
            category=cls._category, data=data_models.DebianSystemTarball(**data)
        )

        artifact.add_local_file(tarball)

        return artifact

    @pydantic.validator("files")
    @classmethod
    def _validate_file_name_ends_in_tar_xz(
        cls, files: dict[str, Path]
    ) -> dict[str, Path]:
        """Check if the artifact contains only one file and it is a .tar.xz."""
        if not len(files) == 1:
            raise ValueError(
                "DebianSystemTarballArtifact does not contain exactly one file"
            )

        if not (name := next(iter(files.keys()))).endswith(".tar.xz"):
            raise ValueError(f"Invalid file name: '{name}'. Expected .tar.xz")

        return files


class BlhcArtifact(LocalArtifact[data_models.EmptyArtifactData]):
    """BlhcArtifact: encapsulate result of the blhc run."""

    _category = data_models.ArtifactCategory.BLHC
    data: data_models.EmptyArtifactData = pydantic.Field(
        default_factory=data_models.EmptyArtifactData
    )

    @classmethod
    def create(cls, blhc_output: Path) -> Self:
        """Return a BlhcArtifact with the files set."""
        artifact = cls(category=cls._category)

        artifact.add_local_file(blhc_output, override_name="blhc.txt")

        return artifact


class DebDiffArtifact(LocalArtifact[data_models.DebDiff]):
    """DebDiffArtifact: encapsulate result of the debdiff run."""

    _category = data_models.ArtifactCategory.DEBDIFF

    @classmethod
    def create(cls, debdiff_output: Path, original: str, new: str) -> Self:
        """Return a DebDiffArtifact with the files set."""
        data = data_models.DebDiff(original=original, new=new)

        artifact = cls(category=cls._category, data=data)

        artifact.add_local_file(debdiff_output, override_name="debdiff.txt")

        return artifact


class DebianSystemImageArtifact(LocalArtifact[data_models.DebianSystemImage]):
    """
    Contains a image.tar.xz file with a bootable Debian system.

    Can be used by a VM.
    """

    _category = data_models.ArtifactCategory.SYSTEM_IMAGE

    @classmethod
    def create(cls, image: Path, data: dict[str, Any]) -> Self:
        """Return a DebianSystemImageArtifact with the image file."""
        data = data.copy()
        data["filename"] = image.name
        artifact = cls(
            category=cls._category, data=data_models.DebianSystemImage(**data)
        )

        artifact.add_local_file(image)

        return artifact

    @pydantic.validator("files")
    @classmethod
    def _validate_files(cls, files: dict[str, Path]) -> dict[str, Path]:
        """Check if the artifact contains only one file and it's ending."""
        if not len(files) == 1:
            raise ValueError(
                "DebianSystemImageArtifact does not contain exactly one file"
            )

        name = next(iter(files.keys()))
        if not name.endswith(".tar.xz") and not name.endswith(".qcow2"):
            raise ValueError(
                f"Invalid file name: '{name}'. Expected .tar.xz or qcow2"
            )

        return files


class SigningInputArtifact(LocalArtifact[data_models.DebusineSigningInput]):
    """Input to a Sign task."""

    _category = data_models.ArtifactCategory.SIGNING_INPUT

    @classmethod
    def create(
        cls,
        files: Iterable[Path],
        base_dir: Path,
        trusted_certs: list[str] | None = None,
        binary_package_name: str | None = None,
    ) -> Self:
        """Return a new SigningInputArtifact."""
        data = data_models.DebusineSigningInput(
            trusted_certs=(
                None if trusted_certs is None else list(trusted_certs)
            ),
            binary_package_name=binary_package_name,
        )
        artifact = cls(category=cls._category, data=data)
        for file in files:
            artifact.add_local_file(
                file,
                artifact_base_dir=base_dir,
                # The content-type here doesn't matter, but we don't want to
                # involve libmagic in a potentially-sensitive signing
                # workflow.
                content_type="application/octet-stream",
            )
        return artifact

    @pydantic.validator("files")
    @classmethod
    def validate_at_least_one_file(
        cls, files: dict[str, Path]
    ) -> dict[str, Path]:
        """Validate that artifact has at least one file."""
        if not files:
            raise ValueError("Expected at least one file")
        return files


class SigningOutputArtifact(LocalArtifact[data_models.DebusineSigningOutput]):
    """Output of a Sign task."""

    _category = data_models.ArtifactCategory.SIGNING_OUTPUT

    @classmethod
    def create(
        cls,
        purpose: KeyPurpose,
        fingerprint: str,
        results: Iterable[data_models.SigningResult],
        files: Iterable[Path],
        base_dir: Path,
        binary_package_name: str | None = None,
    ) -> Self:
        """Return a new SigningOutputArtifact."""
        data = data_models.DebusineSigningOutput(
            purpose=purpose,
            fingerprint=fingerprint,
            results=list(results),
            binary_package_name=binary_package_name,
        )
        artifact = cls(category=cls._category, data=data)
        for file in files:
            artifact.add_local_file(
                file,
                artifact_base_dir=base_dir,
                # The content-type here doesn't matter, but we don't want to
                # involve libmagic in a potentially-sensitive signing
                # workflow.
                content_type="application/octet-stream",
            )
        return artifact


class RepositoryIndex(LocalArtifact[data_models.DebianRepositoryIndex]):
    """An index file in a repository."""

    _category = data_models.ArtifactCategory.REPOSITORY_INDEX

    @classmethod
    def create(cls, *, file: Path, path: str) -> Self:
        """Return a RepositoryIndex."""
        data = data_models.DebianRepositoryIndex(path=path)
        artifact = cls(category=cls._category, data=data)
        artifact.add_local_file(file, override_name=PurePath(path).name)
        return artifact
