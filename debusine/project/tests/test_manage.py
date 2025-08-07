#  Copyright © The Debusine Developers
#  See the AUTHORS file at the top-level directory of this distribution
#
#  This file is part of Debusine. It is subject to the license terms
#  in the LICENSE file found in the top-level directory of this
#  distribution. No part of Debusine, including this file, may be copied,
#  modified, propagated, or distributed except according to the terms
#  contained in the LICENSE file.

"""Unit tests for manage.py."""

import contextlib
import io
from unittest import mock

from django.core.exceptions import ImproperlyConfigured

from debusine.__main__ import main
from debusine.test.django import TestCase


class DefaultSettingsTests(TestCase):
    """Tests for manage.py. Named debusine-admin when packaged."""

    def patch_execute_from_command_line(self, exception: BaseException) -> None:
        """Patch execute_from_command_line: raise exception."""
        patcher = mock.patch(
            'django.core.management.execute_from_command_line', autospec=True
        )
        mocked_pathlib_owner = patcher.start()
        mocked_pathlib_owner.side_effect = exception
        self.addCleanup(patcher.stop)

    def assert_main_drops_privileges(
        self, program_name: str, target_username: str
    ) -> None:
        """Call main(), asserting that it drops privileges."""
        with (
            mock.patch("sys.argv", [program_name, "--version"]),
            mock.patch("os.geteuid", return_value=0),
            mock.patch("os.execvp", side_effect=OSError) as mock_execvp,
            self.assertRaises(OSError),
        ):
            main()

        mock_execvp.assert_called_once_with(
            "runuser",
            [
                "runuser",
                f"--user={target_username}",
                "--",
                program_name,
                "--version",
            ],
        )

    def assert_main_does_not_drop_privileges(self, program_name: str) -> None:
        """Call main(), asserting that it doesn't drop privileges."""
        stdout = io.StringIO()

        with (
            mock.patch("sys.argv", [program_name, "--version"]),
            mock.patch("os.geteuid", return_value=1),
            mock.patch("os.execvp", side_effect=OSError) as mock_execvp,
            contextlib.redirect_stdout(stdout),
        ):
            main()

        mock_execvp.assert_not_called()

    def assert_main_system_exit_with_stderr(
        self, stderr_expected: str, program_name: str = "debusine-admin"
    ) -> None:
        """Call main() assert system exit code is 3 and stderr as expected."""
        stderr = io.StringIO()

        with (
            mock.patch("sys.argv", [program_name]),
            mock.patch("os.geteuid", return_value=1),
            contextlib.redirect_stderr(stderr),
            self.assertRaisesSystemExit(3),
        ):
            main()

        self.assertEqual(stderr.getvalue(), stderr_expected)

    def test_debusine_admin_as_root_drops_privileges(self) -> None:
        """main() drops privs if running debusine-admin as root."""
        self.assert_main_drops_privileges("debusine-admin", "debusine-server")

    def test_debusine_admin_as_non_root_does_not_drop_privileges(self) -> None:
        """main() doesn't drop privs if running debusine-admin as non-root."""
        self.assert_main_does_not_drop_privileges("debusine-admin")

    def test_debusine_signing_as_root_drops_privileges(self) -> None:
        """main() drops privs if running debusine-signing as root."""
        self.assert_main_drops_privileges(
            "debusine-signing", "debusine-signing"
        )

    def test_debusine_signing_as_non_root_does_not_drop_privileges(
        self,
    ) -> None:
        """main() doesn't drop privs if running debusine-signing as non-root."""
        self.assert_main_does_not_drop_privileges("debusine-signing")

    def test_improperly_configured_output(self) -> None:
        """main() handle ImproperlyConfigured from execute_from_command_line."""
        self.patch_execute_from_command_line(ImproperlyConfigured("Error msg"))

        self.assert_main_system_exit_with_stderr(
            "Improperly configured error: Error msg\n"
        )

    def test_raise_error(self) -> None:
        """main() raise non-handled exception."""
        self.patch_execute_from_command_line(OSError("Some error"))

        with (
            mock.patch("sys.argv", ["debusine-admin", "--version"]),
            mock.patch("os.geteuid", return_value=1),
            self.assertRaises(OSError),
        ):
            main()

    def assert_raise_error_permission_error(
        self, permission_error: PermissionError, msg: str
    ) -> None:
        """Assert that permission_error is raised msg is in the stderr."""
        exception = ValueError("Some error")
        exception.__cause__ = permission_error

        self.patch_execute_from_command_line(exception)

        self.assert_main_system_exit_with_stderr(msg)

    def test_permission_error_raised_unknown_file(self) -> None:
        """main() handle ValueError caused by PermissionError."""
        permission_error = PermissionError("Internal error")

        self.assert_raise_error_permission_error(
            permission_error,
            "Permission error: Some error. Check that the user running "
            'debusine-admin has access to the file "Unknown".\n',
        )

    def test_permission_error_raised_file(self) -> None:
        """main() handle ValueError caused by PermissionError."""
        permission_error = PermissionError("Internal error")

        permission_error.filename = "/var/log/debusine/server/debug.log"

        self.assert_raise_error_permission_error(
            permission_error,
            "Permission error: Some error. Check that the user running "
            "debusine-admin has access to the file "
            f'"{permission_error.filename}".\n',
        )

    def test_value_error(self) -> None:
        """main() handles ValueError not caused by PermissionError."""
        self.patch_execute_from_command_line(ValueError("Some error"))

        with self.assertRaisesRegex(ValueError, "Some error"):
            main()
