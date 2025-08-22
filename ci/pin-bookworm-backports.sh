#!/bin/sh

set -e

# Ensure that recent versions of important dependencies will be installed from
# bookworm-backports. This is a no-op if bookworm-backports isn't in the APT
# sources.
cat >/etc/apt/preferences.d/99backports <<EOT
Package: debvm libjs-bootstrap5 python3-django
Pin: release n=bookworm-backports
Pin-Priority: 900
EOT
