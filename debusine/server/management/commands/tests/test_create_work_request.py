# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the management command create_work_request."""

import io

import yaml
from django.conf import settings
from django.core.management import CommandError

from debusine.db.models import WorkRequest, default_workspace, system_user
from debusine.django.management.tests import call_command
from debusine.tasks.models import (
    ActionSendNotification,
    ActionUpdateCollectionWithArtifacts,
    EventReactions,
    TaskTypes,
)
from debusine.test.django import TestCase


class CreateWorkRequestCommandTests(TestCase):
    """Tests for the create_work_request command."""

    def test_data_from_file(self) -> None:
        """`create_work_request` accepts data from a file."""
        data = {"result": True}
        data_file = self.create_temporary_file(
            contents=yaml.safe_dump(data).encode()
        )
        stdout, stderr, exit_code = call_command(
            "create_work_request",
            "worker",
            "noop",
            "--data",
            str(data_file),
        )

        work_request = WorkRequest.objects.get(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.assertEqual(work_request.workspace, default_workspace())
        self.assertEqual(work_request.created_by, system_user())
        self.assertEqual(work_request.task_data, data)
        self.assertEqual(work_request.event_reactions, EventReactions())

        expected = yaml.safe_dump({"work_request_id": work_request.id})
        self.assertEqual(stdout, f"---\n{expected}\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_data_from_stdin(self) -> None:
        """`create_work_request` accepts data from stdin."""
        data = {"result": True}
        stdout, stderr, exit_code = call_command(
            "create_work_request",
            "worker",
            "noop",
            stdin=io.StringIO(yaml.safe_dump(data)),
        )

        work_request = WorkRequest.objects.get(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.assertEqual(work_request.workspace, default_workspace())
        self.assertEqual(work_request.created_by, system_user())
        self.assertEqual(work_request.task_data, data)
        self.assertEqual(work_request.event_reactions, EventReactions())

        expected = yaml.safe_dump({"work_request_id": work_request.id})
        self.assertEqual(stdout, f"---\n{expected}\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_event_reactions(self) -> None:
        """`create_work_request` handles event reactions."""
        data = {"result": True}
        event_reactions = {
            "on_success": [{"action": "send-notification", "channel": "foo"}]
        }
        event_reactions_file = self.create_temporary_file(
            contents=yaml.safe_dump(event_reactions).encode()
        )
        stdout, stderr, exit_code = call_command(
            "create_work_request",
            "worker",
            "noop",
            "--event-reactions",
            str(event_reactions_file),
            stdin=io.StringIO(yaml.safe_dump(data)),
        )

        work_request = WorkRequest.objects.get(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.assertEqual(work_request.workspace, default_workspace())
        self.assertEqual(work_request.created_by, system_user())
        self.assertEqual(work_request.task_data, data)
        self.assertEqual(
            work_request.event_reactions,
            EventReactions(on_success=[ActionSendNotification(channel="foo")]),
        )

        expected = yaml.safe_dump({"work_request_id": work_request.id})
        self.assertEqual(stdout, f"---\n{expected}\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_server(self) -> None:
        """`create_work_request` can create server work requests."""
        data = {"result": True}
        event_reactions = {
            "on_success": [
                {
                    "action": "update-collection-with-artifacts",
                    "collection": 123,
                    "artifact_filters": {"category": "test"},
                }
            ]
        }
        event_reactions_file = self.create_temporary_file(
            contents=yaml.safe_dump(event_reactions).encode()
        )
        stdout, stderr, exit_code = call_command(
            "create_work_request",
            "server",
            "servernoop",
            "--event-reactions",
            str(event_reactions_file),
            stdin=io.StringIO(yaml.safe_dump(data)),
        )

        work_request = WorkRequest.objects.get(
            task_type=TaskTypes.SERVER, task_name="servernoop"
        )
        self.assertEqual(work_request.workspace, default_workspace())
        self.assertEqual(work_request.created_by, system_user())
        self.assertEqual(work_request.task_data, data)
        self.assertEqual(
            work_request.event_reactions,
            EventReactions(
                on_success=[
                    ActionUpdateCollectionWithArtifacts(
                        collection=123, artifact_filters={"category": "test"}
                    )
                ]
            ),
        )

        expected = yaml.safe_dump({"work_request_id": work_request.id})
        self.assertEqual(stdout, f"---\n{expected}\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_signing(self) -> None:
        """`create_work_request` can create signing work requests."""
        data = {"purpose": "uefi", "description": "A UEFI key"}
        event_reactions = {
            "on_success": [
                {
                    "action": "update-collection-with-artifacts",
                    "collection": 123,
                    "artifact_filters": {"category": "test"},
                }
            ]
        }
        event_reactions_file = self.create_temporary_file(
            contents=yaml.safe_dump(event_reactions).encode()
        )
        stdout, stderr, exit_code = call_command(
            "create_work_request",
            "signing",
            "generatekey",
            "--event-reactions",
            str(event_reactions_file),
            stdin=io.StringIO(yaml.safe_dump(data)),
        )

        work_request = WorkRequest.objects.get(
            task_type=TaskTypes.SIGNING, task_name="generatekey"
        )
        self.assertEqual(work_request.workspace, default_workspace())
        self.assertEqual(work_request.created_by, system_user())
        self.assertEqual(work_request.task_data, data)
        self.assertEqual(
            work_request.event_reactions,
            EventReactions(
                on_success=[
                    ActionUpdateCollectionWithArtifacts(
                        collection=123, artifact_filters={"category": "test"}
                    )
                ]
            ),
        )

        expected = yaml.safe_dump({"work_request_id": work_request.id})
        self.assertEqual(stdout, f"---\n{expected}\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_empty_data(self) -> None:
        """`create_work_request` defaults data to {}."""
        stdout, stderr, exit_code = call_command(
            "create_work_request", "worker", "noop", stdin=io.StringIO()
        )

        work_request = WorkRequest.objects.get(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.assertEqual(work_request.task_data, {})

        expected = yaml.safe_dump({"work_request_id": work_request.id})
        self.assertEqual(stdout, f"---\n{expected}\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_different_created_by(self) -> None:
        """`create_work_request` can use a different created-by user."""
        user = self.playground.get_default_user()
        stdout, stderr, exit_code = call_command(
            "create_work_request",
            "worker",
            "noop",
            "--created-by",
            user.username,
            stdin=io.StringIO(yaml.safe_dump({"result": True})),
        )

        work_request = WorkRequest.objects.get(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.assertEqual(work_request.created_by, user)

        expected = yaml.safe_dump({"work_request_id": work_request.id})
        self.assertEqual(stdout, f"---\n{expected}\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_different_workspace(self) -> None:
        """`create_work_request` can use a non-default workspace."""
        workspace_name = "test-workspace"
        workspace = self.playground.create_workspace(name=workspace_name)
        stdout, stderr, exit_code = call_command(
            "create_work_request",
            "worker",
            "noop",
            "--workspace",
            workspace_name,
            stdin=io.StringIO(),
        )

        work_request = WorkRequest.objects.get(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.assertEqual(work_request.workspace, workspace)

        expected = yaml.safe_dump({"work_request_id": work_request.id})
        self.assertEqual(stdout, f"---\n{expected}\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_different_scope(self) -> None:
        """`create_work_request` can use a workspace in a different scope."""
        scope_name = "test-scope"
        scope = self.playground.get_or_create_scope(name=scope_name)
        workspace = self.playground.create_workspace(
            scope=scope, name=settings.DEBUSINE_DEFAULT_WORKSPACE
        )
        stdout, stderr, exit_code = call_command(
            "create_work_request",
            "worker",
            "noop",
            "--workspace",
            f"{scope_name}/{settings.DEBUSINE_DEFAULT_WORKSPACE}",
            stdin=io.StringIO(),
        )

        work_request = WorkRequest.objects.get(
            task_type=TaskTypes.WORKER, task_name="noop"
        )
        self.assertEqual(work_request.workspace, workspace)

        expected = yaml.safe_dump({"work_request_id": work_request.id})
        self.assertEqual(stdout, f"---\n{expected}\n")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)

    def test_invalid_data_yaml(self) -> None:
        """`create_work_request` returns error: cannot parse data."""
        with self.assertRaisesRegex(
            CommandError, r"^Error parsing YAML:"
        ) as exc:
            call_command(
                "create_work_request", "worker", "noop", stdin=io.StringIO(":")
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_invalid_event_reactions_yaml(self) -> None:
        """`create_work_request` returns error: cannot parse data."""
        event_reactions_file = self.create_temporary_file(contents=b":")

        with self.assertRaisesRegex(
            CommandError, r"^Error parsing YAML:"
        ) as exc:
            call_command(
                "create_work_request",
                "worker",
                "noop",
                "--event-reactions",
                str(event_reactions_file),
                stdin=io.StringIO(),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_bad_task_type(self) -> None:
        """`create_work_request` returns error: bad task type."""
        with self.assertRaisesRegex(
            CommandError, r"task_type: invalid choice: 'nonexistent'"
        ) as exc:
            call_command(
                "create_work_request",
                "nonexistent",
                "noop",
                stdin=io.StringIO(),
            )

        self.assertEqual(exc.exception.returncode, 1)

    def test_user_not_found(self) -> None:
        """`create_work_request` returns error: user not found."""
        with self.assertRaisesRegex(
            CommandError, r'^User "nonexistent" not found'
        ) as exc:
            call_command(
                "create_work_request",
                "worker",
                "noop",
                "--created-by",
                "nonexistent",
                stdin=io.StringIO(),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_workspace_not_found(self) -> None:
        """`create_work_request` returns error: workspace not found."""
        with self.assertRaisesRegex(
            CommandError, r"^Workspace 'nonexistent' not found"
        ) as exc:
            call_command(
                "create_work_request",
                "worker",
                "noop",
                "--workspace",
                "nonexistent",
                stdin=io.StringIO(),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_task_name_not_found(self) -> None:
        """`create_work_request` returns error: task name not found."""
        with self.assertRaisesRegex(
            CommandError,
            r"'task_name': \['nonexistent: invalid Worker task name'\]",
        ) as exc:
            call_command(
                "create_work_request",
                "worker",
                "nonexistent",
                stdin=io.StringIO(),
            )

        self.assertEqual(exc.exception.returncode, 3)

    def test_bad_task_data(self) -> None:
        """`create_work_request` returns error: bad task data."""
        with self.assertRaisesRegex(
            CommandError, r"invalid Worker task data"
        ) as exc:
            call_command(
                "create_work_request",
                "worker",
                "noop",
                stdin=io.StringIO("foo: bar\n"),
            )

        self.assertEqual(exc.exception.returncode, 3)
        self.assertFalse(
            WorkRequest.objects.filter(
                task_type=TaskTypes.WORKER, task_name="noop"
            ).exists()
        )
