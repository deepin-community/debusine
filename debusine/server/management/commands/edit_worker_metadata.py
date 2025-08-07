# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to edit workers metadata."""

import io
import os
import shutil
import subprocess
import tempfile
from typing import Any, NoReturn

import yaml
from django.core.management import CommandError, CommandParser

from debusine.db.models import Worker
from debusine.django.management.debusine_base_command import DebusineBaseCommand


class WorkerStaticMetadataEditor:
    """Edit Worker static metadata."""

    def __init__(
        self,
        worker: Worker,
        yaml_file: str | None,
        stdout: io.TextIOBase,
        stderr: io.TextIOBase,
    ) -> None:
        """
        Initialize fields of WorkerStaticMetadataEditor.

        :param worker: worker that is going to be edited
        :param yaml_file: edit() method will read the metadata from it.
          If it is None it will load the metadata from the database.
        :param stdout: stdout file.
        :param stderr: stderr file.
        """
        self._worker = worker
        self._yaml_file = yaml_file
        self._stdout = stdout
        self._stderr = stderr

    def edit(self) -> bool:
        """Edit the Worker's static_metadata."""
        if self._yaml_file:
            metadata = self._read_yaml_file(self._yaml_file)
        else:
            metadata = self._edit_worker_metadata(
                self._worker.static_metadata, self._stdout, self._stderr
            )

        if metadata is not None:
            self._worker.static_metadata = metadata
            self._worker.save()
            self._stdout.write(
                f'debusine: metadata set for {self._worker.name}'
            )
            return True
        else:
            return False

    def _read_yaml_file(self, yaml_path: str) -> dict[Any, Any] | None:
        """
        Read yaml_path and returns it as dictionary (or None if invalid).

        :param yaml_path: parses it. Be aware:
          * If it's an empty object returns {}.

          * If it does not contain a dictionary structure: writes a message to
            self.stderr and returns None.

          * If it cannot be opened writes a message to self.stderr and
            it raises SystemExit(3).
        """
        try:
            with open(yaml_path) as yaml_file:
                try:
                    contents = yaml.safe_load(yaml_file)
                except (yaml.YAMLError, yaml.scanner.ScannerError) as exc:
                    self._stderr.write(f'Invalid YAML: {exc}')
                    return None
        except OSError as exc:
            self._stderr.write(
                f"Error: cannot open worker configuration file "
                f"'{yaml_path}': {exc}\n"
            )
            raise SystemExit(3)

        if contents is None:
            # Empty YAML is normalized to an empty dictionary
            contents = {}
        elif not isinstance(contents, dict):
            self._stderr.write('Worker metadata must be a dictionary or empty')
            return None

        assert isinstance(contents, dict)
        return contents

    @staticmethod
    def _input() -> str:
        return input()  # pragma: no cover

    def _edit_worker_metadata(
        self,
        metadata: dict[str, Any] | None,
        stdout: io.TextIOBase,
        stderr: io.TextIOBase,
    ) -> dict[str, Any] | None:
        edit_metadata = tempfile.NamedTemporaryFile(
            prefix='debusine-edit_worker_metadata_',
            suffix='.tmp.yaml',
            delete=False,
        )
        edit_metadata.close()

        original_metadata = tempfile.NamedTemporaryFile(
            prefix='debusine-edit_worker_metadata', suffix='.yaml', delete=False
        )
        original_metadata.close()

        with open(original_metadata.name, 'w') as original_yaml_file:
            yaml.safe_dump(data=metadata, stream=original_yaml_file)

        shutil.copy2(original_metadata.name, edit_metadata.name)

        while True:
            self._open_editor(edit_metadata.name)

            metadata = self._read_yaml_file(edit_metadata.name)

            if metadata is not None:
                os.unlink(edit_metadata.name)
                os.unlink(original_metadata.name)
                return metadata
            else:
                stderr.write('Error reading metadata.\n')
                stdout.write('Do you want to retry the same edit? (y/n)\n')
                answer = self._input()

                if answer != 'y':
                    os.unlink(original_metadata.name)
                    stdout.write(
                        f'debusine: edits left in {edit_metadata.name}'
                    )
                    return None

    @staticmethod
    def _open_editor(file_path: str) -> None:
        # sensible-editor is in sensible-utils package
        subprocess.run(['sensible-editor', file_path])  # pragma: no cover


class Command(DebusineBaseCommand):
    """Command to edit the metadata of a Worker."""

    help = "Edit worker's metadata"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add CLI arguments for the edit_worker_metadata command."""
        parser.add_argument(
            '--set',
            help=(
                "Filename with the metadata in YAML"
                " (note: its contents will be displayed to users in the web UI)"
            ),
            metavar='PATH',
        )
        parser.add_argument(
            'worker_name',
            help='Name of the worker of which the metadata will be edited',
        )

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Edit the Worker based on the command options."""
        worker_name = options['worker_name']

        try:
            worker = Worker.objects.get(name=worker_name)
        except Worker.DoesNotExist:
            raise CommandError(
                f'Error: worker "{worker_name}" is not registered\n'
                f'Use the command "list_workers" to list the existing workers',
                returncode=3,
            )

        worker_editor = WorkerStaticMetadataEditor(
            worker, options['set'], self.stdout, self.stderr
        )
        if worker_editor.edit():
            raise SystemExit(0)
        else:
            raise SystemExit(3)
