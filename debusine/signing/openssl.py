# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""OpenSSL utilities for the Debusine signing service."""

import re
import subprocess
from pathlib import Path
from typing import BinaryIO

from debusine.signing.signing_utils import SensitiveTemporaryDirectory


def openssl_generate(
    common_name: str, certificate_days: int, log_file: BinaryIO | None = None
) -> tuple[bytes, bytes]:
    """Generate a new private key and certificate using OpenSSL."""
    with SensitiveTemporaryDirectory("debusine-openssl-generate-") as tmp:
        key = Path(tmp, "tmp.key")
        certificate = Path(tmp, "tmp.crt")
        # openssl-req(1ssl) says: 'Special characters may be escaped by "\"
        # (backslash), whitespace is retained.'  "/" and "=" are special
        # since they are used to delimit successive types and values.
        quoted_common_name = re.sub(r'([/=])', r'\\\1', common_name)
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-newkey",
                "rsa:2048",
                "-x509",
                "-subj",
                f"/CN={quoted_common_name}/",
                "-days",
                str(certificate_days),
                "-noenc",
                "-sha256",
                "-keyout",
                key,
                "-out",
                certificate,
            ],
            check=True,
            stdout=log_file,
            stderr=log_file,
        )
        return key.read_bytes(), certificate.read_bytes()


def x509_fingerprint(
    certificate: bytes, log_file: BinaryIO | None = None
) -> str:
    """Get the SHA-256 fingerprint of an X.509 certificate."""
    return (
        subprocess.run(
            [
                "openssl",
                "x509",
                "-inform",
                "PEM",
                "-noout",
                "-fingerprint",
                "-sha256",
            ],
            check=True,
            input=certificate,
            stdout=subprocess.PIPE,
            stderr=log_file,
        )
        .stdout.decode()
        .rstrip("\n")
        # "sha256 Fingerprint=AA:BB:CC:..."
        .split("=", 1)[1]
        .replace(":", "")
    )
