# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""UEFI Secure Boot signing utility for the Debusine signing service."""

import os
import subprocess
from pathlib import Path
from typing import BinaryIO

from debusine.signing.models import ProtectedKey


def sbsign(
    private_key: ProtectedKey,
    public_key: bytes,
    data_path: Path,
    signature_path: Path,
    detached: bool = False,
    log_file: BinaryIO | None = None,
) -> None:
    """Sign a UEFI boot image using `sbsign`."""
    with private_key.available(public_key) as available:
        cmd: list[str | os.PathLike[str]] = ["sbsign", *available.sbsign_args]
        if detached:
            cmd.append("--detached")
        cmd.extend(["--output", signature_path, data_path])
        subprocess.run(cmd, check=True, stdout=log_file, stderr=log_file)
