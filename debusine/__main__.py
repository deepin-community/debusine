# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Django-based command-line utility for administrative tasks."""

import os
import sys

from django.core.exceptions import ImproperlyConfigured


def main() -> None:
    """Run a management command."""
    basename = os.path.basename(sys.argv[0])

    # Drop privileges if running one of the packaged command wrappers as
    # root.
    match os.geteuid(), basename:
        case 0, "debusine-admin":
            os.execvp(
                "runuser",
                ["runuser", "--user=debusine-server", "--", *sys.argv],
            )
        case 0, "debusine-signing":
            os.execvp(
                "runuser",
                ["runuser", "--user=debusine-signing", "--", *sys.argv],
            )

    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        (
            "debusine.signing.settings"
            if basename == "debusine-signing"
            else "debusine.project.settings"
        ),
    )

    # Must only be imported after DJANGO_SETTINGS_MODULE is set.
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    try:
        execute_from_command_line(sys.argv)
    except ImproperlyConfigured as exc:
        print("Improperly configured error:", exc, file=sys.stderr)
        sys.exit(3)
    except ValueError as exc:
        if isinstance(exc.__cause__, PermissionError):
            filename = exc.__cause__.filename or "Unknown"
            print(
                f"Permission error: {exc}. Check that the user running "
                f'{basename} has access to the file "{filename}".',
                file=sys.stderr,
            )
            sys.exit(3)
        else:
            raise
