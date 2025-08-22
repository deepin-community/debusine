# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Rename various workflow data fields to ``qa_suite``."""

from django.db import migrations

from debusine.artifacts.models import TaskTypes
from debusine.db.migrations._utils import (
    make_work_request_task_data_field_renamer,
    make_workflow_template_task_data_field_renamer,
)


class Migration(migrations.Migration):

    dependencies = [
        ("db", "0012_alter_collectionitem_created_by_workflow_and_more"),
    ]
    replaces = [
        ("db", "0154_qa_suite"),
    ]

    operations = [
        make_work_request_task_data_field_renamer(
            TaskTypes.WORKFLOW,
            "reverse_dependencies_autopkgtest",
            [("suite_collection", "qa_suite")],
        ),
        make_workflow_template_task_data_field_renamer(
            "reverse_dependencies_autopkgtest",
            [("suite_collection", "qa_suite")],
        ),
        make_work_request_task_data_field_renamer(
            TaskTypes.WORKFLOW,
            "qa",
            [("reverse_dependencies_autopkgtest_suite", "qa_suite")],
        ),
        make_workflow_template_task_data_field_renamer(
            "qa", [("reverse_dependencies_autopkgtest_suite", "qa_suite")]
        ),
        make_work_request_task_data_field_renamer(
            TaskTypes.WORKFLOW,
            "debian_pipeline",
            [("reverse_dependencies_autopkgtest_suite", "qa_suite")],
        ),
        make_workflow_template_task_data_field_renamer(
            "debian_pipeline",
            [("reverse_dependencies_autopkgtest_suite", "qa_suite")],
        ),
    ]
