# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common test-helper code involving Tasks."""
import datetime
import hashlib
import shlex
import tarfile
from collections.abc import Collection as AbcCollection
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from typing import Any, Generic, Protocol, TypeVar, cast, overload
from unittest import mock
from unittest.mock import MagicMock

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.client.debusine import Debusine
from debusine.client.models import (
    ArtifactResponse,
    FileResponse,
    FilesResponseType,
    RelationType,
    StrMaxLength255,
)
from debusine.tasks import BaseExternalTask, BaseTask, BaseTaskWithExecutor
from debusine.tasks.executors import ExecutorInterface, UnshareInstance
from debusine.tasks.models import (
    BaseDynamicTaskData,
    BaseDynamicTaskDataWithExecutor,
    BaseTaskData,
    BaseTaskDataWithExecutor,
    LookupMultiple,
    LookupSingle,
)
from debusine.tasks.server import (
    ArtifactInfo,
    CollectionInfo,
    MultipleArtifactInfo,
    TaskDatabaseInterface,
)
from debusine.test.base import TestCase
from debusine.test.test_utils import create_artifact_response
from debusine.utils import extract_generic_type_arguments

TaskClass = TypeVar("TaskClass", bound=BaseTask[Any, Any])
ExternalTaskClass = TypeVar(
    "ExternalTaskClass", bound=BaseExternalTask[Any, Any]
)


class TaskTestProtocol(Protocol, Generic[TaskClass]):
    """Attributes that tests using :py:class:`TaskHelperMixin` must provide."""

    SAMPLE_TASK_DATA: dict[str, Any]
    task_class: type[TaskClass]
    task: TaskClass


class FakeTaskDatabaseBase(TaskDatabaseInterface):
    """Fake implementation of database interaction in worker tests."""

    def __init__(
        self,
        *,
        single_lookups: (
            Mapping[
                tuple[LookupSingle, CollectionCategory | None],
                ArtifactInfo | None,
            ]
            | None
        ) = None,
        multiple_lookups: (
            Mapping[
                tuple[LookupMultiple, CollectionCategory | None],
                list[ArtifactInfo],
            ]
            | None
        ) = None,
        relations: (
            Mapping[
                tuple[Sequence[int], ArtifactCategory, RelationType],
                list[ArtifactInfo],
            ]
            | None
        ) = None,
        single_collection_lookups: (
            Mapping[
                tuple[LookupSingle, CollectionCategory | None],
                CollectionInfo | None,
            ]
            | None
        ) = None,
        settings: dict[str, str] | None = None,
    ) -> None:
        """Construct a :py:class:`FakeTaskDatabase`."""
        self.single_lookups = single_lookups or {}
        self.multiple_lookups = multiple_lookups or {}
        self.relations = relations or {}
        self.single_collection_lookups = single_collection_lookups or {}
        self.settings = settings or {}

    @overload
    def lookup_single_artifact(
        self,
        lookup: LookupSingle,
        default_category: CollectionCategory | None = None,
    ) -> ArtifactInfo: ...

    @overload
    def lookup_single_artifact(
        self, lookup: None, default_category: CollectionCategory | None = None
    ) -> None: ...

    def lookup_single_artifact(
        self,
        lookup: LookupSingle | None,
        default_category: CollectionCategory | None = None,
    ) -> ArtifactInfo | None:
        """Pretend to look up a single artifact."""
        return (
            None
            if lookup is None
            else self.single_lookups[(lookup, default_category)]
        )

    def lookup_multiple_artifacts(
        self,
        lookup: LookupMultiple | None,
        default_category: CollectionCategory | None = None,
    ) -> MultipleArtifactInfo:
        """Pretend to look up multiple artifacts."""
        if not lookup:
            return MultipleArtifactInfo()
        return MultipleArtifactInfo(
            self.multiple_lookups[(lookup, default_category)]
        )

    def find_related_artifacts(
        self,
        artifact_ids: AbcCollection[int],
        target_category: ArtifactCategory,
        relation_type: RelationType = RelationType.RELATES_TO,
    ) -> MultipleArtifactInfo:
        """Pretend to find artifacts via relations."""
        return MultipleArtifactInfo(
            self.relations[
                (tuple(artifact_ids), target_category, relation_type)
            ]
        )

    @overload
    def lookup_single_collection(
        self,
        lookup: LookupSingle,
        default_category: CollectionCategory | None = None,
    ) -> CollectionInfo: ...

    @overload
    def lookup_single_collection(
        self, lookup: None, default_category: CollectionCategory | None = None
    ) -> None: ...

    def lookup_single_collection(
        self,
        lookup: LookupSingle | None,
        default_category: CollectionCategory | None = None,
    ) -> CollectionInfo | None:
        """Pretend to look up a single collection."""
        return (
            None
            if lookup is None
            else self.single_collection_lookups[(lookup, default_category)]
        )

    def get_server_setting(self, setting: str) -> str:
        """Look up a Django setting (strings only)."""
        return self.settings[setting]


class FakeTaskDatabase(FakeTaskDatabaseBase):
    """
    Fake task database with empty configure implementation.

    This is used for tests that do not perform database access
    """

    def configure(self, task: "BaseTask[Any, Any]") -> None:
        """Perform server-side task configuration."""
        raise NotImplementedError()


class TaskHelperMixin(Generic[TaskClass]):
    """Helper mixin for Task tests."""

    task_class: type[TaskClass]
    task: TaskClass

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Compute cls.task_class."""
        super().__init_subclass__(**kwargs)

        # The task class used for this test suite, computed by introspecting
        # the type argument used to specialize this generic class.
        [cls.task_class] = extract_generic_type_arguments(cls, TaskHelperMixin)

    def configure_task(
        self: TaskTestProtocol[TaskClass],
        task_data: dict[str, Any] | None = None,
        override: dict[str, Any] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        """
        Run self.task.configure(task_data) with different inputs.

        Copy self.SAMPLE_TASK_DATA or task_data and modify it, call
        self.task.configure(modified_SAMPLE_TASK_DATA).

        :param task_data: if provided use this as a base dictionary. If None,
          use self.SAMPLE_TASK_DATA.
        :param override: dictionary with the keys to modify (only root keys,
          not recursive search) by its values. Modify or add them.
        :param remove: list of keys to remove.
        """
        if task_data is None:  # pragma: no cover
            task_data = self.SAMPLE_TASK_DATA.copy()
        if override:
            for key, value in override.items():
                task_data[key] = value
        if remove:
            for key in remove:
                task_data.pop(key, None)

        self.task = self.task_class(task_data)

    def write_os_release(
        self, filename: Path, codename: str = "bookworm", version: int = 12
    ) -> dict[str, str]:
        """
        Write in directory / self.task._OS_RELEASE_FILE a release file.

        :return: dictionary with keys to values.
        """
        data = {
            "ID": "debian",
            "NAME": "Debian GNU/Linux",
            "PRETTY_NAME": f"Debian GNU/Linux {version} ({codename})",
            "VERSION": f"{version} ({codename})",
            "VERSION_CODENAME": codename,
            "VERSION_ID": str(version),
        }

        if codename == "jessie":
            del data["VERSION_CODENAME"]
        if version >= 13:
            del data["VERSION"]
            del data["VERSION_ID"]

        filename.write_text(
            "\n".join(f"{k}={shlex.quote(v)}" for k, v in data.items()) + "\n"
        )
        return data


class ExternalTaskHelperMixin(
    TaskHelperMixin[ExternalTaskClass], Generic[ExternalTaskClass]
):
    """Helper mixin for testing Tasks that run on external workers."""

    task_class: type[ExternalTaskClass]
    task: ExternalTaskClass

    def mock_debusine(self: TaskTestProtocol[ExternalTaskClass]) -> MagicMock:
        """Create a Debusine mock and configure self.task for it. Return it."""
        debusine_mock = mock.create_autospec(spec=Debusine)
        assert isinstance(debusine_mock, MagicMock)

        # Worker would set the server
        self.task.configure_server_access(debusine_mock)

        return debusine_mock

    def mock_image_download(
        self,
        debusine_mock: MagicMock,
        create_image: bool = False,
        system_image: bool = False,
    ) -> ArtifactResponse:
        """
        Configure a fake ImageCache path and fake_system_*_artifact.

        The tarball itself isn't faked, unless create_image is set.

        If system_image is set, a system-image is faked instead of a tarball.
        """
        if system_image:
            artifact_response = self.fake_system_image_artifact()
        else:
            artifact_response = self.fake_system_tarball_artifact()
        debusine_mock.artifact_get.return_value = artifact_response

        def download_tarball(
            artifact_id: int,  # noqa: U100
            filename: str,  # noqa: U100
            destination: Path,
        ) -> None:
            with tarfile.open(destination, "w:xz") as f:
                tarinfo = tarfile.TarInfo("./")
                tarinfo.type = tarfile.DIRTYPE
                f.addfile(tarinfo)
                tarinfo = tarfile.TarInfo("./sbin")
                tarinfo.type = tarfile.DIRTYPE
                f.addfile(tarinfo)
                tarinfo = tarfile.TarInfo("./sbin/init")
                tarinfo.size = 3
                f.addfile(tarinfo, BytesIO(b"123"))

        def download_image(
            artifact_id: int,  # noqa: U100
            filename: str,  # noqa: U100
            destination: Path,
        ) -> None:
            with destination.open("wb") as f:
                f.write(b"qcow!")

        self.image_cache_path = Path(
            mkdtemp(prefix="debusine-testsuite-images-")
        )
        cast(TestCase, self).addCleanup(rmtree, self.image_cache_path)

        img_cache_patcher = mock.patch(
            "debusine.tasks.executors.images.ImageCache.image_cache_path",
            self.image_cache_path,
        )
        img_cache_patcher.start()
        cast(TestCase, self).addCleanup(img_cache_patcher.stop)

        patcher = mock.patch(
            "debusine.tasks.executors.images.ImageCache"
            "._download_image_artifact"
        )
        self.download_image_artifact_mock = patcher.start()
        cast(TestCase, self).addCleanup(patcher.stop)

        if create_image:
            if system_image:
                self.download_image_artifact_mock.side_effect = download_image
            else:
                self.download_image_artifact_mock.side_effect = download_tarball

        return artifact_response

    def patch_prepare_executor_instance(self) -> MagicMock:
        """
        Patch self.task._prepare_executor_instance(), return its mock.

        Side effect of self.task._prepare_executor_instance(): set
        self.task.executor and self.task.executor_instance.
        """
        patcher = mock.patch.object(
            self.task, "_prepare_executor_instance", autospec=True
        )
        mocked = patcher.start()

        def mock_executor_executor_instance() -> None:
            self.task.executor = MagicMock(spec=ExecutorInterface)
            self.task.executor_instance = MagicMock(spec=UnshareInstance)
            self.task.executor_instance.run.return_value.returncode = 0

        mocked.side_effect = mock_executor_executor_instance
        cast(TestCase, self).addCleanup(patcher.stop)
        return mocked

    def fake_system_tarball_artifact(self) -> ArtifactResponse:
        """Create a fake ArtifactResponse for a debian:system-tarball."""
        return create_artifact_response(
            id=42,
            workspace="Testing",
            category=ArtifactCategory.SYSTEM_TARBALL,
            created_at=datetime.datetime(
                2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC
            ),
            data={
                "architecture": "amd64",
                "codename": "bookworm",
                "filename": "system.tar.xz",
                "vendor": "debian",
                "pkglist": {},
            },
            download_tar_gz_url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://example.com/download-42/"
            ),
            files=FilesResponseType(
                {
                    "system.tar.xz": FileResponse(
                        size=4001,
                        checksums={
                            "sha256": pydantic.parse_obj_as(
                                StrMaxLength255, "abc123"
                            )
                        },
                        type="file",
                        url=pydantic.parse_obj_as(
                            pydantic.AnyUrl,
                            "https://example.com/download-system.tar.xz",
                        ),
                    ),
                }
            ),
            files_to_upload=[],
        )

    def fake_system_image_artifact(self) -> ArtifactResponse:
        """Create a fake ArtifactResponse for a debian:system-image."""
        return create_artifact_response(
            id=42,
            workspace="Testing",
            category=ArtifactCategory.SYSTEM_IMAGE,
            created_at=datetime.datetime(
                2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC
            ),
            data={
                "architecture": "amd64",
                "codename": "bookworm",
                "filename": "image.qcow2",
                "vendor": "debian",
                "image_format": "qcow2",
                "filesystem": "ext4",
                "size": 2 * 1024 * 1024 * 1024,
                "boot_mechanism": "efi",
                "pkglist": {},
            },
            download_tar_gz_url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://example.com/download-42/"
            ),
            files=FilesResponseType(
                {
                    "image.qcow2": FileResponse(
                        size=4001,
                        checksums={
                            "sha256": pydantic.parse_obj_as(
                                StrMaxLength255, "abc123"
                            )
                        },
                        type="file",
                        url=pydantic.parse_obj_as(
                            pydantic.AnyUrl,
                            "https://example.com/download-image.qcow2",
                        ),
                    ),
                }
            ),
            files_to_upload=[],
        )

    def fake_debian_source_package_artifact(self) -> ArtifactResponse:
        """Create a fake ArtifactResponse for a debian:source-package."""
        return create_artifact_response(
            id=6,
            workspace="Testing",
            category=ArtifactCategory.SOURCE_PACKAGE,
            created_at=datetime.datetime(
                2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC
            ),
            data={
                "name": "foo",
                "version": "1.0-1",
                "type": "dpkg",
                "dsc_fields": {},
            },
            download_tar_gz_url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://example.com/download-6/"
            ),
            files=FilesResponseType(
                {
                    "foo_1.0-1.dsc": FileResponse(
                        size=10,
                        checksums={
                            "sha256": pydantic.parse_obj_as(
                                StrMaxLength255, "abc123"
                            )
                        },
                        type="file",
                        url=pydantic.parse_obj_as(
                            pydantic.AnyUrl,
                            "https://example.com/foo_1.0-1.dsc",
                        ),
                    ),
                }
            ),
            files_to_upload=[],
        )

    def fake_debian_binary_package_artifact(self) -> ArtifactResponse:
        """Create a fake ArtifactResponse for a debian:binary-package."""
        return create_artifact_response(
            id=7,
            workspace="Testing",
            category=ArtifactCategory.BINARY_PACKAGE,
            created_at=datetime.datetime(
                2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC
            ),
            data={
                "srcpkg_name": "foo",
                "srcpkg_version": "1.0-1",
                "type": "dpkg",
                "dsc_fields": {},
                "dsc_control_files": [],
            },
            download_tar_gz_url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://example.com/download-7/"
            ),
            files=FilesResponseType(
                {
                    "foo_1.0-1_all.deb": FileResponse(
                        size=10,
                        checksums={
                            "sha256": pydantic.parse_obj_as(
                                StrMaxLength255, "abc123"
                            )
                        },
                        type="file",
                        url=pydantic.parse_obj_as(
                            pydantic.AnyUrl,
                            "https://example.com/foo_1.0-1_all.deb",
                        ),
                    ),
                }
            ),
            files_to_upload=[],
        )

    def fake_debian_upload_artifact(
        self,
        source_package_name: str = "foo",
        architecture: str = "source",
        names: list[str] | None = None,
    ) -> ArtifactResponse:
        """Create a fake ArtifactResponse for a debian:upload."""
        if names is None:
            names = [
                "{source_package_name}_1.0.orig.tar.xz",
                "{source_package_name}_1.0-1.debian.tar.xz",
                "{source_package_name}_1.0-1.dsc",
            ]
        return create_artifact_response(
            id=8,
            workspace="Testing",
            category=ArtifactCategory.UPLOAD,
            created_at=datetime.datetime(
                2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC
            ),
            data={
                "type": "dpkg",
                "changes_fields": {
                    "Architecture": architecture,
                    "Files": [{"name": name} for name in names],
                },
            },
            download_tar_gz_url=pydantic.parse_obj_as(
                pydantic.AnyUrl, "https://example.com/download-8/"
            ),
            files=FilesResponseType(
                {
                    name: FileResponse(
                        size=len(name),
                        checksums={
                            "sha256": pydantic.parse_obj_as(
                                StrMaxLength255,
                                hashlib.sha256(name.encode()).hexdigest(),
                            )
                        },
                        type="file",
                        url=pydantic.parse_obj_as(
                            pydantic.AnyUrl, f"https://example.com/{name}"
                        ),
                    )
                    for name in [
                        f"{source_package_name}_1.0-1_{architecture}.changes",
                        *names,
                    ]
                }
            ),
            files_to_upload=[],
        )


TD = TypeVar("TD", bound=BaseTaskData)
DTD = TypeVar("DTD", bound=BaseDynamicTaskData)


class SampleBaseTask(BaseTask[TD, DTD]):
    """Common test implementation of abstract task methods."""

    def get_label(self) -> str:
        """Return the task label."""
        return "test"

    def build_dynamic_data(
        self,
        task_database: TaskDatabaseInterface,  # noqa: U100
    ) -> DTD:
        """Resolve artifact lookups for this task."""
        return self.dynamic_task_data_type()

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of input artifact IDs used by this task."""
        return []


class SampleBaseExternalTask(
    SampleBaseTask[TD, DTD], BaseExternalTask[TD, DTD]
):
    """Common test implementation of BaseExternalTask methods."""


TDE = TypeVar("TDE", bound=BaseTaskDataWithExecutor)
DTDE = TypeVar("DTDE", bound=BaseDynamicTaskDataWithExecutor)


class SampleBaseTaskWithExecutor(
    SampleBaseTask[TDE, DTDE], BaseTaskWithExecutor[TDE, DTDE]
):
    """Common test implementation of BaseExternalTask methods."""
