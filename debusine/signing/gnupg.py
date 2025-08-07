# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""GnuPG utilities for the Debusine signing service."""

import os
import shutil
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

import gpg

from debusine.signing.models import AvailableKeyFileSystem, ProtectedKey
from debusine.signing.signing_utils import SensitiveTemporaryDirectory


class GnuPGError(Exception):
    """A GnuPG operation failed."""


# Based loosely on EphemeralContext in gpgme's lang/python/tests/support.py.
@contextmanager
def gpg_ephemeral_context(
    temp_dir: Path,
) -> Generator[gpg.Context, None, None]:
    """Run gpg, cleaning up properly afterwards."""
    home = temp_dir / "gpg"
    home.mkdir(mode=0o700)
    try:
        (home / "gpg.conf").write_text(
            # Prefer a SHA-2 hash that's long enough to use with Ed25519.
            # This roughly matches GnuPG's defaults, except without SHA-1
            # and SHA-224; and it seems that Debian's patch to prefer
            # SHA-512 when signing doesn't apply to all key types.
            "personal-digest-preferences SHA512 SHA384 SHA256\n"
        )
        with gpg.Context(home_dir=str(home)) as ctx:
            ctx.armor = True
            yield ctx
            gpgconf_env = os.environ.copy()
            gpgconf_env["GNUPGHOME"] = str(home)
            subprocess.run(["gpgconf", "--kill", "all"], env=gpgconf_env)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def gpg_generate(description: str) -> tuple[bytes, bytes]:
    """Generate a new key pair using GnuPG."""
    with (
        SensitiveTemporaryDirectory("debusine-gnupg-generate-") as tmp,
        gpg_ephemeral_context(Path(tmp)) as ctx,
    ):
        # TODO: Ed25519 is good enough for most purposes, but we should
        # really allow the caller to set the algorithm and key length.  In
        # particular, GnuPG 1 doesn't support Ed25519, so if we need
        # compatibility with that then we'd need to support something like
        # RSA.
        key = ctx.create_key(
            description, algorithm="ed25519", expires=False, sign=True
        )
        if not key.primary:
            raise GnuPGError("Failed to generate a secret key")
        if key.sub:
            raise GnuPGError("Unexpectedly got an encryption subkey")
        secret = ctx.key_export_secret(key.fpr)
        if not secret:
            raise GnuPGError("Failed to export secret key")
        public = ctx.key_export_minimal(key.fpr)
        if not public:
            raise GnuPGError("Failed to export public key")
        return secret, public


def gpg_fingerprint(public_key: bytes) -> str:
    """Get the fingerprint of a GnuPG public key."""
    with (
        TemporaryDirectory("debusine-gnupg-import-") as tmp,
        gpg_ephemeral_context(Path(tmp)) as ctx,
    ):
        result = ctx.key_import(public_key)
        if not getattr(result, "imported", 0):
            raise GnuPGError(f"Failed to get fingerprint of new key: {result}")
        assert isinstance(result.imports[0].fpr, str)
        return result.imports[0].fpr


def gpg_sign(
    private_key: ProtectedKey,
    public_key: bytes,
    data_path: Path,
    signature_path: Path,
) -> None:
    """Sign data using GnuPG."""
    with private_key.available(public_key) as available:
        if not isinstance(available, AvailableKeyFileSystem):
            raise GnuPGError(
                "OpenPGP signing currently only supports software-encrypted "
                "keys"
            )
        with (
            SensitiveTemporaryDirectory("debusine-gnupg-sign-") as tmp,
            gpg_ephemeral_context(Path(tmp)) as ctx,
        ):
            import_result = ctx.key_import(available._key_path.read_bytes())
            if not getattr(import_result, "secret_imported", 0):
                raise GnuPGError("Failed to import secret key")
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
                    # TODO: Cleartext signatures get us what we need to sign
                    # uploads, but we should also support detached
                    # signatures.
                    mode=gpg.constants.SIG_MODE_CLEAR,
                )


def gpg_debsign(
    private_key: ProtectedKey,
    public_key: bytes,
    fingerprint: str,
    data_path: Path,
    signature_path: Path,
    log_file: BinaryIO | None = None,
) -> None:
    """
    Sign an upload using GnuPG and `debsign`.

    :param data_path: path to the unsigned `.changes` file.  Other files
      referenced by it that need to be signed must be in the same directory.
    :param signature_path: path to the signed `.changes` file.  Other files
      referenced by it that were signed are in the same directory.
    """
    with private_key.available(public_key) as available:
        if not isinstance(available, AvailableKeyFileSystem):
            raise GnuPGError(
                "OpenPGP signing currently only supports software-encrypted "
                "keys"
            )
        with (
            SensitiveTemporaryDirectory("debusine-debsign-") as tmp,
            gpg_ephemeral_context(Path(tmp)) as ctx,
        ):
            import_result = ctx.key_import(available._key_path.read_bytes())
            if not getattr(import_result, "secret_imported", 0):
                raise GnuPGError("Failed to import secret key")

            if list(signature_path.parent.iterdir()):
                raise GnuPGError(
                    "Directory containing signature_path is not empty"
                )
            shutil.copytree(
                data_path.parent, signature_path.parent, dirs_exist_ok=True
            )

            subprocess.run(
                ["debsign", f"-k{fingerprint}", "--re-sign", signature_path],
                cwd=signature_path.parent,
                env={**os.environ, "GNUPGHOME": str(ctx.home_dir)},
                check=True,
                stdout=log_file,
                stderr=log_file,
            )
