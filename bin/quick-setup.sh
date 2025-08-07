#!/bin/sh
#
# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

set -e

SUDO="sudo"
SUDO_U="sudo -u"

if [ "$(whoami)" = "root" ]; then
    SUDO=""
    SUDO_U="runuser -u"
fi

APT_OPTIONS="--no-install-recommends"
if [ "$NON_INTERACTIVE" = "1" ]; then
    APT_OPTIONS="-y"
fi

usage() {
    cat <<EOF
Usage: bin/quick-setup.sh [action]

Run with no arguments to perform all actions.

Actions:
  create_directories    Create directories required by debusine.
  install_packages      Ensure that required packages are installed.
  setup_settings        Install a configuration file.
  setup_database        Configure a PostgreSQL database.
EOF
}

sanity_checks() {
    if [ -e debusine/project/settings/local.py ]; then
        echo "ERROR: You already have a configuration file (debusine/project/settings/local.py)" >&2
        exit 1
    fi

    if [ -e data/debusine.sqlite ]; then
        echo "ERROR: You already have a database file (data/debusine.sqlite)" >&2
        exit 1
    fi
}

install_packages() {
    echo ">>> Ensuring we have the required packages"
    packages="$(sed -Ene 's/.*# deb: (.*)/\1,/p' pyproject.toml)"

    # shellcheck disable=SC2086
    $SUDO apt-get $APT_OPTIONS satisfy "$packages"
}

setup_settings() {
    echo ">>> Installing a configuration file"
    cp debusine/project/settings/local.py.sample debusine/project/settings/local.py
}

setup_database() {
    echo ">>> Configuring postgresql user and databases"
    $SUDO_U postgres -- createuser -d "$USER" || true
    if ! $SUDO_U postgres -- psql -A -l | grep -q '^debusine|'; then
        $SUDO_U postgres -- createdb -O "$USER" -E UTF8 debusine
    fi
}

create_directories() {
    echo ">>>> creating directories required by debusine"
    mkdir -p "data/uploads" "data/store" "data/cache" "data/logs" "data/media" "data/static" "data/templates"
}

if [ ! -e debusine/project/settings/local.py.sample ]; then
    echo "ERROR: are you at the root of the debusine repository?" >&2
    usage >&2
    exit 1
fi

case "$1" in
    "")
        sanity_checks
        install_packages
        setup_settings
        setup_database
        create_directories
        ;;
    -h|--help)
        usage
        ;;
    create_directories)
        create_directories
        ;;
    install_packages)
        install_packages
        ;;
    setup_settings)
        setup_settings
        ;;
    setup_database)
        setup_database
        ;;
esac

