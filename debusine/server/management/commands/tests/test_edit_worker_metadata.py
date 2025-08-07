# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command edit_worker_metadata."""

import io
import os
import re
from typing import ClassVar, NoReturn
from unittest.mock import patch

from django.core.management import CommandError

from debusine.db.models import Token, Worker
from debusine.django.management.tests import call_command
from debusine.server.management.commands.edit_worker_metadata import (
    WorkerStaticMetadataEditor,
)
from debusine.test.django import TestCase


class EditWorkerMetadataCommandTests(TestCase):
    """Test for edit_worker_metadata command."""

    token: ClassVar[Token]
    worker: ClassVar[Worker]

    @classmethod
    def setUpTestData(cls) -> None:
        """Create a default Token and Worker."""
        super().setUpTestData()
        cls.token = Token.objects.create()
        cls.worker = Worker.objects.create_with_fqdn("worker-01.lan", cls.token)

    def test_set_worker_metadata(self) -> None:
        """Set the worker metadata from the YAML file."""
        static_metadata_file = self.create_temporary_file()
        static_metadata_file.write_bytes(
            b'sbuild:\n  architectures:\n    - amd64\n    - i386\n'
            b'  distributions:\n    - jessie\n    - stretch'
        )
        call_command(
            'edit_worker_metadata',
            '--set',
            str(static_metadata_file),
            self.worker.name,
        )

        self.worker.refresh_from_db()

        self.assertEqual(
            self.worker.static_metadata,
            {
                'sbuild': {
                    'architectures': ['amd64', 'i386'],
                    'distributions': ['jessie', 'stretch'],
                }
            },
        )

    def test_set_worker_metadata_worker_does_not_exist(self) -> None:
        """Name of the worker does not exist: raise CommandError."""
        worker_name = 'name-of-the-worker-does-not-exist.lan'
        expected_error = (
            f'Error: worker "{worker_name}" is not registered\n'
            f'Use the command "list_workers" to list the '
            f'existing workers'
        )

        with self.assertRaisesMessage(CommandError, expected_error) as exc:
            call_command(
                'edit_worker_metadata',
                '--set',
                '/tmp/some_file_not_accessed_worker_does_not_exist.yaml',
                worker_name,
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_set_worker_metadata_file_not_found(self) -> None:
        """Error message stderr if the YAML file is not found."""
        file_path = '/tmp/does_not_exist_file.yaml'

        stdout, stderr, exit_code = call_command(
            'edit_worker_metadata', '--set', file_path, self.worker.name
        )

        self.assertRegex(
            stderr,
            f"^Error: cannot open worker configuration file .*{file_path}.*\n$",
        )
        self.assertEqual(exit_code, 3)

    def test_set_worker_metadata_cannot_open_file(self) -> None:
        """Error message stderr if the YAML file cannot be opened."""
        temp_directory = self.create_temporary_directory()
        stdout, stderr, exit_code = call_command(
            'edit_worker_metadata',
            '--set',
            temp_directory.as_posix(),
            self.worker.name,
        )

        self.assertRegex(
            stderr,
            "^Error: cannot open worker configuration file "
            f"'{temp_directory}':.*{temp_directory}.*\n$",
        )
        self.assertEqual(exit_code, 3)

    def test_set_worker_metadata_file_is_invalid(self) -> None:
        """Error message stderr if the YAML file is not found."""
        static_metadata_file = self.create_temporary_file(suffix=".yml")
        static_metadata_file.write_bytes(b'a\nb:')

        stdout, stderr, exit_code = call_command(
            'edit_worker_metadata',
            '--set',
            str(static_metadata_file),
            self.worker.name,
        )

        self.assertRegex(
            stderr, 'Invalid YAML: mapping values are not allowed here.*'
        )
        self.assertEqual(exit_code, 3)

    def test_edit_interactive_worker_valid_yaml(self) -> None:
        """Editor changes the contents of Worker's YAML metadata."""
        stdout = io.StringIO()
        stderr = io.StringIO()

        worker_editor = WorkerStaticMetadataEditor(
            self.worker, yaml_file=None, stdout=stdout, stderr=stderr
        )

        def _set_valid_file_contents(file_path: str) -> None:
            with open(file_path, 'w') as file:
                file.write('distributions:\n - buster\n - bullseye')

        with patch.object(
            worker_editor, '_open_editor', new=_set_valid_file_contents
        ):
            worker_editor.edit()

        self.worker.refresh_from_db()

        self.assertEqual(
            self.worker.static_metadata,
            {'distributions': ['buster', 'bullseye']},
        )

        self.assertEqual(
            stdout.getvalue(), 'debusine: metadata set for worker-01-lan'
        )
        self.assertEqual(stderr.getvalue(), '')

    def test_edit_worker_metadata_not_dictionary(self) -> None:
        """Return error when the metadata file is a string."""
        static_metadata_file = self.create_temporary_file(
            suffix=".yml", contents=b"bullseye"
        )
        stdout, stderr, exit_code = call_command(
            'edit_worker_metadata',
            '--set',
            str(static_metadata_file),
            self.worker.name,
        )

        self.assertEqual(
            stderr, 'Worker metadata must be a dictionary or empty\n'
        )
        self.assertEqual(exit_code, 3)

    def test_edit_interactive_worker_invalid_yaml(self) -> None:
        """
        Editor changes the contents of Worker's static data to invalid YAML.

        Then it aborts the editing.
        """
        stdout = io.StringIO()
        stderr = io.StringIO()

        self.worker.static_metadata = {'distributions': ['potato']}
        self.worker.save()

        worker_editor = WorkerStaticMetadataEditor(
            self.worker, yaml_file=None, stdout=stdout, stderr=stderr
        )

        def _set_invalid_file_contents(file_path: str) -> None:
            with open(file_path, 'w') as file:
                file.write('"')

        with (
            patch.object(
                worker_editor, '_open_editor', new=_set_invalid_file_contents
            ),
            patch.object(worker_editor, '_input', return_value='n'),
        ):
            worker_editor.edit()

        self.worker.refresh_from_db()

        self.assertEqual(
            self.worker.static_metadata, {'distributions': ['potato']}
        )

        stdout_text = stdout.getvalue()
        stderr_text = stderr.getvalue()

        self.assertRegex(
            stdout_text,
            r'Do you want to retry the same edit\? \(y/n\)\n'
            r'debusine: edits left in (.+?)\.yaml',
        )

        self.assertRegex(stderr_text, 'Invalid YAML: .*')

        # Check contents of the file
        m = re.search(r'debusine: edits left in (.+?\.yaml)$', stdout_text)
        assert m is not None
        edits_left_file_path = m.group(1)

        self.assertTrue(os.path.exists(edits_left_file_path))
        with open(edits_left_file_path) as fd:
            self.assertEqual(fd.read(), '"')

        os.remove(edits_left_file_path)

    def test_edit_interactive_worker_empty_yaml(self) -> None:
        """Editor sets the contents of Worker's YAML to an empty file."""
        stdout = io.StringIO()
        stderr = io.StringIO()

        worker_editor = WorkerStaticMetadataEditor(
            self.worker, yaml_file=None, stdout=stdout, stderr=stderr
        )

        def _set_empty_yaml(file_path: str) -> None:
            with open(file_path, 'w') as file:
                file.write('')

        def _assert_not_called() -> NoReturn:
            self.fail("should not have been called")  # pragma: no cover

        with (
            patch.object(worker_editor, '_open_editor', new=_set_empty_yaml),
            patch.object(worker_editor, '_input', new=_assert_not_called),
        ):
            worker_editor.edit()

        self.worker.refresh_from_db()
        self.assertEqual(self.worker.static_metadata, {})

    def test_edit_interactive_worker_invalid_then_valid_yaml(self) -> None:
        """
        Editor changes the contents of Worker's static data to invalid YAML.

        Then it fixes the YAML file.
        """
        self.worker.static_metadata = {'distributions': ['potato']}
        self.worker.save()

        stdout = io.StringIO()
        stderr = io.StringIO()

        worker_editor = WorkerStaticMetadataEditor(
            self.worker, yaml_file=None, stdout=stdout, stderr=stderr
        )

        def _verify_and_edit(file_path: str) -> None:
            """
            Write test YAML into file_path.

            The first that it is called it writes invalid YAML,
            the second time writes correct YAML.
            """
            execution_count = (
                getattr(_verify_and_edit, '_execution_count', 0) + 1
            )

            if execution_count == 1:
                setattr(_verify_and_edit, '_executed', True)
                with open(file_path) as file:
                    self.assertEqual(file.read(), 'distributions:\n- potato\n')
                contents = '&'  # Invalid YAML
            else:
                with open(file_path) as file:
                    self.assertEqual(file.read(), '&')

                contents = 'distributions:\n - bookworm'

            with open(file_path, 'w') as file:
                file.write(contents)

            setattr(_verify_and_edit, '_execution_count', execution_count)

        with (
            patch.object(worker_editor, '_open_editor', new=_verify_and_edit),
            patch.object(worker_editor, '_input', return_value='y'),
        ):
            worker_editor.edit()

        self.worker.refresh_from_db()

        self.assertEqual(
            self.worker.static_metadata, {'distributions': ['bookworm']}
        )

        stdout_text = stdout.getvalue()
        stderr_text = stderr.getvalue()

        self.assertRegex(stderr_text, 'Invalid YAML: .*')

        self.assertEqual(
            'Do you want to retry the same edit? (y/n)\n'
            'debusine: metadata set for worker-01-lan',
            stdout_text,
        )
