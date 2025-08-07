# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Generate cloud-init scripts for bootstrapping Debusine Workers."""

from base64 import b64encode
from textwrap import dedent

import yaml
from django.conf import settings

from debusine.db.models import Token
from debusine.server.worker_pools.models import (
    BaseWorkerPoolDataModel,
    DebianRelease,
    DebusineInstallSource,
)

DAILY_BUILD_SOURCE = dedent(
    """\
    Types: deb
    URIs: https://freexian-team.pages.debian.net/debusine/repository/
    Suites: bookworm
    Components: main
    Architectures: all
    Signed-By:
      -----BEGIN PGP PUBLIC KEY BLOCK-----
      .
      mQENBGUAlgcBCAD0AhJt2KVLlrJ+Bx8VcjxCNGcOEMPOY1Db+F+8TiaYrSqZ07kx
      VOJYATlNAqCkQqCu89E9WvqKTg2kyNZchyArwi4bUhEfA+BT+KBeb4dGcoAdwUNX
      c4hx94M/Ow2hAStIni2TUYau99e5sY2QIFb03Lb3dShlQA2EesjQcvnpZCDBcVHW
      kYGj8zjh+i0QdtGVYJlXI/IskSJKexJuiOQ9uIGtsJ+VAm2hD1dTDWvGoUp2quWP
      uHGBouyzFkFQpCOXKBnfpF4h7LMFJSx9KNV/rSMZgLf0F9ukeGnjsST3okQGz2Sz
      E5WdoJuaLOgj/eCeTG2lHD1sdzyndQtG9sKJABEBAAG0Q0RlYnVzaW5lIFNhbHNh
      IFJlcG9zaXRvcnkgPHJhcGhhZWwrZGVidXNpbmUrc2Fsc2FyZXBvQGZyZWV4aWFu
      LmNvbT6JAU4EEwEKADgWIQSQH9XztPO3VNW3w993YWGYlEiOAAUCZQCWBwIbLwUL
      CQgHAwUVCgkICwUWAgMBAAIeAQIXgAAKCRB3YWGYlEiOAO8eCACwcqdDbVigGiv8
      x9iUOp59/6XMG/mtdSjzkkK5LVUEq9bkQAdLRbzb43p0vLl7E/7Yt3on62D6N3QB
      1ap710QsDRIN/CoKRi6sjHCiAqpSUC9LcwgLmnrLwUHDPDrkpsnWmQ7YINtV1P6E
      BFgdqqtw/1avMyliBbh3/XNRQkYq6xkcEZGIftqwX8GYB3M+cheefYe24H/JwLJm
      gerMz8MVEGKMzDm0OaLjWixV6Ga4Wkzo5ya1zg5OmROULAj3CsKDN76OCEwGGMBs
      kNsTB9aAisP8D6wW6ZXX5eChRLRCmKUEB5zpHJ++jdJ0yY38kF+N17w5eos+rc4s
      bMld2SQP
      =XfKL
      -----END PGP PUBLIC KEY BLOCK-----
    """
)

FIREWALL = dedent(
    """\
    #!/usr/sbin/nft -f

    add table inet filter;
    flush table inet filter;

    table inet filter {
        chain input {
            type filter hook input priority filter;
            iif "lo" accept;  # codespell:ignore iif
            tcp dport ssh counter accept;
            meta l4proto {icmp, icmpv6} counter accept;
            ct state invalid,new,untracked counter drop;
        }
        chain forward {
            type filter hook forward priority filter;
            # Cloud Metadata endpoints
            ip daddr 169.254.169.254 counter drop;
            ip6 daddr fd00:ec2::254 counter drop;
        }
        chain output {
            type filter hook output priority filter;
            # Cloud Metadata endpoints
            ip daddr 169.254.169.254 meta skuid != 0 counter drop;
            ip6 daddr fd00:ec2::254 meta skuid != 0 counter drop;
        }
    }
    """
)


class AptConfig(BaseWorkerPoolDataModel):
    """Model for cloud-init APT Configure object."""

    preserve_sources_list: bool
    sources_list: str


class WriteFile(BaseWorkerPoolDataModel):
    """Model for cloud-init Write Files write_files objects."""

    path: str
    content: str
    permissions: str | None = None
    owner: str | None = None


class FilesystemSetup(BaseWorkerPoolDataModel):
    """Model for cloud-init Disk Setup fs_setup objects."""

    device: str
    filesystem: str


class CloudInitConfig(BaseWorkerPoolDataModel):
    """Model for cloud-init configuration."""

    apt: AptConfig | None = None
    device_aliases: dict[str, str] = {}
    fs_setup: list[FilesystemSetup] = []
    mounts: list[list[str]] = []
    package_reboot_if_required: bool = False
    package_update: bool = False
    package_upgrade: bool = False
    packages: list[str] = []
    bootcmd: list[str] = []
    runcmd: list[str] = []
    write_files: list[WriteFile] = []

    def as_str(self) -> str:
        """Render configuration file in YAML."""
        return "#cloud-config\n" + yaml.safe_dump(
            self.dict(exclude_defaults=True)
        )

    def as_base64_str(self) -> str:
        """Render configuration file in YAML and base64-encode the result."""
        return b64encode(self.as_str().encode("utf-8")).decode("ascii")


def worker_bootstrap_cloud_init(
    activation_token: Token,
    debusine_install_source: DebusineInstallSource,
    debian_release: DebianRelease | None,
) -> CloudInitConfig:
    """Generate cloud-init configuration for a new instance."""
    if getattr(activation_token, "key", None) is None:
        raise ValueError(
            f"Requires a fresh token with a key. Token<{activation_token.pk}> "
            f"does not have one."
        )

    if debusine_install_source == DebusineInstallSource.PRE_INSTALLED:
        return CloudInitConfig(
            package_update=True,
            package_upgrade=True,
            package_reboot_if_required=True,
            write_files=[
                WriteFile(
                    path="/etc/debusine/worker/activation-token",
                    permissions="0o600",
                    content=activation_token.key,
                    owner="debusine-worker:debusine-worker",
                ),
            ],
            runcmd=["systemctl restart debusine-worker.service"],
        )

    config = CloudInitConfig(
        package_update=True,
        package_upgrade=True,
        package_reboot_if_required=True,
        apt=None,
        bootcmd=[
            # Executes early in boot, before apt is configured
            (
                "[ -e /usr/bin/eatmydata ] "
                "|| (apt-get update && apt-get -y install eatmydata)"
            ),
        ],
        write_files=[
            # We set the owner in a runcmd after the user has been created
            WriteFile(
                path="/etc/debusine/worker/activation-token",
                permissions="0o600",
                content=activation_token.key,
            ),
            WriteFile(
                path="/etc/nftables.conf",
                permissions="0o755",
                content=FIREWALL,
            ),
            WriteFile(
                path="/etc/debusine/worker/config.ini",
                content=dedent(
                    f"""\
                    [General]
                    api-url = https://{settings.DEBUSINE_FQDN}/api
                    log-file = /var/log/debusine/worker/worker.log
                    log-level = DEBUG
                    """
                ),
            ),
        ],
        packages=[
            "autopkgtest",
            "ca-certificates",
            "debusine-worker",
            "gpg",
            "incus",
            "mmdebstrap",
            "nftables",
            "sbuild",
            "uidmap",
        ],
        runcmd=[
            (
                "chown debusine-worker:debusine-worker "
                "/etc/debusine/worker/activation-token"
            ),
            "/usr/share/doc/debusine-worker/examples/configure-worker-incus.sh",
            "systemctl enable nftables.service",
            "systemctl start nftables.service",
            "systemctl restart debusine-worker.service",
        ],
    )
    backports_packages: list[str] = []
    if debian_release == DebianRelease.BOOKWORM:
        backports_packages += [
            "src:autopkgtest",
            "src:incus",
            "src:python-django",
            # Required by incus backport
            "src:qemu",
            # Required by QEMU backport
            "src:seabios",
        ]
        config.apt = AptConfig(
            preserve_sources_list=False,
            sources_list=dedent(
                """\
                deb $MIRROR $RELEASE main contrib non-free-firmware
                deb $MIRROR $RELEASE-backports main contrib non-free-firmware
                deb $SECURITY $RELEASE-security main contrib non-free-firmware
                """
            ),
        )

    if debusine_install_source == DebusineInstallSource.BACKPORTS:
        backports_packages.append("src:debusine")

    if backports_packages:
        if debian_release:
            pin_release = f"n={debian_release}-backports"
        else:
            pin_release = "a=stable-backports"
        config.write_files.append(
            WriteFile(
                path="/etc/apt/preferences.d/90-debusine",
                content=dedent(
                    f"""\
                    Package: {" ".join(backports_packages)}
                    Pin: release {pin_release}
                    Pin-Priority: 900
                    """
                ),
            )
        )
    if debusine_install_source == DebusineInstallSource.DAILY_BUILDS:
        config.write_files.append(
            WriteFile(
                path="/etc/apt/sources.list.d/debusine.sources",
                content=DAILY_BUILD_SOURCE,
            ),
        )
    return config
