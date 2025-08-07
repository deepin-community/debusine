# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for UpdateEnvironmentsWorkflow."""

from typing import Any

from django.test import TestCase

from debusine.artifacts.models import ArtifactCategory, CollectionCategory
from debusine.db.context import context
from debusine.db.models import (
    TaskDatabase,
    WorkRequest,
    WorkflowTemplate,
    default_workspace,
)
from debusine.server.collections.tests.utils import CollectionTestMixin
from debusine.server.workflows import UpdateEnvironmentsWorkflow
from debusine.server.workflows.base import orchestrate_workflow
from debusine.tasks.models import BaseDynamicTaskData, TaskTypes


class UpdateEnvironmentsWorkflowTests(CollectionTestMixin, TestCase):
    """Unit tests for UpdateEnvironmentsWorkflow."""

    def create_update_environments_template(
        self,
        name: str = "update_environments",
        task_data: dict[str, Any] | None = None,
    ) -> WorkflowTemplate:
        """Create an update_environments WorkflowTemplate."""
        return WorkflowTemplate.objects.create(
            name=name,
            workspace=default_workspace(),
            task_name="update_environments",
            task_data=task_data or {},
        )

    def test_create_orchestrator(self) -> None:
        """Instantiate an UpdateEnvironmentsWorkflow."""
        wr = WorkRequest(
            task_data={
                "vendor": "debian",
                "targets": [
                    {
                        "codenames": ["bookworm"],
                        "architectures": ["amd64"],
                        "mmdebstrap_template": {},
                    }
                ],
            },
            workspace=default_workspace(),
        )
        w = UpdateEnvironmentsWorkflow(wr)
        self.assertEqual(w.data.vendor, "debian")
        self.assertEqual(len(w.data.targets), 1)
        self.assertEqual(w.data.targets[0].codenames, ["bookworm"])
        self.assertEqual(w.data.targets[0].architectures, ["amd64"])
        self.assertEqual(w.data.targets[0].mmdebstrap_template, {})

    def test_create_work_requests(self) -> None:
        """Create specified work requests."""
        template = self.create_update_environments_template(
            task_data={
                "vendor": "debian",
                "targets": [
                    {
                        "codenames": "bookworm",
                        "architectures": ["amd64"],
                        "mmdebstrap_template": {
                            "bootstrap_repositories": [
                                {"mirror": "https://deb.debian.org/debian"}
                            ]
                        },
                    },
                    {
                        "codenames": ["trixie"],
                        "codename_aliases": {"trixie": ["sid"]},
                        "variants": ["autopkgtest", "sbuild"],
                        "backends": ["unshare", "incus-lxc"],
                        "architectures": ["amd64", "arm64"],
                        "simplesystemimagebuild_template": {
                            "bootstrap_options": {"variant": "minbase"},
                            "bootstrap_repositories": [
                                {"mirror": "https://deb.debian.org/debian"}
                            ],
                            "disk_image": {
                                "format": "qcow2",
                                "partitions": [
                                    {"size": 2, "filesystem": "ext4"}
                                ],
                            },
                        },
                    },
                ],
            }
        )
        wr = self.playground.create_workflow(template, task_data={})
        self.assertEqual(wr.status, WorkRequest.Statuses.PENDING)
        wr.mark_running()
        orchestrate_workflow(wr)

        children = list(wr.children.order_by("id"))
        self.assertEqual(len(children), 3)

        expected_bookworm_task_data = {
            "bootstrap_repositories": [
                {"mirror": "https://deb.debian.org/debian", "suite": "bookworm"}
            ],
        }
        expected_bookworm_reactions = [
            {
                "action": "update-collection-with-artifacts",
                "artifact_filters": {
                    "category": ArtifactCategory.SYSTEM_TARBALL
                },
                "collection": "debian@debian:environments",
                "name_template": None,
                "variables": {"codename": "bookworm"},
            }
        ]

        self.assertEqual(children[0].status, WorkRequest.Statuses.PENDING)
        self.assertEqual(children[0].task_type, TaskTypes.WORKER)
        self.assertEqual(children[0].task_name, "mmdebstrap")
        self.assertEqual(
            children[0].task_data,
            {
                "bootstrap_options": {"architecture": "amd64"},
                **expected_bookworm_task_data,
            },
        )
        self.assertQuerySetEqual(children[0].dependencies.all(), [])
        self.assertEqual(
            children[0].workflow_data_json,
            {
                "display_name": "Build tarball for bookworm/amd64",
                "step": "mmdebstrap-bookworm-amd64",
                "group": "bookworm",
            },
        )
        self.assert_work_request_event_reactions(
            children[0], on_success=expected_bookworm_reactions
        )

        expected_trixie_task_data = {
            "bootstrap_repositories": [
                {
                    "mirror": "https://deb.debian.org/debian",
                    "suite": "trixie",
                }
            ],
            "disk_image": {
                "format": "qcow2",
                "partitions": [{"size": 2, "filesystem": "ext4"}],
            },
        }
        expected_trixie_reactions = [
            {
                "action": "update-collection-with-artifacts",
                "artifact_filters": {"category": ArtifactCategory.SYSTEM_IMAGE},
                "collection": "debian@debian:environments",
                "name_template": None,
                "variables": {
                    "codename": codename,
                    "variant": variant,
                    "backend": backend,
                },
            }
            for codename, variant, backend in (
                ("trixie", "autopkgtest", "unshare"),
                ("trixie", "autopkgtest", "incus-lxc"),
                ("trixie", "sbuild", "unshare"),
                ("trixie", "sbuild", "incus-lxc"),
                ("sid", "autopkgtest", "unshare"),
                ("sid", "autopkgtest", "incus-lxc"),
                ("sid", "sbuild", "unshare"),
                ("sid", "sbuild", "incus-lxc"),
            )
        ]

        self.assertEqual(children[1].status, WorkRequest.Statuses.PENDING)
        self.assertEqual(children[1].task_type, TaskTypes.WORKER)
        self.assertEqual(children[1].task_name, "simplesystemimagebuild")
        self.assertEqual(
            children[1].task_data,
            {
                "bootstrap_options": {
                    "architecture": "amd64",
                    "variant": "minbase",
                },
                **expected_trixie_task_data,
            },
        )
        self.assertQuerySetEqual(children[1].dependencies.all(), [])
        self.assertEqual(
            children[1].workflow_data_json,
            {
                "display_name": "Build image for trixie/amd64",
                "step": "simplesystemimagebuild-trixie-amd64",
                "group": "trixie [autopkgtest,sbuild]",
            },
        )
        self.assert_work_request_event_reactions(
            children[1], on_success=expected_trixie_reactions
        )

        self.assertEqual(children[2].status, WorkRequest.Statuses.PENDING)
        self.assertEqual(children[2].task_type, TaskTypes.WORKER)
        self.assertEqual(children[2].task_name, "simplesystemimagebuild")
        self.assertEqual(
            children[2].task_data,
            {
                "bootstrap_options": {
                    "architecture": "arm64",
                    "variant": "minbase",
                },
                **expected_trixie_task_data,
            },
        )
        self.assertQuerySetEqual(children[2].dependencies.all(), [])
        self.assertEqual(
            children[2].workflow_data_json,
            {
                "display_name": "Build image for trixie/arm64",
                "step": "simplesystemimagebuild-trixie-arm64",
                "group": "trixie [autopkgtest,sbuild]",
            },
        )
        self.assert_work_request_event_reactions(
            children[2], on_success=expected_trixie_reactions
        )

        # populate() is idempotent.
        orchestrator = UpdateEnvironmentsWorkflow(wr)
        orchestrator.populate()
        self.assertEqual(wr.children.count(), 3)

    def test_event_reaction(self) -> None:
        """The event reaction created by the workflow can be handled."""
        template = self.create_update_environments_template(
            task_data={
                "vendor": "debian",
                "targets": [
                    {
                        "codenames": "bookworm",
                        "architectures": ["amd64"],
                        "mmdebstrap_template": {
                            "bootstrap_repositories": [
                                {"mirror": "https://deb.debian.org/debian"}
                            ]
                        },
                    }
                ],
            }
        )
        wr = self.playground.create_workflow(template, task_data={})
        wr.mark_running()
        orchestrate_workflow(wr)
        child = WorkRequest.objects.get(parent=wr)
        child.status = WorkRequest.Statuses.PENDING
        collection = self.playground.create_collection(
            name="debian", category=CollectionCategory.ENVIRONMENTS
        )
        with context.disable_permission_checks():
            tarball, _ = self.playground.create_artifact(
                category=ArtifactCategory.SYSTEM_TARBALL,
                data={"architecture": "amd64"},
                work_request=child,
            )

        child.mark_completed(WorkRequest.Results.SUCCESS)

        item = collection.manager.lookup(
            "match:format=tarball:codename=bookworm:architecture=amd64"
        )
        assert item is not None
        self.assertEqual(item.artifact, tarball)

    def test_compute_dynamic_data(self) -> None:
        wr = WorkRequest(
            task_data={
                "vendor": "debian",
                "targets": [
                    {
                        "codenames": ["bookworm"],
                        "architectures": ["amd64"],
                        "mmdebstrap_template": {},
                    }
                ],
            },
            workspace=default_workspace(),
        )
        w = UpdateEnvironmentsWorkflow(wr)

        self.assertEqual(
            w.compute_dynamic_data(TaskDatabase(wr)),
            BaseDynamicTaskData(subject="debian"),
        )

    def test_label(self) -> None:
        """Test get_label."""
        wr = WorkRequest(
            task_data={
                "vendor": "debian",
                "targets": [
                    {
                        "codenames": ["bookworm"],
                        "architectures": ["amd64"],
                        "mmdebstrap_template": {},
                    }
                ],
            },
            workspace=default_workspace(),
        )
        w = UpdateEnvironmentsWorkflow(wr)
        self.assertEqual(w.get_label(), "update 1 debian environment")

        wr = WorkRequest(
            task_data={
                "vendor": "debian",
                "targets": [
                    {
                        "codenames": ["bookworm"],
                        "architectures": ["amd64"],
                        "mmdebstrap_template": {},
                    },
                    {
                        "codenames": ["sid"],
                        "architectures": ["amd64"],
                        "mmdebstrap_template": {},
                    },
                ],
            },
            workspace=default_workspace(),
        )
        w = UpdateEnvironmentsWorkflow(wr)
        self.assertEqual(w.get_label(), "update 2 debian environments")
