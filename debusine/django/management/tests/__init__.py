# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for common Django utility code."""

import io
import sys
from collections.abc import Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from typing import Any, TextIO

from django.core.management import call_command as django_call_command


def call_command(
    cmd: str,
    *args: str,
    verbosity: int = 0,
    stdin: TextIO = sys.stdin,
    **kwargs: Any,
) -> tuple[str, str, int | str]:
    """
    Call command cmd with arguments.

    Capture SystemExit's code.

    Return tuple with:
    - stdout's of the command
    - stderr's of the command
    - exit_code of the command or the string "command did not raise SystemExit \
      or call sys.exit"
    """
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code: int | str = "command did not raise SystemExit or call sys.exit"

    try:
        with (
            redirect_stderr(stderr),
            redirect_stdout(stdout),
            _redirect_stdin(stdin),
        ):
            django_call_command(cmd, *args, **kwargs, verbosity=verbosity)
    except SystemExit as exc:
        exit_code = 0 if exc.code is None else exc.code

    return stdout.getvalue(), stderr.getvalue(), exit_code


@contextmanager
def _redirect_stdin(new_stdin: TextIO) -> Generator[None, None, None]:
    """Context manager to temporarily redirect stdin."""
    old_stdin = sys.stdin
    sys.stdin = new_stdin
    try:
        yield
    finally:
        sys.stdin = old_stdin
