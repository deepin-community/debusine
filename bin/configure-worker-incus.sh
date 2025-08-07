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

set -eu

if [ "$(id -u)" -ne 0 ]; then
    if ! id -Gn | grep -qFw incus-admin; then
        echo "ERROR: Must be root or a member of incus-admin" >&2
        exit 1
    fi
    if ! id -Gn debusine-worker | grep -qFw incus-admin; then
        echo "ERROR: Must be root to add debusine-worker to incus-admin" >&2
        exit 1
    fi
else
    if ! id -Gn debusine-worker | grep -qFw incus-admin; then
        echo "Adding debusine-worker to incus-admin..."
        usermod --append --groups incus-admin debusine-worker
        systemctl restart debusine-worker.service
    fi
fi

if [ ! -d /var/lib/incus/database ]; then
    echo "Initializing Incus..."
    incus admin init --minimal
fi

if ! incus network list -f csv | grep -q ^debusinebr0,; then
    echo "Creating debusine network..."
    incus network create debusinebr0
    incus network show debusinebr0
fi

if ! incus profile list -f csv | grep -q ^debusine,; then
    echo "Creating debusine profile..."
    incus profile create debusine
    incus profile set debusine raw.lxc=lxc.mount.auto=sys
    incus profile set debusine security.secureboot=false
    incus profile device add debusine host0 nic network=debusinebr0 name=host0
    incus profile device add debusine root disk pool=default path=/
    incus profile show debusine
fi

if ! incus storage list -f csv | grep -q .; then
    echo "Creating default storage pool..."
    filesystem=$(findmnt --raw --output FSTYPE --noheadings --first-only --target /var/lib/incus/storage-pools/)
    case "$filesystem" in
        btrfs)
            driver=btrfs
            ;;
        zfs)
            driver=zfs
            ;;
        *)
            driver=dir
            ;;
    esac
    if [ $driver != dir ]; then
        # Fall back to dir driver if we're unable to use the specialized driver
        if ! incus storage create default $driver; then
            incus storage create default dir
        fi
    else
        incus storage create default $driver
    fi
    incus storage show default
fi

printf "Enabled Incus Drivers: "
incus info | grep driver: | cut -d: -f 2
if ! incus info | grep -q 'driver:.*qemu'; then
    grep 'Instance type not operational' /var/log/incus/incus.log
fi
