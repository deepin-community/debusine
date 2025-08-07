# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Models used for file backend configuration."""

try:
    import pydantic.v1 as pydantic
except ImportError:
    import pydantic as pydantic  # type: ignore


class FileBackendConfiguration(pydantic.BaseModel):
    """Base class for file backend configuration."""

    class Config:
        """Set up stricter pydantic Config."""

        validate_assignment = True
        extra = pydantic.Extra.forbid


class LocalFileBackendConfiguration(FileBackendConfiguration):
    """Configuration for `LocalFileBackend`."""

    base_directory: str | None = None


class ExternalDebianSuiteBackendConfiguration(FileBackendConfiguration):
    """Configuration for `ExternalDebianSuiteFileBackend`."""

    archive_root_url: str
    suite: str
    components: list[str]


class MemoryFileBackendConfiguration(FileBackendConfiguration):
    """Configuration for `MemoryFileBackend`."""

    name: str


class S3FileBackendConfiguration(FileBackendConfiguration):
    """Configuration for `S3FileBackend`."""

    bucket_name: str
    # https://docs.aws.amazon.com/AmazonS3/latest/userguide/storage-class-intro.html
    storage_class: str | None = None
