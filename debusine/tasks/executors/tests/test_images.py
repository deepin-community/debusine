# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the ImageCache class."""

import json
from datetime import datetime, timezone
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from unittest.mock import MagicMock, create_autospec, patch

from debusine.artifacts.models import ArtifactCategory
from debusine.client.debusine import Debusine
from debusine.tasks import Noop
from debusine.tasks.executors.base import (
    ExecutorImageCategory,
    ExecutorInterface,
    _backends,
)
from debusine.tasks.executors.images import CACHE_SIZE, ImageCache
from debusine.tasks.executors.images import log as images_log
from debusine.tasks.tests.helper_mixin import ExternalTaskHelperMixin
from debusine.test import TestCase


class ImageCacheTestMixin(ExternalTaskHelperMixin[Noop]):
    """Common setup for unit tests of ImageCache class."""

    def setUp(self) -> None:
        """Mock the Debusine API for tests."""
        self.debusine_api = create_autospec(Debusine)
        self.image_cache = ImageCache(
            self.debusine_api, ExecutorImageCategory.TARBALL
        )
        self.artifact = self.fake_system_tarball_artifact()
        self.debusine_api.artifact_get.return_value = self.artifact


class ImageCacheMetadataTests(ImageCacheTestMixin, TestCase):
    """Unit tests for ImageCache class that don't work with images."""

    def test_cache_artifact_path(self) -> None:
        """Test that cache_artifact_path builds the correct path."""
        self.assertEqual(
            self.image_cache.cache_artifact_path(42),
            self.image_cache.image_cache_path / "42",
        )

    def test_cache_artifact_image_path(self) -> None:
        """Test that cache_artifact_image_path builds the correct base path."""
        self.assertEqual(
            self.image_cache.cache_artifact_image_path(42, "foo.img"),
            self.image_cache.image_cache_path / "42" / "foo.img",
        )

    def test_cache_artifact_image_path_for_backend(self) -> None:
        """Test that cache_artifact_image_path builds a correct backend path."""
        self.assertEqual(
            self.image_cache.cache_artifact_image_path(
                42, "foo.img", backend="bar"
            ),
            self.image_cache.image_cache_path
            / "42"
            / "backends"
            / "bar"
            / "foo.img",
        )

    def test_image_artifact(self) -> None:
        """Test that image_metadata fetches the Artifact."""
        self.assertEqual(self.image_cache.image_artifact(42), self.artifact)

        self.debusine_api.artifact_get.assert_called_once_with(42)

    def test_image_artifact_incorrect_category(self) -> None:
        """Test that image_artifact rejects the wrong kind of artifact."""
        self.artifact.category = ArtifactCategory.SYSTEM_IMAGE

        with self.assertRaisesRegex(
            ValueError,
            r"^Unexpected artifact type debian:system-image; expected "
            r"debian:system-tarball$",
        ):
            self.image_cache.image_artifact(42)


class ImageCacheTests(ImageCacheTestMixin, TestCase):
    """Unit tests for ImageCache class that work with images."""

    def setUp(self) -> None:
        """Mock the cache directory for tests."""
        super().setUp()
        self.cache_path = Path(mkdtemp(prefix="debusine-testsuite-images-"))
        self.addCleanup(rmtree, self.cache_path)
        self.image_cache.image_cache_path = self.cache_path
        patcher = patch.object(self.image_cache, 'process_downloaded_image')
        self.process_downloaded_image_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def patch_download_image_artifact(self) -> None:
        """Mock ImageCache._download_image_artifact()."""
        patcher = patch.object(self.image_cache, "_download_image_artifact")
        self._download_image_artifact_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def test_download_image_uncached(self) -> None:
        """Test that download_image downloads images when not cached."""
        self.patch_download_image_artifact()
        image = self.image_cache.download_image(self.artifact)

        self.assertEqual(image, self.cache_path / "42" / "system.tar.xz")
        self._download_image_artifact_mock.assert_called_with(
            42, "system.tar.xz", self.cache_path / "42" / "system.tar.xz"
        )
        self.process_downloaded_image_mock.assert_called_with(self.artifact)

    def test_download_image_cached(self) -> None:
        """Test that download_image uses cached images."""
        self.patch_download_image_artifact()
        image_path = self.cache_path / "42" / "system.tar.xz"
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"Fake Data")
        self.assertEqual(
            self.image_cache.download_image(self.artifact), image_path
        )
        self._download_image_artifact_mock.assert_not_called()
        self.process_downloaded_image_mock.assert_not_called()

    def test_download_image_cached_artifact(self) -> None:
        """Test that download_image processes cached artifacts."""
        self.patch_download_image_artifact()

        # The image still needs processing:
        patcher = patch.object(self.image_cache, "image_in_cache")
        image_in_cache_mock = patcher.start()
        self.addCleanup(patcher.stop)
        image_in_cache_mock.return_value = False

        image_path = self.cache_path / "42" / "system.tar.xz"
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"Fake Data")

        self.image_cache.download_image(self.artifact)

        self._download_image_artifact_mock.assert_not_called()
        self.process_downloaded_image_mock.assert_called_with(self.artifact)

    def test_download_image_invalid_name(self) -> None:
        """Test that download_image downloads images when not cached."""
        self.patch_download_image_artifact()
        self.artifact.data["filename"] = ".hidden.artifact"
        with self.assertRaisesRegex(
            ValueError, "Image has an invalid filename"
        ):
            self.image_cache.download_image(self.artifact)

    def test_image_in_cache_absent(self) -> None:
        """Test that image_in_cache returns false when not cached."""
        self.assertFalse(self.image_cache.image_in_cache(self.artifact))

    def test_image_in_cache_present(self) -> None:
        """Test that image_in_cache finds images in the cache."""
        image_path = self.cache_path / "42" / "system.tar.xz"
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"Fake Data")
        self.assertTrue(self.image_cache.image_in_cache(self.artifact))

    def test_download_image_artifact(self) -> None:
        """Test that _download_image_artifact does a 2-stage download."""

        def download_tarball(
            artifact_id: int,  # noqa: U100
            path_in_artifact: str,  # noqa: U100
            destination: Path,
        ) -> None:
            destination.write_bytes(b"downloaded!")

        self.debusine_api.download_artifact_file.side_effect = download_tarball

        destination = self.cache_path / "filename.tar"
        self.image_cache._download_image_artifact(
            42, "filename.tar", destination
        )

        temp_path = self.cache_path / ".download.tmp.filename.tar"
        self.debusine_api.download_artifact_file.assert_called_with(
            42, path_in_artifact="filename.tar", destination=temp_path
        )

        self.assertTrue(destination.exists())

    @patch("debusine.tasks.executors.images.datetime")
    def test_record_image_use(self, datetime_mock: MagicMock) -> None:
        """Test that record_image_use writes a usage log."""
        (dir_ := self.image_cache.cache_artifact_path(42)).mkdir()
        record_path = dir_ / "usage.json"
        datetime_mock.now.return_value = datetime(
            2024, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc
        )

        self.image_cache.record_image_use(self.artifact)

        self.assertTrue(record_path.exists())
        with record_path.open("r") as f:
            record = json.load(f)
        self.assertEqual(
            record,
            {
                "version": 1,
                "backends": [],
                "usage": [
                    {
                        "filename": "system.tar.xz",
                        "timestamp": "2024-01-01T00:00:00+00:00",
                        "backend": None,
                    }
                ],
            },
        )

    @patch("debusine.tasks.executors.images.datetime")
    def test_record_image_use_appends(self, datetime_mock: MagicMock) -> None:
        """Test that record_image_use updates an existing usage log."""
        (dir_ := self.image_cache.cache_artifact_path(42)).mkdir()
        record_path = dir_ / "usage.json"
        existing = {
            "version": 1,
            "backends": [],
            "usage": [
                {
                    "filename": "system.tar.xz",
                    "timestamp": "2024-01-01T00:00:00+00:00",
                    "backend": None,
                }
            ]
            * 10,
        }
        with record_path.open("w") as f:
            json.dump(existing, f)

        datetime_mock.now.return_value = datetime(
            2024, 1, 2, 0, 0, 0, 0, tzinfo=timezone.utc
        )

        self.image_cache.record_image_use(self.artifact)

        with record_path.open("r") as f:
            record = json.load(f)

        self.assertEqual(len(record["usage"]), 10)
        self.assertEqual(
            record["usage"][-1],
            {
                "filename": "system.tar.xz",
                "timestamp": "2024-01-02T00:00:00+00:00",
                "backend": None,
            },
        )

    def test_find_least_used_images_empty_cache(self) -> None:
        """Test _find_least_used_images with an empty cache."""
        self.assertEqual(self.image_cache._find_least_used_images(), [])

    def test_find_least_used_images_ignores_non_digit_directories(self) -> None:
        """Test _find_least_used_images ignores non-digit directories."""
        (self.image_cache.image_cache_path / "foo").mkdir()
        self.assertEqual(self.image_cache._find_least_used_images(), [])

    def test_find_least_used_images_missing_log(self) -> None:
        """Test _find_least_used_images with an image missing a log."""
        self.image_cache.cache_artifact_path(42).mkdir()
        with self.assertLogsContains(
            "No usage log for artifact 42", logger=images_log
        ):
            self.assertEqual(
                self.image_cache._find_least_used_images(),
                [(42, set(_backends.keys()))],
            )

    def test_find_least_used_images_invalid_log(self) -> None:
        """Test _find_least_used_images with an unparsable log."""
        (cache_dir := self.image_cache.cache_artifact_path(42)).mkdir()
        with (cache_dir / "usage.json").open("w") as f:
            f.write('{"version": 99}')
        with self.assertLogsContains(
            "Failed to parse log for artifact 42", logger=images_log
        ):
            self.assertEqual(
                self.image_cache._find_least_used_images(),
                [(42, set(_backends.keys()))],
            )

    def write_image_usage_log(
        self,
        artifact_id: int,
        timestamp: datetime | None = None,
        backends: set[str] | None = None,
    ) -> None:
        """Create an image usage log for artifact_id, with timestamp."""
        if timestamp is None:
            timestamp = datetime(2024, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
        if backends is None:
            backends = set()
        (dir_ := self.image_cache.cache_artifact_path(artifact_id)).mkdir()
        record_path = dir_ / "usage.json"
        existing = {
            "version": 1,
            "backends": list(backends),
            "usage": [
                {
                    "filename": "system.tar.xz",
                    "timestamp": timestamp.isoformat(),
                    "backend": None,
                }
            ],
        }
        with record_path.open("w") as f:
            json.dump(existing, f)

    def test_find_least_used_images_under_size(self) -> None:
        """Test _find_least_used_images returns nothing under CACHE_SIZE."""
        assert CACHE_SIZE > 2
        self.write_image_usage_log(42)
        self.write_image_usage_log(43)
        self.assertEqual(self.image_cache._find_least_used_images(), [])

    def test_find_least_used_images_over_size(self) -> None:
        """Test _find_least_used_images returns images over CACHE_SIZE."""
        for i in range(CACHE_SIZE):
            self.write_image_usage_log(i)
        self.write_image_usage_log(
            99, datetime(2023, 12, 1, 0, 0, 0, 0, tzinfo=timezone.utc), {"foo"}
        )
        self.assertEqual(
            self.image_cache._find_least_used_images(), [(99, {"foo"})]
        )

    @patch("debusine.tasks.executors.images.rmtree")
    def test_clean_up_old_images(self, rmtree: MagicMock) -> None:
        """Test that clean_up_old_images cleans them up."""
        patcher = patch.object(self.image_cache, "_find_least_used_images")
        _find_least_used_images = patcher.start()
        self.addCleanup(patcher.stop)
        _find_least_used_images.return_value = [(99, {"foo"})]
        (img_dir := self.image_cache.cache_artifact_path(99)).mkdir()

        FooExecutor = MagicMock()

        with patch.dict(
            "debusine.tasks.executors.base._backends", {"foo": FooExecutor}
        ):
            self.image_cache.clean_up_old_images()

        FooExecutor.clean_up_image.assert_called_once_with(99)
        rmtree.assert_called_once_with(img_dir)

    @patch("debusine.tasks.executors.images.rmtree")
    def test_clean_up_old_images_ignores_backends_without_cleanup(
        self, rmtree: MagicMock
    ) -> None:
        """Test that clean_up_old_images doesn't require backends cleeanup."""
        patcher = patch.object(self.image_cache, "_find_least_used_images")
        _find_least_used_images = patcher.start()
        self.addCleanup(patcher.stop)
        _find_least_used_images.return_value = [(99, {"foo"})]
        (img_dir := self.image_cache.cache_artifact_path(99)).mkdir()

        FooExecutor = create_autospec(ExecutorInterface)

        with patch.dict(
            "debusine.tasks.executors.base._backends", {"foo": FooExecutor}
        ):
            self.image_cache.clean_up_old_images()

        self.assertFalse(hasattr(FooExecutor, "clean_up_image"))
        rmtree.assert_called_once_with(img_dir)

    def test_clean_up_old_images_swallows_exceptions(self) -> None:
        """Test that clean_up_old_images swallows exceptions for backends."""
        patcher = patch.object(self.image_cache, "_find_least_used_images")
        _find_least_used_images = patcher.start()
        self.addCleanup(patcher.stop)
        _find_least_used_images.return_value = [(99, {"foo"})]
        self.image_cache.cache_artifact_path(99).mkdir()

        FooExecutor = MagicMock()
        FooExecutor.clean_up_image.side_effect = Exception("Can't find it.")

        with (
            patch.dict(
                "debusine.tasks.executors.base._backends", {"foo": FooExecutor}
            ),
            self.assertLogsContains(
                "Failed to clean up 99 backend foo", logger=images_log
            ),
        ):
            self.image_cache.clean_up_old_images()
