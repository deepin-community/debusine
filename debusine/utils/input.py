# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Common functions for user input."""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Generic, TypeVar

import yaml

T = TypeVar("T")


class InvalidData(ValueError):
    """Exception raised when the user entered invalid data."""


class EditorInteractionError(RuntimeError):
    """
    Exception raised when interaction with the editor failed.

    This could be the editor not being found, or a failure to open the edited
    file.
    """


class YamlEditor(Generic[T]):
    """Let the user edit a data structure as YAML in a text editor."""

    def __init__(self, value: T, base_filename: str = "edit-") -> None:
        """
        Set up editing a value.

        :param value: the initial value to edit
        :param base_filename: a string used as a prefix for constructing the
            temporary file name for the editor

        The edited value will be stored in self.value, and can be edited
        multiple times to allow downstream code to perform further validation.
        """
        self.initial = value
        self.value: T = value

        # Reserve a file name to use for the editor
        with tempfile.NamedTemporaryFile(
            prefix=base_filename, suffix=".tmp.yaml", delete=False
        ) as editor_file:
            self.editor_file = Path(editor_file.name)

    def open_editor(self) -> None:
        """Open an editor on the given file."""
        editor = shutil.which("sensible-editor")
        if editor is None:
            raise EditorInteractionError(
                "sensible-editor not found:"
                " you may need to `apt install sensible-utils`"
            )
        subprocess.run([editor, self.editor_file.as_posix()])

    def read_edited(self) -> None:
        """
        Read the edited file, setting self.edited.

        If the edited file is empty, it sets self.edited to an empty valid
        value.

        :raises InvalidData: if the edited file is not valid YAML or is a value
            of an unexpected type
        :raises EditorInteractionError: if the edited file cannot be opened
        """
        try:
            with self.editor_file.open() as yaml_file:
                try:
                    contents = yaml.safe_load(yaml_file)
                except (yaml.YAMLError, yaml.scanner.ScannerError) as exc:
                    raise InvalidData(f"Invalid YAML: {exc}")
        except OSError as exc:
            raise EditorInteractionError(
                f"Cannot open edited file {self.editor_file}: {exc}"
            )

        if contents is None:
            # Empty YAML is normalized to empty data
            self.value = self.initial.__class__()
        elif not isinstance(contents, self.initial.__class__):
            raise InvalidData(
                f"Data must be a {self.initial.__class__.__name__} or empty"
            )
        else:
            self.value = contents

    def message(self, message: str) -> None:
        """Write a message to the user."""
        print(message)

    def confirm(self, prompt: str) -> bool:
        """Ask the user a yes/no question."""
        while True:
            res = input(prompt + " (y/n)> ")
            if res.strip().lower() == "y":
                return True
            elif res.strip().lower() == "n":
                return False

    def edit(self) -> bool:
        """
        Let the user edit a data structure as YAML in a text editor.

        :return: True if editing was successful, False if aborted.
        """
        # Write the original contents
        with self.editor_file.open("w") as out:
            yaml.safe_dump(data=self.value, sort_keys=False, stream=out)

        while True:
            self.open_editor()
            try:
                self.read_edited()
            except InvalidData as e:
                self.message(f"Error reading metadata: {e}")
                answer = self.confirm("Do you want to retry the same edit?")

                if not answer:
                    self.message(f"Edits left in {self.editor_file}")
                    return False
            else:
                self.editor_file.unlink(missing_ok=True)
                return True
