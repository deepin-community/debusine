# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test the Dummy Worker Pool Implementation."""

from debusine.server.worker_pools import DummyWorkerPool
from debusine.test.django import TestCase


class DumyWorkerPoolTest(TestCase):
    """Tests for DummyWorkerPool."""

    def test_launch_worker(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        worker_pool_api = DummyWorkerPool(worker_pool=worker_pool)

        worker = self.playground.create_worker(worker_pool=worker_pool)
        worker.instance_created_at = None
        worker.save()

        worker_pool_api.launch_worker(worker)

        self.assertIsNotNone(worker.instance_created_at)
        self.assertEqual(worker.worker_pool_data, {"launched": True})

    def test_terminate_worker(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        worker_pool_api = DummyWorkerPool(worker_pool=worker_pool)

        worker = self.playground.create_worker(worker_pool=worker_pool)
        worker.worker_pool_data = {"something": "Foo"}
        worker.save()

        worker_pool_api.terminate_worker(worker)

        self.assertIsNone(worker.instance_created_at)
        self.assertIsNone(worker.worker_pool_data)
