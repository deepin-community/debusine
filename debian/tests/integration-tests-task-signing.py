#!/usr/bin/env python3

# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Debusine integration tests.

Test signing service.
"""

import base64
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest import TestCase

import requests
import yaml

from debusine.artifacts.models import ArtifactCategory
from debusine.assets.models import SigningKeyData
from debusine.client.models import FileResponse
from utils.client import Client
from utils.common import Configuration
from utils.integration_test_helpers_mixin import IntegrationTestHelpersMixin
from utils.server import DebusineServer


class IntegrationSigningTests(IntegrationTestHelpersMixin, TestCase):
    """
    Integration tests for the signing service.

    These tests assume:
    - debusine-server is running
    - debusine-signing is running (connected to the server)
    - debusine-client is correctly configured
    """

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        # If debusine-server or nginx was launched just before this test,
        # then debusine-server might not be available yet.  Wait for
        # debusine-server to be reachable if it's not ready.
        self.assertTrue(
            DebusineServer.wait_for_server_ready(),
            f"debusine-server should be available (in "
            f"{Configuration.get_base_url()}) before the integration tests "
            f"are run",
        )
        self.signing_input_files: dict[str, bytes] = {}

    @classmethod
    def download_and_extract_binary(
        cls, apt_path: Path, package_name: str, target: Path
    ) -> Path:
        """Download and extract a binary package."""
        subprocess.check_call(
            ["apt-get", "download", package_name],
            cwd=target,
            env=cls.make_apt_environment(apt_path),
        )
        subprocess.check_call(
            [
                "dpkg-deb",
                "-x",
                next(target.glob(f"{package_name}_*.deb")),
                package_name,
            ],
            cwd=target,
        )
        return target / package_name

    @classmethod
    def verify_uefi_signature(
        cls,
        signing_key_data: SigningKeyData,
        image_data: bytes,
        signed_file: FileResponse,
    ) -> None:
        """Verify a signed file against its UEFI signing key."""
        with cls._temporary_directory() as temp_path:
            (certificate := temp_path / "uefi.crt").write_bytes(
                base64.b64decode(signing_key_data.public_key)
            )
            (image := temp_path / "image").write_bytes(image_data)
            (signature := temp_path / "image.sig").write_bytes(
                requests.get(signed_file["url"]).content
            )
            subprocess.check_call(
                [
                    "sbverify",
                    "--cert",
                    certificate,
                    "--detached",
                    signature,
                    image,
                ],
                cwd=temp_path,
            )

    def generate_signing_key(self) -> int:
        """Generate a signing key. Return the task ID."""
        result = DebusineServer.execute_command(
            "create_work_request",
            "signing",
            "generatekey",
            stdin=yaml.safe_dump(
                {"purpose": "uefi", "description": "Test key"}
            ),
        )
        self.assertEqual(result.returncode, 0)
        work_request_id = yaml.safe_load(result.stdout)["work_request_id"]
        return work_request_id

    def grant_sign_permission(self, asset_id: int) -> None:
        """Grant the test user permission to sign with asset."""
        result = DebusineServer.execute_command(
            "asset",
            "grant_role",
            str(asset_id),
            "--workspace",
            "debusine/System",
            "signer",
            "Admins",
        )
        self.assertEqual(result.returncode, 0)

    def find_created_asset(self, work_request_id: int) -> dict[str, Any]:
        """Return a dict describing the asset created by work_request_id."""
        result = Client.execute_command(
            "list-assets", "--work-request", str(work_request_id)
        )
        return yaml.safe_load(result.stdout)[0]

    def grub_signed_package_name(self) -> str | None:
        """Return the name of a signed grub-efi binary package."""
        dpkg_architecture = subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True
        ).strip()
        match dpkg_architecture:
            case "amd64":
                return "grub-efi-amd64-signed"
            case "i386":
                return "grub-efi-ia32-signed"
            case "arm64":
                return "grub-efi-arm64-signed"
            case _:
                return None

    def sign_template_package(
        self, template_package_name: str, fingerprint: str
    ) -> int:
        """Sign a template package. Return the task id."""
        self.signing_input_files = {}
        with (
            self._temporary_directory() as temp_path,
            self.apt_indexes("bookworm") as apt_path,
        ):
            extracted_template = self.download_and_extract_binary(
                apt_path, template_package_name, temp_path
            )
            packages = json.loads(
                (
                    extracted_template
                    / "usr/share/code-signing"
                    / template_package_name
                    / "files.json"
                ).read_text()
            )["packages"]

            for package_name, metadata in packages.items():
                extracted_package = self.download_and_extract_binary(
                    apt_path, package_name, temp_path
                )
                for file in metadata["files"]:
                    self.assertEqual(file["sig_type"], "efi")
                    self.assertFalse(file["file"].startswith("/"))
                    self.signing_input_files[
                        f"{package_name}/{file['file']}"
                    ] = (extracted_package / file["file"]).read_bytes()

            signing_input_id = self.create_artifact_signing_input(
                template_package_name, self.signing_input_files
            )

        result = DebusineServer.execute_command(
            "create_work_request",
            "signing",
            "sign",
            "--created-by",
            "test-user",
            stdin=yaml.safe_dump(
                {
                    "purpose": "uefi",
                    "unsigned": [signing_input_id],
                    "key": fingerprint,
                }
            ),
        )
        self.assertEqual(result.returncode, 0)
        work_request_id = yaml.safe_load(result.stdout)["work_request_id"]
        return work_request_id

    def wait_and_assert_result(
        self, work_request_id: int, result: str = "success"
    ) -> None:
        """Wait for a task to complete, assert success."""
        status = Client.wait_for_work_request_completed(work_request_id, result)
        if not status:
            self.print_work_request_debug_logs(work_request_id)
        if status == "success":
            self.assertTrue(status)

    def assert_successful_sign(
        self, work_request_id: int, signing_key_data: SigningKeyData
    ) -> int:
        """Wait for sign work request, assert success, return output id."""
        work_request = Client.execute_command(
            "show-work-request", work_request_id
        )

        # The requested files were signed.
        [signing_output_artifact] = [
            artifact
            for artifact in work_request["artifacts"]
            if artifact["category"] == ArtifactCategory.SIGNING_OUTPUT
        ]
        self.assertEqual(signing_output_artifact["data"]["purpose"], "uefi")
        self.assertEqual(
            signing_output_artifact["data"]["fingerprint"],
            signing_key_data.fingerprint,
        )
        self.assertCountEqual(
            signing_output_artifact["data"]["results"],
            [
                {
                    "file": name,
                    "output_file": f"{name}.sig",
                    "error_message": None,
                }
                for name in self.signing_input_files
            ],
        )
        self.assertCountEqual(
            list(signing_output_artifact["files"]),
            [f"{name}.sig" for name in self.signing_input_files],
        )
        for name in self.signing_input_files:
            self.verify_uefi_signature(
                signing_key_data,
                self.signing_input_files[name],
                signing_output_artifact["files"][f"{name}.sig"],
            )
        return signing_output_artifact["id"]

    def assemble_signed_source(
        self, template_package_name: str, signing_output_artifact_id: int
    ) -> None:
        """Assemble a signed source package, return the task."""
        # We need an environment for this.
        self.create_system_images_mmdebstrap(["bookworm"])
        with self.apt_indexes("bookworm") as apt_path:
            template_artifact_id = self.create_artifact_binary(
                apt_path, template_package_name
            )

        work_request_id = Client.execute_command(
            "create-work-request",
            "assemblesignedsource",
            stdin=yaml.safe_dump(
                {
                    "environment": "debian/match:codename=bookworm",
                    "template": template_artifact_id,
                    "signed": [signing_output_artifact_id],
                }
            ),
        )["work_request_id"]
        return work_request_id

    def assert_successful_assembly(
        self, work_request_id: int, signed_package_name: str
    ) -> None:
        """Examine the results of a sign work request, assert signatures."""
        work_request = Client.execute_command(
            "show-work-request", work_request_id
        )

        # The resulting source package contains all the signed files.
        [source_package_artifact] = [
            artifact
            for artifact in work_request["artifacts"]
            if artifact["category"] == ArtifactCategory.SOURCE_PACKAGE
        ]
        self.assertEqual(
            source_package_artifact["data"]["name"], signed_package_name
        )
        with self._temporary_directory() as temp_path:
            Client.execute_command(
                "download-artifact",
                "--target-directory",
                temp_path,
                source_package_artifact["id"],
            )
            subprocess.check_call(
                [
                    "dpkg-source",
                    "-x",
                    next(temp_path.glob("*.dsc")).name,
                    "unpacked",
                ],
                cwd=temp_path,
            )
            for name in self.signing_input_files:
                self.assertTrue(
                    (
                        temp_path
                        / "unpacked"
                        / "debian"
                        / "signatures"
                        / f"{name}.sig"
                    ).exists(),
                    f"Assembled source package lacks signature of {name}",
                )

    def test_generate_and_sign(self) -> None:
        """Generate a key and sign something with it."""
        generate_task_id = self.generate_signing_key()
        self.wait_and_assert_result(generate_task_id)
        signing_asset = self.find_created_asset(generate_task_id)
        signing_key_data = SigningKeyData.parse_obj(signing_asset["data"])
        signed_package_name = self.grub_signed_package_name()
        if signed_package_name is None:
            # Not all architectures have signed grub, just stop here
            return
        template_package_name = f"{signed_package_name}-template"

        # No permission yet, we expect failure
        signing_task_id = self.sign_template_package(
            template_package_name, signing_key_data.fingerprint
        )
        self.wait_and_assert_result(signing_task_id, result="failure")

        # Grant permission, expect success
        self.grant_sign_permission(signing_asset["id"])
        signing_task_id = self.sign_template_package(
            template_package_name, signing_key_data.fingerprint
        )
        self.wait_and_assert_result(signing_task_id)

        signing_output_artifact_id = self.assert_successful_sign(
            signing_task_id, signing_key_data
        )
        assemble_signed_source_task_id = self.assemble_signed_source(
            template_package_name, signing_output_artifact_id
        )
        self.wait_and_assert_result(assemble_signed_source_task_id)
        self.assert_successful_assembly(
            assemble_signed_source_task_id, signed_package_name
        )
