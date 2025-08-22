# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for input module."""

import io
from contextlib import redirect_stderr, redirect_stdout
from typing import Any
from unittest import mock

import yaml

from debusine.test import TestCase
from debusine.utils.input import EditorInteractionError, InvalidData, YamlEditor


class YamlEditorTests(TestCase):
    """Tests for the :py:class:`YamlEditor` class."""

    def _editor(
        self, value: dict[str, Any] | None = None
    ) -> YamlEditor[dict[str, Any]]:
        if value is None:
            value = {}
        return YamlEditor(value)

    def test_init(self) -> None:
        editor = self._editor()
        self.assertEqual(editor.initial, {})
        self.assertEqual(editor.value, {})
        assert editor.editor_file.name.startswith("edit-")

    def test_open_editor(self) -> None:
        editor = self._editor()
        with (
            mock.patch("shutil.which", return_value="foo") as mock_which,
            mock.patch("subprocess.run") as mock_run,
        ):
            editor.open_editor()

        mock_which.assert_called_with("sensible-editor")
        mock_run.assert_called_with(["foo", editor.editor_file.as_posix()])

    def test_open_editor_not_found(self) -> None:
        editor = self._editor()
        with (
            mock.patch("shutil.which", return_value=None),
            self.assertRaisesRegex(
                EditorInteractionError, "sensible-editor not found"
            ),
        ):
            editor.open_editor()

    def test_read_edited(self) -> None:
        test_value = {"test": 1}
        editor = self._editor(test_value)
        editor.editor_file.write_text(yaml.safe_dump(test_value))
        editor.read_edited()
        self.assertEqual(editor.value, test_value)

    def test_read_edited_not_found(self) -> None:
        editor = self._editor()
        editor.editor_file.unlink()
        with self.assertRaisesRegex(
            EditorInteractionError, "Cannot open edited file"
        ):
            editor.read_edited()
        self.assertEqual(editor.value, editor.initial)

    def test_read_edited_empty(self) -> None:
        for data_type in (list, dict):
            with self.subTest(data_type=data_type):
                editor: YamlEditor[Any] = YamlEditor(data_type())
                editor.value = data_type([("test", 1)])
                editor.editor_file.write_text("")
                editor.read_edited()
                self.assertEqual(editor.value, data_type())

    def test_read_edited_invalid_yaml(self) -> None:
        editor = self._editor()
        editor.editor_file.write_text("{")
        with self.assertRaisesRegex(InvalidData, "Invalid YAML:"):
            editor.read_edited()
        self.assertEqual(editor.value, editor.initial)

    def test_read_edited_invalid_type(self) -> None:
        editor = self._editor()
        editor.editor_file.write_text("[]")
        with self.assertRaisesRegex(
            InvalidData, "Data must be a dict or empty"
        ):
            editor.read_edited()
        self.assertEqual(editor.value, editor.initial)

    def test_message(self) -> None:
        editor = self._editor()
        with (
            redirect_stdout(stdout := io.StringIO()),
            redirect_stderr(stderr := io.StringIO()),
        ):
            editor.message("test message")
        self.assertEqual(stdout.getvalue(), "test message\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_confirm(self) -> None:
        editor = self._editor()
        for reply, expected in (
            ("y", True),
            ("Y", True),
            (" Y ", True),
            ("n", False),
            ("N", False),
            (" N ", False),
        ):
            with (
                self.subTest(reply=reply),
                mock.patch("builtins.input", return_value=reply) as mock_input,
            ):
                self.assertEqual(editor.confirm("prompt"), expected)
                mock_input.assert_called_with("prompt (y/n)> ")

    def test_confirm_retry(self) -> None:
        editor = self._editor()
        with mock.patch(
            "builtins.input", side_effect=["yes", "y"]
        ) as mock_input:
            self.assertTrue(editor.confirm("prompt"))
            mock_input.assert_has_calls([mock.call("prompt (y/n)> ")] * 2)

    def test_edit(self) -> None:
        def write_file(self: YamlEditor[dict[str, Any]]) -> None:
            self.editor_file.write_text("{'test': 1}")

        editor = self._editor()
        with mock.patch(
            "debusine.utils.input.YamlEditor.open_editor",
            side_effect=write_file,
            autospec=True,
        ):
            self.assertTrue(editor.edit())
        self.assertEqual(editor.value, {"test": 1})

    def test_edit_initial_data(self) -> None:
        """The editor is opened in the initial data."""

        def write_file(self_: YamlEditor[dict[str, Any]]) -> None:
            self.assertEqual(self_.editor_file.read_text(), "test: 42\n")

        editor = self._editor({"test": 42})
        with mock.patch(
            "debusine.utils.input.YamlEditor.open_editor",
            side_effect=write_file,
            autospec=True,
        ):
            self.assertTrue(editor.edit())
        self.assertFalse(editor.editor_file.exists())

    def test_edit_fails_user_aborts(self) -> None:
        editor = self._editor({"test": 1})
        with (
            mock.patch(
                "debusine.utils.input.YamlEditor.open_editor",
            ) as mock_open_editor,
            mock.patch(
                "debusine.utils.input.YamlEditor.read_edited",
                side_effect=InvalidData("test"),
            ),
            mock.patch(
                "debusine.utils.input.YamlEditor.message",
            ) as mock_message,
            mock.patch(
                "debusine.utils.input.YamlEditor.confirm", return_value=False
            ),
        ):
            self.assertFalse(editor.edit())
        self.assertEqual(editor.value, {"test": 1})

        mock_open_editor.assert_called_once()
        mock_message.assert_has_calls(
            [
                mock.call("Error reading metadata: test"),
                mock.call(f"Edits left in {editor.editor_file}"),
            ]
        )

        # Check contents of the file
        self.assertTrue(editor.editor_file.exists())
        self.assertEqual(editor.editor_file.read_text(), "test: 1\n")
        editor.editor_file.unlink()

    def test_edit_fails_user_retries(self) -> None:
        call_count = 0

        def mock_read_edited(self_: YamlEditor[dict[str, Any]]) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise InvalidData("test")
            else:
                self_.value = {"test": 42}
                return True

        editor = self._editor({"test": 1})
        with (
            mock.patch(
                "debusine.utils.input.YamlEditor.open_editor",
            ) as mock_open_editor,
            mock.patch(
                "debusine.utils.input.YamlEditor.read_edited",
                side_effect=mock_read_edited,
                autospec=True,
            ),
            mock.patch(
                "debusine.utils.input.YamlEditor.confirm", return_value=True
            ),
        ):
            self.assertTrue(editor.edit())
        self.assertEqual(editor.value, {"test": 42})

        mock_open_editor.assert_has_calls([mock.call()] * 2)
        self.assertFalse(editor.editor_file.exists())
