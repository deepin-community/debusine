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

Find the most recent task's debug logs and extract them to the specified path.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, cast

import yaml

sys.path.append(str(Path(__file__).parent.parent))

from utils.client import Client  # noqa: E402


def last_work_request() -> dict[str, Any]:
    """Return the most recent work request."""
    result = Client.execute_command("list-work-requests", "--limit", "1")
    try:
        data = yaml.safe_load(result.stdout)
        return cast(dict[str, Any], data["results"][0])
    except Exception as exc:
        exc.add_note(f"Stdout: {result.stdout}")
        exc.add_note(f"Stderr: {result.stderr}")
        raise


def show_artifact(artifact_id: str | int) -> dict[str, Any]:
    """Return details for artifact_id."""
    result = Client.execute_command("show-artifact", str(artifact_id))
    return cast(dict[str, Any], yaml.safe_load(result.stdout))


def extract_last_debug_logs_to(destdir: Path) -> None:
    """Extract the debug logs for the last work request to destdir."""
    work_request = last_work_request()
    for artifact_id in work_request["artifacts"]:
        artifact = show_artifact(artifact_id)
        if artifact["category"] == "debusine:work-request-debug-logs":
            Client.execute_command(
                "download-artifact",
                "--target-directory",
                str(destdir),
                artifact_id,
            )


def main() -> None:
    """Parse arguments and run the job."""
    p = argparse.ArgumentParser()
    p.add_argument(
        "destdir", type=Path, help="Directory to extract artifact into."
    )
    args = p.parse_args()

    extract_last_debug_logs_to(args.destdir)


if __name__ == "__main__":
    main()
