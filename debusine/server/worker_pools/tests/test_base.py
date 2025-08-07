# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Test the Worker Pool base API."""

from debusine.server.worker_pools import DummyWorkerPool, provider_interface
from debusine.test.django import TestCase


class TestWorkerPoolInterface(TestCase):
    """Tests for WorkerPoolInterface."""

    def test_lookup_test(self) -> None:
        worker_pool = self.playground.create_worker_pool()
        worker_pool_api = provider_interface(worker_pool)
        self.assertIsInstance(worker_pool_api, DummyWorkerPool)
        self.assertEqual(worker_pool_api.worker_pool, worker_pool)
