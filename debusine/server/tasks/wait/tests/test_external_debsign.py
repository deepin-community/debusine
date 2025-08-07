# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for the ExternalDebsign task."""

from debusine.artifacts.models import ArtifactCategory, DebianUpload
from debusine.server.tasks.wait import ExternalDebsign
from debusine.server.tasks.wait.models import ExternalDebsignDynamicData
from debusine.tasks.server import ArtifactInfo
from debusine.tasks.tests.helper_mixin import FakeTaskDatabase
from debusine.test.django import TestCase


class ExternalDebsignTaskTests(TestCase):
    """Test the ExternalDebsign task."""

    def test_compute_dynamic_data(self) -> None:
        """Dynamic data receives relevant artifact IDs."""
        unsigned_lookup = "internal@collections/unsigned-upload"
        task_db = FakeTaskDatabase(
            single_lookups={
                # unsigned
                (unsigned_lookup, None): ArtifactInfo(
                    1,
                    ArtifactCategory.UPLOAD,
                    DebianUpload(
                        type="dpkg",
                        changes_fields={
                            "Architecture": "source",
                            "Files": [{"name": "hello_1.0.dsc"}],
                        },
                    ),
                ),
            }
        )

        task = ExternalDebsign(task_data={"unsigned": unsigned_lookup})
        self.assertEqual(
            task.compute_dynamic_data(task_db),
            ExternalDebsignDynamicData(unsigned_id=1),
        )

    def test_execute(self) -> None:
        """Executing the task does nothing, successfully."""
        task = ExternalDebsign(
            task_data={"unsigned": 1}, dynamic_task_data={"unsigned_id": 1}
        )
        task.set_work_request(self.playground.create_work_request())
        self.assertTrue(task.execute())

    def test_label(self) -> None:
        """Test get_label."""
        task = ExternalDebsign(
            task_data={"unsigned": "internal@collections/unsigned-upload"},
            dynamic_task_data={"unsigned_id": 1},
        )
        self.assertEqual(
            task.get_label(),
            "wait for external debsign for "
            "internal@collections/unsigned-upload",
        )
