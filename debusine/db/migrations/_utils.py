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
from django.db.migrations.operations.base import Operation
from django.db.migrations.state import ProjectState, StateApps
from django.db.models import F, Q

from debusine.artifacts.models import ArtifactCategory
from debusine.tasks.models import TaskTypes


def _dict_has_path(d: dict[str, Any], path: Sequence[str]) -> bool:
    """Return whether `d[path[0]][path[1]][...]` exists."""
    for key in path[:-1]:
        d = d.get(key, {})
    return path[-1] in d


def _dict_pop_path(d: dict[str, Any], path: Sequence[str]) -> Any:
    """Pop and return `d[path[0]][path[1]][...]`."""
    for key in path[:-1]:
        d = d[key]
    return d.pop(path[-1])


def _dict_set_path(d: dict[str, Any], path: Sequence[str], value: Any) -> None:
    """Set `d[path[0]][path[1]][...]` to `value`."""
    for key in path[:-1]:
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
                        except KeyError:
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


def make_artifact_field_renamer(
    category: ArtifactCategory, renames: list[tuple[str, str]]
) -> migrations.RunPython:
    """Make a migration operation to rename an Artifact data field."""
    return make_data_field_renamer(
        "Artifact", "data", {"category": category}, renames
    )


def make_work_request_task_data_field_renamer(
    task_name: str | None, renames: list[tuple[str, str]]
) -> migrations.RunPython:
    """Make a migration operation to rename task_data in a WorkRequest."""
    filters: dict[str, Any] = {}
    if task_name is not None:
        filters["task_name"] = task_name
    return make_data_field_renamer("WorkRequest", "task_data", filters, renames)


def make_work_request_task_renamer(
    task_type: TaskTypes, old_task_name: str, new_task_name: str
) -> migrations.RunPython:
    """Make a migration operation to change a task_name in a WorkRequest."""

    def operation(
        task_type: TaskTypes,
        old_task_name: str,
        new_task_name: str,
        apps: StateApps,
        schema_editor: BaseDatabaseSchemaEditor,  # noqa: U100
    ) -> None:
        WorkRequest = apps.get_model("db", "WorkRequest")
        WorkRequest.objects.filter(
            task_type=task_type, task_name=old_task_name
        ).update(task_name=new_task_name)

    return migrations.RunPython(
        partial(operation, task_type, old_task_name, new_task_name),
        reverse_code=partial(
            operation, task_type, new_task_name, old_task_name
        ),
    )


class ReloadModel(Operation):
    """
    Work around a Django bug by reloading a model manually.

    If a migration changes only model A that is related to models B, C, and
    D by successive foreign keys, and then a later data migration tries to
    delete model C in its reverse operation, then the deletion raises
    `ValueError: Cannot query "C (...)": Must be "C" instance` when trying
    to handle the foreign key from D to C because the migration engine's
    idea of the state of model D now refers to an old version of model C
    that hasn't been reloaded.  To work around that, the migration that
    changes model A can manually reload model D by including something like
    `ReloadModel(("db", "d"))` in its operations.

    This is probably related to https://code.djangoproject.com/ticket/27737.
    """

    def __init__(self, model_key: tuple[str, str]) -> None:
        """Construct the operation."""
        self.model_key = model_key

    def state_forwards(
        self,
        app_label: str,  # noqa: U100
        state: ProjectState,
    ) -> None:
        """Tell Django to reload the given model."""
        state.reload_model(*self.model_key)

    def database_forwards(
        self,
        app_label: str,  # noqa: U100
        schema_editor: BaseDatabaseSchemaEditor,  # noqa: U100
        from_state: ProjectState,  # noqa: U100
        to_state: ProjectState,  # noqa: U100
    ) -> None:
        """No schema change."""

    def database_backwards(
        self,
        app_label: str,  # noqa: U100
        schema_editor: BaseDatabaseSchemaEditor,  # noqa: U100
        from_state: ProjectState,  # noqa: U100
        to_state: ProjectState,  # noqa: U100
    ) -> None:
        """No schema change."""


# https://docs.djangoproject.com/en/4.2/howto/writing-migrations/#changing-a-manytomanyfield-to-use-a-through-model
# gives advice on renaming the table used as a "through" model for a
# many-to-many field, but it doesn't handle renaming the associated
# constraints, indexes, and sequences whose names were generated based on
# the original table name.  It seems quite difficult to extract the relevant
# names from the Django migrator so that we can generate the appropriate SQL
# statements to rename them all.  And we can't just query the relevant
# PostgreSQL catalog tables in the migration, because the current database
# state might not match the projected migration state (e.g. in sqlmigrate).
#
# PL/pgSQL to the rescue!  We generate SQL that queries the catalog tables
# to find the associated constraints, indexes, and sequences, and executes
# the appropriate ALTER statements.  When that SQL is actually executed, the
# database state must be up to date.
#
# The catalog queries here are simplified from
# django.db.backends.postgresql.introspection.
_rename_table_sql = """
DO $$
DECLARE
    from_name text := %s;
    to_name text := %s;
    name text;
BEGIN
    EXECUTE format('ALTER TABLE %%I RENAME TO %%I', from_name, to_name);

    FOR name IN
        SELECT c.conname
        FROM pg_constraint AS c
        JOIN pg_class AS cl ON c.conrelid = cl.oid
        WHERE
            cl.relname = to_name
            AND pg_catalog.pg_table_is_visible(cl.oid)
            AND starts_with(c.conname, from_name || '_')
            -- Primary key, unique, and exclusion constraints are
            -- automatically backed by indexes, so we'll deal with them
            -- separately.
            AND c.contype NOT IN ('p', 'u', 'x')
    LOOP
        EXECUTE format(
            'ALTER TABLE %%I RENAME CONSTRAINT %%I TO %%I',
            to_name,
            name,
            to_name || substring(name FROM char_length(from_name) + 1)
        );
    END LOOP;

    FOR name IN
        SELECT i.relname
        FROM
            pg_class i
            JOIN pg_index idx ON idx.indexrelid = i.oid
            JOIN pg_class tbl ON tbl.oid = idx.indrelid
                AND tbl.relname = to_name
                AND pg_catalog.pg_table_is_visible(tbl.oid)
        WHERE starts_with(i.relname, from_name || '_')
    LOOP
        EXECUTE format(
            'ALTER INDEX %%I RENAME TO %%I',
            name,
            to_name || substring(name FROM char_length(from_name) + 1)
        );
    END LOOP;

    FOR name IN
        SELECT s.relname
        FROM
            pg_class s
            JOIN pg_depend d ON d.objid = s.oid
                AND d.classid = 'pg_class'::regclass
                AND d.refclassid = 'pg_class'::regclass
            JOIN pg_class tbl ON tbl.oid = d.refobjid
                AND tbl.relname = to_name
                AND pg_catalog.pg_table_is_visible(tbl.oid)
        WHERE
            s.relkind = 'S'
            AND starts_with(s.relname, from_name || '_')
    LOOP
        EXECUTE format(
            'ALTER SEQUENCE %%I RENAME TO %%I',
            name,
            to_name || substring(name FROM char_length(from_name) + 1)
        );
    END LOOP;
END;
$$;
"""


def make_table_and_relations_renamer(
    from_name: str, to_name: str
) -> migrations.RunSQL:
    """Return an operation that renames a table and its associated relations."""
    return migrations.RunSQL(
        sql=[(_rename_table_sql, (from_name, to_name))],
        reverse_sql=[(_rename_table_sql, (to_name, from_name))],
    )
