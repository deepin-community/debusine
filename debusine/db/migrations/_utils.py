# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

import copy
from collections.abc import Sequence
from functools import partial
from typing import Any

from django.db import migrations, transaction
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import F, Q

from debusine.artifacts.models import TaskTypes


def _dict_has_path(d: dict[str, Any], path: Sequence[str]) -> bool:
    """Return whether `d[path[0]][path[1]][...]` exists."""
    for key in path[:-1]:  # pragma: no cover
        d = d.get(key, {})
    return path[-1] in d


def _dict_pop_path(d: dict[str, Any], path: Sequence[str]) -> Any:
    """Pop and return `d[path[0]][path[1]][...]`."""
    for key in path[:-1]:  # pragma: no cover
        d = d[key]
    return d.pop(path[-1])


def _dict_set_path(d: dict[str, Any], path: Sequence[str], value: Any) -> None:
    """Set `d[path[0]][path[1]][...]` to `value`."""
    for key in path[:-1]:  # pragma: no cover
        d = d.setdefault(key, {})
    d[path[-1]] = value


def make_data_field_renamer(
    model_name: str,
    data_field: str,
    filters: dict[str, Any],
    renames: list[tuple[str, str]],
) -> migrations.RunPython:
    """
    Make a migration operation to rename keys in data of model_name.

    This will be slow if there are many matching models.  Let's hope there
    aren't yet.
    """

    def operation(
        model_name: str,
        data_field: str,
        filters: dict[str, Any],
        renames: list[tuple[str, str]],
        apps: StateApps,
        schema_editor: BaseDatabaseSchemaEditor,  # noqa: U100
    ) -> None:
        Model = apps.get_model("db", model_name)
        rename_paths = [
            (old_name.split("__"), new_name.split("__"))
            for old_name, new_name in renames
        ]
        path_filter = Q()
        for old_name, _ in renames:
            path_filter |= Q(
                **{f"{data_field}__has_key": F(f"{data_field}__{old_name}")}
            )
        while True:
            with transaction.atomic():
                models = Model.objects.select_for_update().filter(
                    path_filter, **filters
                )[:1000]
                if not models:
                    break
                for model in models:
                    data = copy.deepcopy(getattr(model, data_field))
                    for old_path, new_path in rename_paths:
                        try:
                            old_value = _dict_pop_path(data, old_path)
                        except KeyError:  # pragma: no cover
                            pass
                        else:
                            assert not _dict_has_path(data, new_path)
                            _dict_set_path(data, new_path, old_value)
                    setattr(model, data_field, data)
                    model.save()

    return migrations.RunPython(
        partial(operation, model_name, data_field, filters, renames),
        reverse_code=partial(
            operation,
            model_name,
            data_field,
            filters,
            [(new_name, old_name) for old_name, new_name in renames],
        ),
    )


def make_work_request_task_data_field_renamer(
    task_type: TaskTypes,
    task_name: str,
    renames: list[tuple[str, str]],
) -> migrations.RunPython:
    """Make a migration operation to rename task_data in a WorkRequest."""
    filters = {"task_type": task_type, "task_name": task_name}
    return make_data_field_renamer("WorkRequest", "task_data", filters, renames)


def make_workflow_template_task_data_field_renamer(
    task_name: str, renames: list[tuple[str, str]]
) -> migrations.RunPython:
    """Make a migration operation to rename task_data in a WorkflowTemplate."""
    return make_data_field_renamer(
        "WorkflowTemplate", "task_data", {"task_name": task_name}, renames
    )
