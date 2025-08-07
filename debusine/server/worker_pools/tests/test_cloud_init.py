# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for cloud-init script generation."""

from base64 import b64decode
from textwrap import dedent

import yaml
from django.test import SimpleTestCase

from debusine.db.models import Token
from debusine.server.worker_pools.cloud_init import (
    AptConfig,
    CloudInitConfig,
    FilesystemSetup,
    WriteFile,
    worker_bootstrap_cloud_init,
)
from debusine.server.worker_pools.models import (
    DebianRelease,
    DebusineInstallSource,
)
from debusine.test.django import TestCase


class CloudInitConfigTest(SimpleTestCase):
    """Tests for CloudInitConfig."""

    def test_full_instantiation(self) -> None:
        config = CloudInitConfig(
            apt=AptConfig(
                preserve_sources_list=False,
                sources_list="deb $MIRROR $RELEASE main",
            ),
            device_aliases={"foo": "/dev/nvme0n1"},
            fs_setup=[
                FilesystemSetup(device="foo", filesystem="ext4"),
            ],
            mounts=[
                ["foo", "/mnt/foo", "ext4", "defaults"],
            ],
            package_reboot_if_required=True,
            package_update=True,
            package_upgrade=True,
            packages=["build-essential", "vim-tiny"],
            bootcmd=["/bin/true"],
            runcmd=["/bin/true", "/bin/false"],
            write_files=[
                WriteFile(
                    path="/etc/hostname", content="test", permissions="0o644"
                ),
            ],
        )
        rendered = config.as_str()
        parsed = yaml.safe_load(rendered)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(
            parsed["apt"],
            {
                "preserve_sources_list": False,
                "sources_list": "deb $MIRROR $RELEASE main",
            },
        )

        self.assertEqual(
            parsed["fs_setup"], [{"device": "foo", "filesystem": "ext4"}]
        )
        self.assertTrue(parsed["package_reboot_if_required"])

    def test_empty_as_str(self) -> None:
        config = CloudInitConfig()
        self.assertEqual(config.as_str(), "#cloud-config\n{}\n")

    def test_simple_as_str(self) -> None:
        config = CloudInitConfig(packages=["foobar"], runcmd=["/bin/true"])
        self.assertEqual(
            config.as_str(),
            dedent(
                """\
                #cloud-config
                packages:
                - foobar
                runcmd:
                - /bin/true
                """
            ),
        )

    def test_simple_as_base64_str(self) -> None:
        config = CloudInitConfig(packages=["foobar"], runcmd=["/bin/true"])
        encoded = config.as_base64_str()
        parsed = yaml.safe_load(b64decode(encoded))
        self.assertEqual(
            parsed, {"packages": ["foobar"], "runcmd": ["/bin/true"]}
        )


class TestWorkerBootstrapCloudInit(TestCase):
    """Tests for worker_bootstrap_cloud_init."""

    def test_worker_bootstrap_cloud_init_simple(self) -> None:
        token = self.playground.create_bare_token()
        config = worker_bootstrap_cloud_init(
            token, DebusineInstallSource.BACKPORTS, None
        )
        self.assertTrue(config.package_update)
        self.assertTrue(config.package_upgrade)
        self.assertTrue(config.package_reboot_if_required)
        self.assertEqual(len(config.bootcmd), 1)
        self.assertIn("eatmydata", config.bootcmd[0])
        self.assertIn("debusine-worker", config.packages)
        self.assertIn("incus", config.packages)
        apt_pin_files = [
            file
            for file in config.write_files
            if file.path == "/etc/apt/preferences.d/90-debusine"
        ]
        self.assertEqual(len(apt_pin_files), 1)
        self.assertIn("src:debusine", apt_pin_files[0].content)
        self.assertIn("a=stable-backports", apt_pin_files[0].content)
        token_files = [
            file
            for file in config.write_files
            if file.path == "/etc/debusine/worker/activation-token"
        ]
        self.assertEqual(len(token_files), 1)
        self.assertEqual(token_files[0].content, token.key)
        self.assertIsNone(token_files[0].owner)
        nftables_files = [
            file
            for file in config.write_files
            if file.path == "/etc/nftables.conf"
        ]
        self.assertEqual(len(nftables_files), 1)
        daily_build_sources = [
            file
            for file in config.write_files
            if file.path == "/etc/apt/sources.list.d/debusine.sources"
        ]
        self.assertEqual(len(daily_build_sources), 0)

    def test_worker_bootstrap_cloud_init_daily(self) -> None:
        token = self.playground.create_bare_token()
        config = worker_bootstrap_cloud_init(
            token, DebusineInstallSource.DAILY_BUILDS, None
        )
        apt_pin_files = [
            file
            for file in config.write_files
            if file.path == "/etc/apt/preferences.d/90-debusine"
        ]
        self.assertEqual(len(apt_pin_files), 0)
        daily_build_sources = [
            file
            for file in config.write_files
            if file.path == "/etc/apt/sources.list.d/debusine.sources"
        ]
        self.assertEqual(len(daily_build_sources), 1)

    def test_worker_bootstrap_cloud_init_pre_installed(self) -> None:
        token = self.playground.create_bare_token()
        config = worker_bootstrap_cloud_init(
            token, DebusineInstallSource.PRE_INSTALLED, None
        )
        self.assertTrue(config.package_update)
        self.assertTrue(config.package_upgrade)
        self.assertTrue(config.package_reboot_if_required)
        self.assertEqual(config.bootcmd, [])
        self.assertEqual(
            config.write_files,
            [
                WriteFile(
                    path="/etc/debusine/worker/activation-token",
                    permissions="0o600",
                    content=token.key,
                    owner="debusine-worker:debusine-worker",
                )
            ],
        )
        self.assertEqual(
            config.runcmd, ["systemctl restart debusine-worker.service"]
        )
        self.assertEqual(config.packages, [])

    def test_worker_bootstrap_bookworm_bpo(self) -> None:
        token = self.playground.create_bare_token()
        config = worker_bootstrap_cloud_init(
            token, DebusineInstallSource.BACKPORTS, DebianRelease.BOOKWORM
        )
        apt_pin_files = [
            file
            for file in config.write_files
            if file.path == "/etc/apt/preferences.d/90-debusine"
        ]
        self.assertEqual(len(apt_pin_files), 1)
        self.assertIn("src:debusine", apt_pin_files[0].content)
        self.assertIn("src:python-django", apt_pin_files[0].content)
        self.assertIn("n=bookworm-backports", apt_pin_files[0].content)

    def test_worker_bootstrap_bookworm_daily(self) -> None:
        token = self.playground.create_bare_token()
        config = worker_bootstrap_cloud_init(
            token, DebusineInstallSource.DAILY_BUILDS, DebianRelease.BOOKWORM
        )
        self.assertIsNotNone(config.apt)
        assert config.apt
        self.assertFalse(config.apt.preserve_sources_list)
        self.assertEqual(
            config.apt.sources_list,
            dedent(
                """\
                deb $MIRROR $RELEASE main contrib non-free-firmware
                deb $MIRROR $RELEASE-backports main contrib non-free-firmware
                deb $SECURITY $RELEASE-security main contrib non-free-firmware
                """
            ),
        )
        apt_pin_files = [
            file
            for file in config.write_files
            if file.path == "/etc/apt/preferences.d/90-debusine"
        ]
        self.assertEqual(len(apt_pin_files), 1)
        self.assertNotIn("src:debusine", apt_pin_files[0].content)
        self.assertIn("src:python-django", apt_pin_files[0].content)
        self.assertIn("n=bookworm-backports", apt_pin_files[0].content)
        daily_build_sources = [
            file
            for file in config.write_files
            if file.path == "/etc/apt/sources.list.d/debusine.sources"
        ]
        self.assertEqual(len(daily_build_sources), 1)

    def test_worker_bootstrap_trixie(self) -> None:
        token = self.playground.create_bare_token()
        config = worker_bootstrap_cloud_init(
            token, DebusineInstallSource.RELEASE, DebianRelease.TRIXIE
        )
        self.assertIsNone(config.apt)
        apt_pin_files = [
            file
            for file in config.write_files
            if file.path == "/etc/apt/preferences.d/90-debusine"
        ]
        self.assertEqual(len(apt_pin_files), 0)

    def test_worker_bootstrap_cloud_init_without_key(self) -> None:
        token = Token()
        with self.assertRaisesRegex(
            ValueError,
            (
                r"Requires a fresh token with a key. Token<None> does not have "
                r"one\."
            ),
        ):
            worker_bootstrap_cloud_init(
                token, DebusineInstallSource.BACKPORTS, None
            )
