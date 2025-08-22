# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for workflow models."""

from abc import ABCMeta, abstractmethod
from typing import Any, Generic, TypeVar

from debusine.server.workflows.models import (
    AutopkgtestWorkflowData,
    DebianPipelineWorkflowData,
    ExperimentWorkspaceData,
    LintianWorkflowData,
    PackagePublishWorkflowData,
    PiupartsWorkflowData,
    QAWorkflowData,
    RegressionTrackingWorkflowData,
    ReverseDependenciesAutopkgtestWorkflowData,
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


WD = TypeVar("WD", bound=RegressionTrackingWorkflowData)


class RegressionTrackingWorkflowDataTests(
    TestCase, Generic[WD], metaclass=ABCMeta
):
    """Common tests for models that handle regression tracking."""

    qa_suite_always_required = False

    @abstractmethod
    def make_data(self, **kwargs: Any) -> WD:
        """Make a data model of the appropriate type."""

    def test_qa_results_consistency(self) -> None:
        """Test validation of regression-tracking options."""
        self.make_data()
        self.make_data(
            reference_prefix="reference-qa-result|",
            qa_suite="sid@debian:suite",
            reference_qa_results="sid@debian:qa-results",
            enable_regression_tracking=True,
        )
        self.make_data(
            qa_suite="sid@debian:suite",
            reference_qa_results="sid@debian:qa-results",
            update_qa_results=True,
        )

        if not self.qa_suite_always_required:
            with self.assertRaisesRegex(
                ValueError,
                '"qa_suite" is required if "enable_regression_tracking" or '
                '"update_qa_results" is set',
            ):
                self.make_data(enable_regression_tracking=True)
            with self.assertRaisesRegex(
                ValueError,
                '"qa_suite" is required if "enable_regression_tracking" or '
                '"update_qa_results" is set',
            ):
                self.make_data(update_qa_results=True)
        with self.assertRaisesRegex(
            ValueError,
            '"reference_qa_results" is required if '
            '"enable_regression_tracking" or "update_qa_results" is set',
        ):
            self.make_data(
                qa_suite="sid@debian:suite", enable_regression_tracking=True
            )
        with self.assertRaisesRegex(
            ValueError,
            '"reference_qa_results" is required if '
            '"enable_regression_tracking" or "update_qa_results" is set',
        ):
            self.make_data(qa_suite="sid@debian:suite", update_qa_results=True)
        with self.assertRaisesRegex(
            ValueError,
            '"reference_prefix" is required if "enable_regression_tracking" '
            'is set',
        ):
            self.make_data(
                qa_suite="sid@debian:suite",
                reference_qa_results="sid@debian:qa-results",
                enable_regression_tracking=True,
            )


class PiupartsWorkflowDataTests(
    RegressionTrackingWorkflowDataTests[PiupartsWorkflowData]
):
    """Tests for :py:class:`PiupartsWorkflowData`."""

    def make_data(self, **kwargs: Any) -> PiupartsWorkflowData:
        """Make a `PiupartsWorkflowData`."""
        return PiupartsWorkflowData(
            source_artifact=1,
            binary_artifacts=LookupMultiple.parse_obj([2]),
            vendor="debian",
            codename="sid",
            **kwargs,
        )


class AutopkgtestWorkflowDataTests(
    RegressionTrackingWorkflowDataTests[AutopkgtestWorkflowData]
):
    """Tests for :py:class:`AutopkgtestWorkflowData`."""

    def make_data(self, **kwargs: Any) -> AutopkgtestWorkflowData:
        """Make an `AutopkgtestWorkflowData`."""
        return AutopkgtestWorkflowData(
            source_artifact=1,
            binary_artifacts=LookupMultiple.parse_obj([2]),
            vendor="debian",
            codename="sid",
            **kwargs,
        )


class ReverseDependenciesAutopkgtestWorkflowDataTests(
    RegressionTrackingWorkflowDataTests[
        ReverseDependenciesAutopkgtestWorkflowData
    ]
):
    """Tests for :py:class:`ReverseDependenciesAutopkgtestWorkflowData`."""

    qa_suite_always_required = True

    def make_data(
        self, **kwargs: Any
    ) -> ReverseDependenciesAutopkgtestWorkflowData:
        """Make a `ReverseDependenciesAutopkgtestWorkflowData`."""
        data: dict[str, Any] = {
            "source_artifact": 1,
            "binary_artifacts": LookupMultiple.parse_obj([2]),
            "qa_suite": "sid@debian:suite",
            "vendor": "debian",
            "codename": "sid",
        }
        data.update(kwargs)
        return ReverseDependenciesAutopkgtestWorkflowData(**data)


class LintianWorkflowDataTests(
    RegressionTrackingWorkflowDataTests[LintianWorkflowData]
):
    """Tests for :py:class:`LintianWorkflowData`."""

    def make_data(self, **kwargs: Any) -> LintianWorkflowData:
        """Make a `LintianWorkflowData`."""
        return LintianWorkflowData(
            source_artifact=1,
            binary_artifacts=LookupMultiple.parse_obj([2]),
            vendor="debian",
            codename="sid",
            **kwargs,
        )


class QAWorkflowDataTests(RegressionTrackingWorkflowDataTests[QAWorkflowData]):
    """Tests for :py:class:`QAWorkflowData`."""

    def make_data(self, **kwargs: Any) -> QAWorkflowData:
        """Make a `QAWorkflowData`."""
        return QAWorkflowData.parse_obj(
            {
                "source_artifact": 1,
                "binary_artifacts": LookupMultiple.parse_obj([2]),
                "package_build_logs": LookupMultiple.parse_obj([3]),
                "vendor": "debian",
                "codename": "sid",
                **kwargs,
            },
        )

    def test_reverse_dependencies_autopkgtest_consistency(self) -> None:
        """Test reverse-dependencies-autopkgtest validation."""
        self.make_data()
        self.make_data(
            qa_suite="sid@debian:suite",
            enable_reverse_dependencies_autopkgtest=True,
        )

        with self.assertRaisesRegex(
            ValueError,
            '"qa_suite" is required if '
            '"enable_reverse_dependencies_autopkgtest" is set',
        ):
            self.make_data(enable_reverse_dependencies_autopkgtest=True)

    def test_enable_debdiff_consistency(self) -> None:
        """Test enable_debdiff_consistency validation."""
        self.make_data()
        self.make_data(qa_suite="sid@debian:suite", enable_debdiff=True)

        self.make_data()

        with self.assertRaisesRegex(
            ValueError,
            '"qa_suite" is required if "enable_debdiff" is set',
        ):
            self.make_data(enable_debdiff=True)

    def test_enable_blhc_consistency(self) -> None:
        """Test enable_blhc_consistency validation."""
        self.make_data()

        with self.assertRaisesRegex(
            ValueError,
            '"package_build_logs" is required if "enable_blhc" is set',
        ):
            self.make_data(package_build_logs=None, enable_blhc=True)


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
            qa_suite="sid@debian:suite",
            enable_reverse_dependencies_autopkgtest=True,
        )

        with self.assertRaisesRegex(
            ValueError,
            '"qa_suite" is required if '
            '"enable_reverse_dependencies_autopkgtest" is set',
        ):
            DebianPipelineWorkflowData(
                source_artifact=1,
                vendor="debian",
                codename="sid",
                enable_reverse_dependencies_autopkgtest=True,
            )

    def test_enable_debdiff_consistency(self) -> None:
        """Test enable_debdiff validator."""
        DebianPipelineWorkflowData(
            source_artifact=1, vendor="debian", codename="sid"
        )

        with self.assertRaisesRegex(
            ValueError,
            '"qa_suite" is required if "enable_debdiff" is set',
        ):
            DebianPipelineWorkflowData(
                source_artifact=1,
                enable_debdiff=True,
                vendor="debian",
                codename="sid",
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


# Avoid running tests from common base class.
del RegressionTrackingWorkflowDataTests
