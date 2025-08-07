# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for workflow models."""

from debusine.server.workflows.models import (
    DebianPipelineWorkflowData,
    ExperimentWorkspaceData,
    PackagePublishWorkflowData,
    QAWorkflowData,
    SbuildWorkflowData,
)
from debusine.tasks.models import (
    LookupMultiple,
    SbuildInput,
    empty_lookup_multiple,
)
from debusine.test.django import TestCase


class SbuildWorkflowDataTests(TestCase):
    """Tests for :py:class:`SbuildWorkflowData`."""

    def test_retry_delays(self) -> None:
        """A valid `retry_delays` field is accepted."""
        delays = ["30m", "1h", "2d", "1w"]
        data = SbuildWorkflowData(
            input=SbuildInput(source_artifact=42),
            target_distribution="debian:bookworm",
            architectures=["all"],
            retry_delays=delays,
        )
        self.assertEqual(
            data.dict(exclude_unset=True),
            {
                "input": {"source_artifact": 42},
                "target_distribution": "debian:bookworm",
                "architectures": ["all"],
                "retry_delays": delays,
            },
        )

    def test_retry_delays_bad_format(self) -> None:
        """Items in `retry_delays` must be integers followed by m/h/d/w."""
        with self.assertRaisesRegex(
            ValueError,
            "Item in retry_delays must be an integer followed by "
            "m, h, d, or w; got '2 weeks'",
        ):
            SbuildWorkflowData(
                input=SbuildInput(source_artifact=42),
                target_distribution="debian:bookworm",
                architectures=["all"],
                retry_delays=["2 weeks"],
            )


class QAWorkflowDataTests(TestCase):
    """Tests for :py:class:`QAWorkflowData`."""

    def test_reverse_dependencies_autopkgtest_consistency(self) -> None:
        """Test reverse-dependencies-autopkgtest validation."""
        QAWorkflowData(
            source_artifact=1,
            binary_artifacts=LookupMultiple.parse_obj([2]),
            vendor="debian",
            codename="sid",
        )
        QAWorkflowData(
            source_artifact=1,
            binary_artifacts=LookupMultiple.parse_obj([2]),
            vendor="debian",
            codename="sid",
            enable_reverse_dependencies_autopkgtest=True,
            reverse_dependencies_autopkgtest_suite="sid@debian:suite",
        )

        with self.assertRaisesRegex(
            ValueError,
            '"reverse_dependencies_autopkgtest_suite" is required if '
            '"enable_reverse_dependencies_autopkgtest" is set',
        ):
            QAWorkflowData(
                source_artifact=1,
                binary_artifacts=LookupMultiple.parse_obj([2]),
                vendor="debian",
                codename="sid",
                enable_reverse_dependencies_autopkgtest=True,
            )


class DebianPipelineWorkflowDataTests(TestCase):
    """Tests for :py:class:`DebianPipelineWorkflowData`."""

    def test_reverse_dependencies_autopkgtest_consistency(self) -> None:
        """Test reverse-dependencies-autopkgtest validation."""
        DebianPipelineWorkflowData(
            source_artifact=1, vendor="debian", codename="sid"
        )
        DebianPipelineWorkflowData(
            source_artifact=1,
            vendor="debian",
            codename="sid",
            enable_reverse_dependencies_autopkgtest=True,
            reverse_dependencies_autopkgtest_suite="sid@debian:suite",
        )

        with self.assertRaisesRegex(
            ValueError,
            '"reverse_dependencies_autopkgtest_suite" is required if '
            '"enable_reverse_dependencies_autopkgtest" is set',
        ):
            DebianPipelineWorkflowData(
                source_artifact=1,
                vendor="debian",
                codename="sid",
                enable_reverse_dependencies_autopkgtest=True,
            )


class PackagePublishWorkflowDataTests(TestCase):
    """Tests for :py:class:`PackagePublishWorkflowData`."""

    def test_input_validation(self) -> None:
        """Test LintianInput validation."""
        PackagePublishWorkflowData(
            source_artifact=1, target_suite="bookworm@debian:suite"
        )
        PackagePublishWorkflowData(
            binary_artifacts=LookupMultiple.parse_obj([1]),
            target_suite="bookworm@debian:suite",
        )
        PackagePublishWorkflowData(
            binary_artifacts=LookupMultiple.parse_obj([1, 2]),
            target_suite="bookworm@debian:suite",
        )

        error_msg = '"source_artifact" or "binary_artifacts" must be set'
        with self.assertRaisesRegex(ValueError, error_msg):
            PackagePublishWorkflowData(target_suite="bookworm@debian:suite")
        with self.assertRaisesRegex(ValueError, error_msg):
            PackagePublishWorkflowData(
                binary_artifacts=empty_lookup_multiple(),
                target_suite="bookworm@debian:suite",
            )


class ExperimentWorkspaceDataTests(TestCase):
    """Tests for :py:class:`ExperimentWorkspaceData`."""

    def test_validate_experiment_name(self) -> None:
        """Test experiment name validation."""
        for name in ["test-test", "0test", "test@test", ".test", "+test"]:
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    ValueError, "experiment name contains invalid characters"
                ),
            ):
                ExperimentWorkspaceData(experiment_name="test-test")
