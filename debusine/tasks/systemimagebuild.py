# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Ontology definition of SystemImageBuild."""
import abc

from debusine.tasks import DefaultDynamicData, RunCommandTask
from debusine.tasks.models import BaseDynamicTaskData, SystemImageBuildData
from debusine.tasks.server import TaskDatabaseInterface


class SystemImageBuild(
    abc.ABC,
    RunCommandTask[SystemImageBuildData, BaseDynamicTaskData],
    DefaultDynamicData[SystemImageBuildData],
):
    """Implement ontology SystemImageBuild."""

    TASK_VERSION = 1

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface  # noqa: U100
    ) -> BaseDynamicTaskData:
        """Compute dynamic data."""
        suite_name = self.data.bootstrap_repositories[0].suite

        architecture = self.data.bootstrap_options.architecture
        variant = self.data.bootstrap_options.variant
        extra_packages = ",".join(
            sorted(self.data.bootstrap_options.extra_packages)
        )

        return BaseDynamicTaskData(
            subject=suite_name,
            runtime_context=f"{architecture}:{variant}:{extra_packages}",
            configuration_context=architecture,
        )

    def get_label(self) -> str:
        """Return the task label."""
        return "bootstrap a system image"
