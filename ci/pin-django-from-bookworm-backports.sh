#!/bin/sh

set -e

# Ensure that python3-django from bookworm-backports will be installed as
# needed.  This is a no-op if bookworm-backports isn't in the APT sources.
cat >/etc/apt/preferences.d/99django-backports <<EOT
Package: python3-django
Pin: release n=bookworm-backports
Pin-Priority: 900
EOT
