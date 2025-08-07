# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Image handling for executor backends."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from shutil import rmtree

from fasteners import InterProcessLock  # type: ignore

from debusine.client.debusine import Debusine
from debusine.client.models import ArtifactResponse
from debusine.tasks.executors.base import ExecutorImageCategory, _backends
from debusine.tasks.models import ImageCacheUsageLog, ImageCacheUsageLogEntry

CACHE_SIZE = 10
USAGE_LOG = "usage.json"

log = logging.getLogger(__name__)


class ImageCache:
    """
    Download image artifacts to a local cache.

    Processed versions of an image may be stored for a backend.
    """

    image_cache_path: Path = Path.home() / "system-images"
    image_category: ExecutorImageCategory
    backend: str | None = None

    def __init__(
        self, debusine_api: Debusine, image_category: ExecutorImageCategory
    ):
        """
        Instantiate an ImageCache.

        :param debusine_api: object to use the debusine client API
        :param image_category: artifact category of supported images
        """
        self.debusine_api = debusine_api
        self.image_category = image_category

    def cache_artifact_path(self, artifact_id: int) -> Path:
        """Return the path to the image cache pool for artifact_id."""
        return self.image_cache_path / str(artifact_id)

    def cache_artifact_image_path(
        self, artifact_id: int, filename: str, backend: str | None = None
    ) -> Path:
        """Return the path to the cached image for filename, for backend."""
        cache_path = self.cache_artifact_path(artifact_id)
        if backend:
            cache_path = cache_path / "backends" / backend
        return cache_path / filename

    def image_artifact(self, artifact_id: int) -> ArtifactResponse:
        """Retrieve an Artifact."""
        r = self.debusine_api.artifact_get(artifact_id)
        if r.category != self.image_category.value:
            raise ValueError(
                f"Unexpected artifact type {r.category}; expected "
                f"{self.image_category}"
            )
        return r

    def _image_lock(self, artifact_id: int) -> InterProcessLock:
        """Context Manager to hold a RW lock for working on a cached image."""
        cache_path = self.cache_artifact_path(artifact_id)
        cache_path.mkdir(exist_ok=True)
        return InterProcessLock(cache_path / ".lock")

    def process_downloaded_image(self, artifact: ArtifactResponse) -> None:
        """
        Handle any necessary post-download processing of the image.

        Locate the downloaded image using cache_artifact_image_path() and store
        it for a backend, if the result needs to be stored in the cache.

        This only needs to be done once, after the image is first downloaded.
        """
        pass

    def image_in_cache(self, artifact: ArtifactResponse) -> bool:
        """Return whether or not artifact is available in the local cache."""
        return self.cache_artifact_image_path(
            artifact.id, artifact.data["filename"]
        ).exists()

    def _download_image_artifact(
        self, artifact_id: int, filename: str, destination: Path
    ) -> None:
        """Atomically download filename from artifact to destination."""
        temp_path = destination.parent / (".download.tmp." + destination.name)
        temp_path.unlink(missing_ok=True)
        self.debusine_api.download_artifact_file(
            artifact_id, path_in_artifact=filename, destination=temp_path
        )
        temp_path.rename(destination)

    def download_image(self, artifact: ArtifactResponse) -> Path:
        """
        Make the image available locally.

        Fetch the image from artifact storage, if it isn't already available,
        and make it available locally.

        Return a path to the image or name, as appropriate for the backend.
        """
        self.image_cache_path.mkdir(exist_ok=True)

        filename = artifact.data["filename"]

        # Protect our image-cache directory structure
        if filename.startswith(".") or filename in ("backends", USAGE_LOG):
            raise ValueError("Image has an invalid filename")

        path = self.cache_artifact_image_path(artifact.id, filename)

        with self._image_lock(artifact.id):
            if not self.image_in_cache(artifact):
                if not path.exists():
                    self._download_image_artifact(artifact.id, filename, path)
                self.process_downloaded_image(artifact)
            self.record_image_use(artifact)
        self.clean_up_old_images()
        return path

    def record_image_use(self, artifact: ArtifactResponse) -> None:
        """
        Record the use of an image.

        Executed while the image lock is held.
        """
        record_file = self.cache_artifact_image_path(artifact.id, USAGE_LOG)
        if record_file.exists():
            with record_file.open("r") as f:
                record = ImageCacheUsageLog.parse_raw(f.read())
        else:
            record = ImageCacheUsageLog()

        if self.backend:
            record.backends.add(self.backend)

        record.usage = record.usage[-CACHE_SIZE + 1 :]
        record.usage.append(
            ImageCacheUsageLogEntry(
                filename=artifact.data["filename"],
                backend=self.backend,
                timestamp=datetime.now(timezone.utc),
            )
        )

        tmp_file = self.cache_artifact_image_path(
            artifact.id, f".tmp.{USAGE_LOG}"
        )
        with tmp_file.open("w") as f:
            f.write(record.json())
        tmp_file.replace(record_file)

    def clean_up_old_images(self) -> None:
        """
        Purge old images from the cache.

        This implements simple LRU eviction, evicting all backend versions at
        once.
        """
        for artifact_id, backends in self._find_least_used_images():
            with self._image_lock(artifact_id):
                for backend in backends:
                    try:
                        backend_cls = _backends[backend]
                        if hasattr(backend_cls, "clean_up_image"):
                            backend_cls.clean_up_image(artifact_id)
                    except Exception:
                        # Clean-up is in a critical code path, don't fail, just
                        # log errors. If the log gets corrupted, we'll attempt
                        # a full clean-up including backends that may not exist.
                        log.warning(
                            "Failed to clean up %i backend %s",
                            artifact_id,
                            backend,
                            exc_info=True,
                        )
                rmtree(self.cache_artifact_path(artifact_id))

    def _find_least_used_images(self) -> list[tuple[int, set[str]]]:
        """
        Find the least recently used images in the cache.

        Return a list of (artifact_id, [backends]) tuples.
        """
        last_used: dict[int, datetime] = {}
        backends: dict[int, set[str]] = {}
        unknown: list[int] = []

        for path in self.image_cache_path.iterdir():
            if not path.name.isdigit():
                continue
            artifact_id = int(path.name)
            with self._image_lock(artifact_id):
                record_file = path / USAGE_LOG
                if not record_file.exists():
                    log.warning("No usage log for artifact %i", artifact_id)
                    unknown.append(artifact_id)
                    continue
                try:
                    with record_file.open("r") as f:
                        record = ImageCacheUsageLog.parse_raw(f.read())
                except Exception:
                    log.warning(
                        "Failed to parse log for artifact %i", artifact_id
                    )
                    unknown.append(artifact_id)
                    continue
                last_used[artifact_id] = record.usage[-1].timestamp
                backends[artifact_id] = record.backends

        most_recently_used = sorted(
            last_used.items(), key=lambda item: item[1], reverse=True
        )

        least_recently_used = []

        for artifact_id, timestamp in most_recently_used[CACHE_SIZE:]:
            least_recently_used.append((artifact_id, backends[artifact_id]))

        for artifact_id in unknown:
            # Assume the worst
            least_recently_used.append((artifact_id, set(_backends.keys())))

        return least_recently_used
