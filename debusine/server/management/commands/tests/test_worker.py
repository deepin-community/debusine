# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command manage_worker."""

import datetime
from typing import Any, ClassVar
from unittest.mock import patch

from django.core.management import CommandError
from django.utils import timezone

from debusine.db.models import Token, WorkRequest, Worker
from debusine.django.management.tests import call_command
from debusine.server.management.commands.tests.utils import TabularOutputTests
from debusine.tasks.models import WorkerType
from debusine.test.django import TestCase
from debusine.utils.input import EditorInteractionError, YamlEditor


class WorkerCommandTests(TabularOutputTests, TestCase):
    """Tests for the ``worker`` command."""

    token: ClassVar[Token]
    worker: ClassVar[Worker]

    @classmethod
    def setUpTestData(cls) -> None:
        """Set up a default token and worker."""
        super().setUpTestData()
        cls.token = Token.objects.create()
        cls.worker = Worker.objects.create_with_fqdn(
            fqdn="worker-01.lan", token=cls.token
        )

    def test_create(self) -> None:
        """``worker create`` creates a new worker and activation token."""
        stdout, stderr, exit_code = call_command(
            "worker", "create", "worker.example.org"
        )

        token = Token.objects.get_token_or_none(stdout.rstrip("\n"))
        assert token is not None
        self.assertIsNotNone(token.activating_worker)
        self.assertEqual(
            token.activating_worker.worker_type, WorkerType.EXTERNAL
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_create_signing(self) -> None:
        """``worker create`` creates a new signing worker."""
        stdout, stderr, exit_code = call_command(
            "worker",
            "create",
            "--worker-type",
            "signing",
            "signing.example.org",
        )

        token = Token.objects.get_token_or_none(stdout.rstrip("\n"))
        assert token is not None
        self.assertIsNotNone(token.activating_worker)
        self.assertEqual(
            token.activating_worker.worker_type, WorkerType.SIGNING
        )
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_enable(self) -> None:
        """`worker enable <worker>` enables the worker."""
        self.assertFalse(self.token.enabled)

        call_command("worker", "enable", self.worker.name)

        self.token.refresh_from_db()
        self.assertTrue(self.token.enabled)

    def test_enable_not_found(self) -> None:
        """Worker not found raise CommandError."""
        with self.assertRaisesRegex(CommandError, "^Worker not found$"):
            call_command("worker", "enable", "worker-does-not-exist")

    def test_enable_wrong_type(self) -> None:
        """Trying to enable a worker of the wrong type raises CommandError."""
        worker = Worker.objects.get_or_create_celery()
        with self.assertRaisesRegex(
            CommandError,
            f'^Worker "{worker.name}" is of type "celery", not "external"$',
        ):
            call_command("worker", "enable", worker.name)

    def test_enable_signing(self) -> None:
        """Signing workers can be enabled with an option."""
        self.worker.worker_type = WorkerType.SIGNING
        self.worker.save()
        self.assertFalse(self.token.enabled)

        call_command(
            "worker", "enable", "--worker-type", "signing", self.worker.name
        )

        self.token.refresh_from_db()
        self.assertTrue(self.token.enabled)

    def test_disable(self) -> None:
        """`worker disable <worker>` disables the worker."""
        self.token.enable()
        self.token.refresh_from_db()

        work_request_running = self.playground.create_work_request(
            worker=self.worker,
            status=WorkRequest.Statuses.RUNNING,
            started_at=timezone.now(),
        )
        work_request_pending = self.playground.create_work_request(
            worker=self.worker,
            status=WorkRequest.Statuses.PENDING,
            started_at=timezone.now(),
        )
        work_request_finished = self.playground.create_work_request(
            worker=self.worker,
            status=WorkRequest.Statuses.COMPLETED,
            started_at=timezone.now(),
        )

        self.assertTrue(self.token.enabled)

        call_command("worker", "disable", self.worker.name)

        # Worker is disabled
        self.token.refresh_from_db()
        self.assertFalse(self.token.enabled)

        # work requests in running or pending status are not assigned
        # to the worker anymore
        for work_request in work_request_running, work_request_pending:
            work_request_running.refresh_from_db()
            self.assertIsNone(work_request_running.worker)
            self.assertEqual(
                work_request_running.status, WorkRequest.Statuses.PENDING
            )
            self.assertIsNone(work_request_running.started_at)

        # work request that was completed: fields did not change
        work_request_finished.refresh_from_db()
        self.assertEqual(
            work_request_finished.status, WorkRequest.Statuses.COMPLETED
        )
        self.assertEqual(work_request_finished.worker, self.worker)
        self.assertIsInstance(
            work_request_finished.started_at, datetime.datetime
        )

    def test_edit_metadata(self) -> None:
        """Set the worker metadata from the YAML file."""
        static_metadata_file = self.create_temporary_file()
        static_metadata_file.write_bytes(
            b"sbuild:\n"
            b"  architectures:\n"
            b"    - amd64\n"
            b"    - i386\n"
            b"  distributions:\n"
            b"    - jessie\n"
            b"    - stretch"
        )
        call_command(
            "worker",
            "edit_metadata",
            "--set",
            str(static_metadata_file),
            self.worker.name,
        )

        self.worker.refresh_from_db()

        self.assertEqual(
            self.worker.static_metadata,
            {
                "sbuild": {
                    "architectures": ["amd64", "i386"],
                    "distributions": ["jessie", "stretch"],
                }
            },
        )

    def test_edit_metadata_worker_does_not_exist(self) -> None:
        """Name of the worker does not exist: raise CommandError."""
        worker_name = "name-of-the-worker-does-not-exist.lan"
        expected_error = (
            f'Error: worker "{worker_name}" is not registered\n'
            f'Use the command `debusine-admin worker list` to list the '
            f'existing workers'
        )

        with self.assertRaisesMessage(CommandError, expected_error) as exc:
            call_command(
                "worker",
                "edit_metadata",
                "--set",
                "/tmp/some_file_not_accessed_worker_does_not_exist.yaml",
                worker_name,
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_edit_metadata_file_empty(self) -> None:
        """Error message stderr if the YAML file is not found."""
        file_path = self.create_temporary_file()

        stdout, stderr, exit_code = call_command(
            "worker",
            "edit_metadata",
            "--set",
            file_path.as_posix(),
            self.worker.name,
        )

        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "Error: file is empty\n")
        self.assertEqual(exit_code, 3)

    def test_edit_metadata_file_not_found(self) -> None:
        """Error message stderr if the YAML file is not found."""
        file_path = "/tmp/does_not_exist_file.yaml"

        stdout, stderr, exit_code = call_command(
            "worker", "edit_metadata", "--set", file_path, self.worker.name
        )

        self.assertRegex(
            stderr,
            fr"^Error: \[Errno 2\] No such file or directory: '{file_path}'\n",
        )
        self.assertEqual(exit_code, 3)

    def test_edit_metadata_cannot_open_file_is_directory(self) -> None:
        """Error message stderr if the YAML file cannot be opened."""
        temp_directory = self.create_temporary_directory()
        stdout, stderr, exit_code = call_command(
            "worker",
            "edit_metadata",
            "--set",
            temp_directory.as_posix(),
            self.worker.name,
        )

        self.assertRegex(
            stderr,
            fr"^Error: \[Errno 21\] Is a directory: '{temp_directory}'\n",
        )
        self.assertEqual(exit_code, 3)

    def test_edit_metadata_cannot_open_file_is_unreadable(self) -> None:
        """Error message stderr if the YAML file cannot be opened."""
        file_path = self.create_temporary_file()

        with patch(
            "debusine.utils.input.YamlEditor.read_edited",
            side_effect=EditorInteractionError("cannot read"),
        ):
            stdout, stderr, exit_code = call_command(
                "worker",
                "edit_metadata",
                "--set",
                file_path.as_posix(),
                self.worker.name,
            )

        self.assertEqual(stderr, "Error: cannot read\n")
        self.assertEqual(exit_code, 3)

    def test_edit_metadata_file_is_invalid(self) -> None:
        """Error message stderr if the YAML file is not found."""
        static_metadata_file = self.create_temporary_file(suffix=".yml")
        static_metadata_file.write_bytes(b"a\nb:")

        stdout, stderr, exit_code = call_command(
            "worker",
            "edit_metadata",
            "--set",
            str(static_metadata_file),
            self.worker.name,
        )

        self.assertRegex(
            stderr, "Invalid YAML: mapping values are not allowed here.*"
        )
        self.assertEqual(exit_code, 3)

    def test_edit_metadata_not_dictionary(self) -> None:
        """Return error when the metadata file is a string."""
        static_metadata_file = self.create_temporary_file(
            suffix=".yml", contents=b"bullseye"
        )
        stdout, stderr, exit_code = call_command(
            "worker",
            "edit_metadata",
            "--set",
            str(static_metadata_file),
            self.worker.name,
        )

        self.assertEqual(stderr, "Error: Data must be a dict or empty\n")
        self.assertEqual(exit_code, 3)

    def test_edit_metadata_interactive(self) -> None:
        """Editor changes the contents of worker's YAML metadata."""

        def mock_edit(self_: YamlEditor[dict[str, Any]]) -> bool:
            self_.value = {"distributions": ["buster", "bullseye"]}
            return True

        with patch(
            "debusine.utils.input.YamlEditor.edit",
            side_effect=mock_edit,
            autospec=True,
        ):
            stdout, stderr, exit_code = call_command(
                "worker",
                "edit_metadata",
                self.worker.name,
            )

        self.worker.refresh_from_db()

        self.assertEqual(
            self.worker.static_metadata,
            {"distributions": ["buster", "bullseye"]},
        )

        self.assertEqual(stdout, "debusine: metadata set for worker-01-lan\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_edit_metadata_interactive_aborted(self) -> None:
        """Editor is aborted by user."""
        self.worker.static_metadata = {"distributions": ["potato"]}
        self.worker.save()

        with patch("debusine.utils.input.YamlEditor.edit", return_value=False):
            call_command(
                "worker",
                "edit_metadata",
                self.worker.name,
            )

        self.worker.refresh_from_db()

        self.assertEqual(
            self.worker.static_metadata, {"distributions": ["potato"]}
        )

    def test_list_connected(self) -> None:
        """
        List worker command prints worker information.

        The worker is connected.
        """
        self.worker.mark_connected()
        assert self.worker.connected_at is not None

        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command("worker", "list")

        self.assertEqual(output.col(0), [self.worker.name])
        self.assertEqual(output.col(2), [self.worker.registered_at.isoformat()])
        self.assertEqual(output.col(3), [self.worker.connected_at.isoformat()])
        self.assertEqual(output.col(4), [self.token.hash])
        self.assertEqual(output.col(5), [str(self.token.enabled)])

    def test_list_not_connected(self) -> None:
        """
        List worker command prints worker information.

        The worker is not connected.
        """
        with self.assertPrintsTable() as output:
            stdout, stderr, _ = call_command("worker", "list")

        self.assertEqual(output.col(0), [self.worker.name])
        self.assertEqual(output.col(2), [self.worker.registered_at.isoformat()])
        self.assertEqual(output.col(3), ["-"])
        self.assertEqual(output.col(4), [self.token.hash])
        self.assertEqual(output.col(5), [str(self.token.enabled)])

    def test_list_celery(self) -> None:
        """list_workers handles Celery workers, which have no tokens."""
        self.worker.delete()
        worker = Worker.objects.get_or_create_celery()
        worker.mark_connected()
        assert worker.connected_at is not None

        with self.assertPrintsTable() as output:
            call_command("worker", "list")

        self.assertEqual(output.col(0), [worker.name])
        self.assertEqual(output.col(1), [worker.worker_type])
        self.assertEqual(output.col(2), [worker.registered_at.isoformat()])
        self.assertEqual(output.col(3), [worker.connected_at.isoformat()])
        self.assertEqual(output.col(4), ["-"])
        self.assertEqual(output.col(5), ["-"])
