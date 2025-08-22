#!/bin/sh

set -e

# Set up the debusine server, worker, and client.

debian/tests/utils/integration-tests-setup-debusine-server.sh
debian/tests/utils/integration-tests-setup-debusine-worker.sh

debian/tests/utils/integration-tests-setup-debusine-client.sh
