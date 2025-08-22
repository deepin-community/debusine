#!/usr/bin/env python3

# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine integration tests for APT mirroring."""

import lzma
import os
import re
import socket
import subprocess
import unittest
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from textwrap import dedent
from threading import Thread

import gpg
import requests
import yaml

from debusine.artifacts.models import CollectionCategory
from debusine.signing.gnupg import gpg_ephemeral_context, gpg_generate
from utils.client import Client
from utils.common import Configuration
from utils.integration_test_helpers_mixin import IntegrationTestHelpersMixin
from utils.server import DebusineServer


class IntegrationTaskAPTMirrorTests(
    IntegrationTestHelpersMixin, unittest.TestCase
):
    """
    Integration tests for the aptmirror task.

    These tests assume:
    - debusine-server is running
    - debusine-worker is running (connected to the server)
    - debusine-client is correctly configured
    """

    TASK_NAME = "aptmirror"

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        # If debusine-server or nginx was launched just before the
        # integration-tests-simplesystemimagebuild.py is launched the
        # debusine-server might not be yet available. Let's wait for the
        # debusine-server to be reachable if it's not ready yet
        self.assertTrue(
            DebusineServer.wait_for_server_ready(),
            f"debusine-server should be available (in "
            f"{Configuration.get_base_url()}) before the integration tests "
            f"are run",
        )

        self.architecture = subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True
        ).strip()

    def create_suite(self, suite_name: str) -> None:
        """Create a suite collection."""
        result = DebusineServer.execute_command(
            "create_collection", suite_name, CollectionCategory.SUITE
        )
        self.assertEqual(result.returncode, 0)

    def add_suite_to_archive(self, suite_name: str) -> None:
        """Add a suite to our workspace's archive."""
        # TODO: We don't have a sensible command to do this yet.
        result = DebusineServer.execute_command(
            "shell",
            "-c",
            dedent(
                f"""\
                from debusine.artifacts.models import *
                from debusine.db.models import *

                user = User.objects.get(username="test-user")
                workspace = default_workspace()
                archive = workspace.get_singleton_collection(
                    user=user, category=CollectionCategory.ARCHIVE
                )
                suite = workspace.get_collection(
                    user=user,
                    category=CollectionCategory.SUITE,
                    name={suite_name!r},
                )
                archive.manager.add_collection(suite, user=user)
                """
            ),
        )
        self.assertEqual(result.returncode, 0)

    def make_packages(
        self,
        temp_path: Path,
        packages_path: Path,
        from_directory: str,
        override_path: Path,
        architecture: str | None = None,
    ) -> None:
        """Generate ``Packages`` and ``Packages.xz`` files."""
        args = ["apt-ftparchive"]
        if architecture is not None:
            args += ["-a", architecture]
        args += ["packages", from_directory, override_path]
        packages_data = subprocess.run(
            args, stdout=subprocess.PIPE, cwd=temp_path, check=True
        ).stdout
        with open(packages_path, mode="wb") as packages_file:
            packages_file.write(packages_data)
        with lzma.open(
            packages_path.with_suffix(".xz"), mode="wb", format=lzma.FORMAT_XZ
        ) as packages_xz_file:
            packages_xz_file.write(packages_data)

    def make_sources(
        self,
        temp_path: Path,
        sources_path: Path,
        from_directory: str,
        override_path: Path,
    ) -> None:
        """Generate ``Sources`` and ``Sources.xz`` files."""
        sources_data = subprocess.run(
            ["apt-ftparchive", "sources", from_directory, override_path],
            stdout=subprocess.PIPE,
            cwd=temp_path,
            check=True,
        ).stdout
        with open(sources_path, mode="wb") as sources_file:
            sources_file.write(sources_data)
        with lzma.open(
            sources_path.with_suffix(".xz"), mode="wb", format=lzma.FORMAT_XZ
        ) as sources_xz_file:
            sources_xz_file.write(sources_data)

    def make_release(self, release_path: Path, suite_name: str) -> None:
        """Generate a ``Release`` file."""
        with open(release_path, "w") as release:
            subprocess.run(
                [
                    "apt-ftparchive",
                    "-o",
                    f"APT::FTPArchive::Release::Suite={suite_name}",
                    "release",
                    release_path.parent,
                ],
                stdout=release,
                text=True,
                check=True,
            )

    def gpg_sign(
        self, private_key: bytes, data_path: Path, signature_path: Path
    ) -> None:
        """
        Sign data using GnuPG.

        Borrowed from ``debusine.signing.gnupg.gpg_sign``, but without the
        ``ProtectedKey`` abstraction that just gets in the way for
        integration testing.  Don't use this in production.
        """
        with (
            self._temporary_directory() as tmp,
            gpg_ephemeral_context(Path(tmp)) as ctx,
        ):
            import_result = ctx.key_import(private_key)
            self.assertNotEqual(getattr(import_result, "secret_imported", 0), 0)
            ctx.signers = list(
                ctx.keylist(pattern=import_result.imports[0].fpr, secret=True)
            )
            with (
                open(data_path, mode="rb") as data_file,
                open(signature_path, mode="wb") as signature_file,
            ):
                ctx.sign(
                    gpg.Data(file=data_file),
                    sink=signature_file,
                    mode=gpg.constants.SIG_MODE_CLEAR,
                )

    def serve_archive(self, path: Path) -> tuple[str | bytes | bytearray, int]:
        """Serve a local archive over HTTP, in a thread."""
        httpd = HTTPServer(
            ("", 0), partial(SimpleHTTPRequestHandler, directory=path)
        )
        httpd_thread = Thread(target=httpd.serve_forever)
        httpd_thread.start()
        self.addCleanup(httpd_thread.join)
        self.addCleanup(httpd.shutdown)
        host, port = httpd.server_address
        return host, port

    def wait_and_assert_result(self, work_request_id: int) -> None:
        """Wait for a task to complete, assert success."""
        status = Client.wait_for_work_request_completed(
            work_request_id, "success"
        )
        if not status:
            self.print_work_request_debug_logs(work_request_id)
        self.assertTrue(status)

    def test_pooled(self) -> None:
        """Import and serve packages from a pooled format."""
        suite_name = "sid-dists-pool"
        self.create_suite(suite_name)
        self.add_suite_to_archive(suite_name)

        private_key, public_key = gpg_generate("test")

        with (
            self.apt_indexes(
                "sid", architectures=["amd64", "s390x"]
            ) as apt_path,
            self._temporary_directory() as temp_path,
        ):
            debusine_path = temp_path / "pool/main/d/debusine"
            debusine_path.mkdir(parents=True)
            hello_path = temp_path / "pool/main/h/hello"
            hello_path.mkdir(parents=True)
            subprocess.run(
                ["apt-get", "source", "--download-only", "debusine"],
                cwd=debusine_path,
                env=self.make_apt_environment(apt_path),
                check=True,
            )
            for package, parent_path in (
                ("python3-debusine", debusine_path),
                ("hello:amd64", hello_path),
                ("hello:s390x", hello_path),
            ):
                subprocess.run(
                    ["apt-get", "download", package],
                    cwd=parent_path,
                    env=self.make_apt_environment(apt_path),
                    check=True,
                )
            suite_path = temp_path / f"dists/{suite_name}"
            suite_binary_amd64_path = suite_path / "main/binary-amd64"
            suite_binary_s390x_path = suite_path / "main/binary-s390x"
            suite_source_path = suite_path / "main/source"
            suite_binary_amd64_path.mkdir(parents=True)
            suite_binary_s390x_path.mkdir(parents=True)
            suite_source_path.mkdir(parents=True)
            override_path = suite_path / "override"
            override_path.write_text(
                "hello optional devel\npython3-debusine optional python\n"
            )
            override_path.with_suffix(".src").write_text("debusine misc\n")
            for path, architecture in (
                (suite_binary_amd64_path, "amd64"),
                (suite_binary_s390x_path, "s390x"),
            ):
                self.make_packages(
                    temp_path,
                    path / "Packages",
                    "pool",
                    override_path,
                    architecture=architecture,
                )
            self.make_sources(
                temp_path, suite_source_path / "Sources", "pool", override_path
            )
            self.make_release(suite_path / "Release", suite_name)
            (suite_binary_amd64_path / "Packages").unlink()
            (suite_source_path / "Sources").unlink()
            self.gpg_sign(
                private_key, suite_path / "Release", suite_path / "InRelease"
            )
            pool_files = {
                # Strip the version.  This isn't quite accurate, but it's
                # fine as long as we only care about .dsc and .deb files.
                re.sub(r"_[^_]+([_.])", r"\1", filename): (
                    filename,
                    Path(dirpath, filename).read_bytes(),
                )
                for dirpath, _, filenames in os.walk(temp_path / "pool")
                for filename in filenames
            }
            host, port = self.serve_archive(temp_path)

            result = DebusineServer.execute_command(
                "create_work_request",
                "--created-by",
                "test-user",
                "server",
                "aptmirror",
                stdin=yaml.safe_dump(
                    {
                        "collection": suite_name,
                        "url": f"http://{host}:{port}/",
                        "suite": suite_name,
                        "components": ["main"],
                        "architectures": ["amd64", "s390x"],
                        "signing_key": public_key.decode(),
                    }
                ),
            )
            self.assertEqual(result.returncode, 0)
            self.wait_and_assert_result(
                yaml.safe_load(result.stdout)["work_request_id"]
            )

        archive_url = f"http://deb.{socket.getfqdn()}/debusine/System"
        with (
            self.apt_indexes(
                suite_name,
                url=archive_url,
                signed_by=public_key.decode(),
                architectures=["amd64", "s390x"],
            ) as apt_path,
            self._temporary_directory() as temp_path,
        ):
            subprocess.run(
                ["apt-get", "source", "--download-only", "debusine"],
                cwd=temp_path,
                env=self.make_apt_environment(apt_path),
                check=True,
            )
            self.assertEqual(
                (temp_path / pool_files["debusine.dsc"][0]).read_bytes(),
                pool_files["debusine.dsc"][1],
            )
            for package, architecture in (
                ("python3-debusine", "all"),
                ("hello", "amd64"),
                ("hello", "s390x"),
            ):
                subprocess.run(
                    ["apt-get", "download", f"{package}:{architecture}"],
                    cwd=temp_path,
                    env=self.make_apt_environment(apt_path),
                    check=True,
                )
                self.assertEqual(
                    (
                        temp_path
                        / pool_files[f"{package}_{architecture}.deb"][0]
                    ).read_bytes(),
                    pool_files[f"{package}_{architecture}.deb"][1],
                )

    def test_flat(self) -> None:
        """Import and serve packages from a flat format."""
        suite_name = "sid-flat"
        self.create_suite(suite_name)
        self.add_suite_to_archive(suite_name)

        private_key, public_key = gpg_generate("test")

        with (
            self.apt_indexes("sid") as apt_path,
            self._temporary_directory() as temp_path,
        ):
            subprocess.run(
                ["apt-get", "source", "--download-only", "debusine"],
                cwd=temp_path,
                env=self.make_apt_environment(apt_path),
                check=True,
            )
            subprocess.run(
                ["apt-get", "download", "python3-debusine"],
                cwd=temp_path,
                env=self.make_apt_environment(apt_path),
                check=True,
            )
            override_path = temp_path / "override"
            override_path.write_text("python3-debusine optional python\n")
            override_path.with_suffix(".src").write_text("debusine misc\n")
            self.make_packages(
                temp_path, temp_path / "Packages", ".", override_path
            )
            self.make_sources(
                temp_path, temp_path / "Sources", ".", override_path
            )
            self.make_release(temp_path / "Release", suite_name)
            (temp_path / "Packages").unlink()
            (temp_path / "Sources").unlink()
            self.gpg_sign(
                private_key, temp_path / "Release", temp_path / "InRelease"
            )
            [dsc_name] = [
                p.name for p in temp_path.iterdir() if p.suffix == ".dsc"
            ]
            dsc_data = (temp_path / dsc_name).read_bytes()
            [deb_name] = [
                p.name for p in temp_path.iterdir() if p.suffix == ".deb"
            ]
            deb_data = (temp_path / deb_name).read_bytes()
            host, port = self.serve_archive(temp_path)

            result = DebusineServer.execute_command(
                "create_work_request",
                "--created-by",
                "test-user",
                "server",
                "aptmirror",
                stdin=yaml.safe_dump(
                    {
                        "collection": suite_name,
                        "url": f"http://{host}:{port}/",
                        "suite": "./",
                        "architectures": [self.architecture],
                        "signing_key": public_key.decode(),
                    }
                ),
            )
            self.assertEqual(result.returncode, 0)
            self.wait_and_assert_result(
                yaml.safe_load(result.stdout)["work_request_id"]
            )

        # We don't mirror indexes for flat repositories, but we can at least
        # fetch the packages directly (even though doing so via the pooled
        # layout doesn't really make sense).
        archive_url = f"http://deb.{socket.getfqdn()}/debusine/System"
        self.assertEqual(
            requests.get(
                f"{archive_url}/pool/main/d/debusine/{dsc_name}"
            ).content,
            dsc_data,
        )
        self.assertEqual(
            requests.get(
                f"{archive_url}/pool/main/d/debusine/{deb_name}"
            ).content,
            deb_data,
        )
