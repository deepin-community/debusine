# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""DB constraints."""

from typing import Any

from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.backends.ddl_references import Statement, Table
from django.db.models import Model, Q
from django.db.models.constraints import BaseConstraint
from django.db.models.sql import Query


class JsonDataUniqueConstraint(BaseConstraint):
    """UniqueConstraint for JsonField data."""

    def __init__(
        self,
        *,
        fields: list[str] | tuple[str, ...],
        name: str,
        condition: Q | None = None,
        nulls_distinct: bool | None = None,
    ) -> None:
        """
        Create a UniqueConstraint operating on a JsonField.

        :param fields: list of keys in the JsonField that must be unique
        :param condition: condition for the constraint to be enforced
           (e.g. if the constraint is only enforced for some "category")
        :param name: constraint name
        :param nulls_distinct: False: nulls are not distinct.
           If nulls_distinct=False requires non-JSON fields to be null=False.
        """
        self.name = name
        self._condition = condition
        self._fields = tuple(fields)
        self._nulls_distinct = nulls_distinct
        super().__init__(name)

    def _get_condition_sql(
        self, model: type[Model], schema_editor: BaseDatabaseSchemaEditor
    ) -> str | None:
        # Copy-paste of UniqueConstraint._get_condition_sql
        if self._condition is None:
            return None
        query = Query(model=model, alias_cols=False)
        where = query.build_where(self._condition)
        compiler = query.get_compiler(connection=schema_editor.connection)
        sql, params = where.as_sql(compiler, schema_editor.connection)
        return sql % tuple(schema_editor.quote_value(p) for p in params)

    def constraint_sql(
        self,
        model: type[Model] | None,
        schema_editor: BaseDatabaseSchemaEditor | None,
    ) -> str:
        """Not currently used."""
        raise NotImplementedError()

    def create_sql(
        self,
        model: type[Model] | None,
        schema_editor: BaseDatabaseSchemaEditor | None,
    ) -> str:
        """Return the statement to create the index."""
        assert model
        assert schema_editor

        if schema_editor.connection.vendor != "postgresql":
            raise NotImplementedError(
                f"{self.__class__.__name__} only supports PostgreSQL"
            )

        columns = []

        for field in self._fields:
            if "->>" in field:
                columns.append(f"({field})")
            else:
                columns.append(
                    model._meta.get_field(field).column  # type: ignore
                )

        condition = self._get_condition_sql(model, schema_editor)

        if self._nulls_distinct is True:
            nulls_distinct = " NULLS DISTINCT"
        elif self._nulls_distinct is False:
            nulls_distinct = " NULLS NOT DISTINCT"
        else:
            nulls_distinct = ""

        if condition:
            where = f" WHERE {condition}"
        else:
            where = ""

        table_quoted = Table(
            model._meta.db_table, schema_editor.connection.ops.quote_name
        )

        sql = (
            "CREATE UNIQUE INDEX %(name)s ON %(table)s (%(columns)s)"
            "%(nulls_distinct)s%(where)s"
        )

        return Statement(
            sql,
            name=self.name,
            table=table_quoted,
            columns=", ".join(columns),
            nulls_distinct=nulls_distinct,
            where=where,
        )  # type: ignore[return-value]

    def remove_sql(
        self,
        model: type[Model] | None,  # noqa: U100
        schema_editor: BaseDatabaseSchemaEditor | None,  # noqa: U100
    ) -> str:
        """Remove the index."""
        sql = "DROP INDEX %(name)s"
        return Statement(sql, name=self.name)  # type: ignore[return-value]

    def deconstruct(self) -> tuple[str, tuple[Any], dict[str, Any]]:
        """
        Deconstruct the index.

        Return what is serialized in the migration file.
        """
        path, args, kwargs = super().deconstruct()

        path = path.replace(
            "debusine.db.models.constraints", "debusine.db.models"
        )

        kwargs["fields"] = self._fields
        if self._condition:
            kwargs["condition"] = self._condition
        if self._nulls_distinct is not None:
            kwargs["nulls_distinct"] = self._nulls_distinct

        return path, tuple(args), kwargs

    def __eq__(self, other: Any) -> bool:
        """Return True if other is equal to self."""
        if isinstance(other, JsonDataUniqueConstraint):
            return (
                self._fields == other._fields
                and self._condition == other._condition
                and self._nulls_distinct is other._nulls_distinct
                and self.name == other.name
            )

        return super().__eq__(other)
