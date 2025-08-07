#!/bin/sh

set -e

# Set up the debusine server, worker, and client.

debian/tests/utils/integration-tests-setup-debusine-server.sh
worker_token=$(debian/tests/utils/integration-tests-setup-debusine-worker.sh)

sudo -u debusine-server debusine-admin manage_worker enable "$worker_token"

debian/tests/utils/integration-tests-setup-debusine-client.sh
