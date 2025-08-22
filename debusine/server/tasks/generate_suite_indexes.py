# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Server-side task to generate indexes for a suite."""

import lzma
import shutil
import tempfile
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path, PurePath
from typing import Any

from debian.deb822 import Release
from django.contrib.postgres.aggregates import StringAgg
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Func,
    JSONField,
    OuterRef,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.expressions import Combinable, CombinedExpression, RawSQL
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Coalesce, Concat, Left
from django.db.models.sql.compiler import SQLCompiler
from django.db.models.sql.constants import INNER
from django.db.models.sql.datastructures import BaseTable
from django_pglocks import advisory_lock

from debusine.artifacts import RepositoryIndex
from debusine.artifacts.models import ArtifactCategory
from debusine.db.locks import LockError, LockType
from debusine.db.models import (
    Artifact,
    ArtifactRelation,
    Collection,
    CollectionItem,
    FileInArtifact,
)
from debusine.server.tasks import BaseServerTask
from debusine.server.tasks.models import (
    GenerateSuiteIndexesData,
    GenerateSuiteIndexesDynamicData,
)
from debusine.tasks import TaskConfigError
from debusine.tasks.server import TaskDatabaseInterface
from debusine.utils import calculate_hash

# Loosely based on the metadata_keys table in Debian's dak database.
_metadata_ordering = (
    "Package",
    "Source",
    "Binary",
    "Version",
    "Essential",
    "Installed-Size",
    "Maintainer",
    "Uploaders",
    "Original-Maintainer",
    "Build-Depends",
    "Build-Depends-Indep",
    "Build-Depends-Arch",
    "Build-Conflicts",
    "Build-Conflicts-Indep",
    "Build-Conflicts-Arch",
    "Architecture",
    "Standards-Version",
    "Format",
    "Files",
    "Dm-Upload-Allowed",
    "Vcs-Browser",
    "Vcs-%",
    "Checksums-%",
    "Replaces",
    "Provides",
    "Depends",
    "Pre-Depends",
    "Recommends",
    "Suggests",
    "Enhances",
    "Conflicts",
    "Breaks",
    "Description",
    "Origin",
    "Bugs",
    "Multi-Arch",
    "Homepage",
    "%",
    "Tag",
    "Package-Type",
    "Installer-Menu-Item",
)


def _release_sort_key(key: str) -> tuple[int, str | None]:
    """
    Sort order for Release fields.

    This is based on what dak produces, and is cosmetic.
    """
    fixed_start = (
        "Origin",
        "Label",
        "Suite",
        "Version",
        "Codename",
        "Changelogs",
        "Date",
        "Valid-Until",
        "NotAutomatic",
        "ButAutomaticUpgrades",
        "Acquire-By-Hash",
        "No-Support-for-Architecture-all",
        "Architectures",
        "Components",
        "Description",
    )
    fixed_end = ("MD5Sum", "SHA1", "SHA256")
    try:
        return fixed_start.index(key), None
    except ValueError:
        pass
    try:
        return len(fixed_start) + 1 + fixed_end.index(key), None
    except ValueError:
        pass
    # Sort anything else alphabetically between the keys in fixed_start and
    # those in fixed_end.
    return len(fixed_start), key


class FunctionJoin(BaseTable):
    """
    Join a set-returning function.

    See:
    https://forum.djangoproject.com/t/joining-a-set-returning-function-and-aggregating-its-output/40930

    Although this is really a join, ``Query.join`` accepts either ``Join``
    or ``BaseTable``, and we subclass ``BaseTable`` instead since it's less
    inconvenient.
    """

    def __init__(
        self,
        table_name: str,
        alias: str | None,
        function: Func,
        use_alias: bool = True,
    ) -> None:
        """Construct the join."""
        super().__init__(table_name=table_name, alias=alias)
        self.function = function
        self.use_alias = use_alias

    def as_sql(
        self,
        compiler: SQLCompiler,
        connection: BaseDatabaseWrapper,  # noqa: U100
    ) -> tuple[str, list[str | int]]:
        """Generate an SQL fragment to be included in the current query."""
        alias_str = (
            f" {self.table_name}"
            if self.table_alias is None or not self.use_alias
            else f" {self.table_alias}"
        )
        sql, params = compiler.compile(self.function)
        return f"{INNER} {sql}{alias_str} ON true", list(params)

    def relabeled_clone(
        self, change_map: dict[str | None, str]
    ) -> "FunctionJoin":
        """Return a clone of ``self``, with any column aliases relabeled."""
        return self.__class__(
            self.table_name,
            change_map.get(self.table_alias, self.table_alias),
            self.function,
            use_alias=self.use_alias,
        )

    @property
    def identity(self) -> tuple[Any, ...]:
        """Return object identity, for comparison and hashing."""
        return (
            self.__class__,
            self.table_name,
            self.table_alias,
            # https://github.com/typeddjango/django-stubs/pull/2685
            *getattr(self.function, "identity"),
        )


class JSONBEach(Func):
    """
    Expand a JSON object into a set of key/value pairs.

    Since Django doesn't support set-returning functions very well, this is
    just a minimal wrapper and in practice needs to be used with ``RawSQL``.
    """

    function = "jsonb_each"
    arity = 1


class JSONBEachText(Func):
    """
    Expand a JSON object into a set of key/value pairs, parsing values as text.

    Since Django doesn't support set-returning functions very well, this is
    just a minimal wrapper and in practice needs to be used with ``RawSQL``.
    """

    function = "jsonb_each_text"
    arity = 1


class JSONBToRecordSet(Func):
    """
    Expand a JSON array into a set of its rows.

    Since Django doesn't support set-returning functions very well, this is
    just a minimal wrapper and in practice needs to be used with ``RawSQL``.
    The join must explicitly define the row structure using the table name
    and ``use_alias=False``.
    """

    function = "jsonb_to_recordset"
    arity = 1


class JSONBTypeOf(Func):
    """Return the type of a JSON value."""

    function = "jsonb_typeof"
    arity = 1
    output_field = CharField()


class JSONStringValue(CombinedExpression):
    """Parse a JSON value already known to be a string."""

    output_field = CharField()

    def __init__(self, item: Combinable) -> None:
        """Construct the expression."""
        super().__init__(item, "->>", Value(0))


class Encode(Func):
    """
    Encode binary data into a text representation.

    Note that this follows PostgreSQL's naming for encode/decode, which is
    the other way round from Python's (where "encode" goes from text to
    binary).
    """

    function = "encode"
    arity = 2
    output_field = CharField()


class CompareBoolean(CombinedExpression):
    """Compare two expressions, returning a boolean."""

    output_field = BooleanField()


@dataclass
class IndexInfo:
    """Information about an index file, used to generate ``Release``."""

    size: int
    sha256: str
    artifact: Artifact | None = None


class GenerateSuiteIndexes(
    BaseServerTask[GenerateSuiteIndexesData, GenerateSuiteIndexesDynamicData]
):
    """Task that generates indexes for a suite."""

    TASK_VERSION = 1

    def __init__(
        self,
        task_data: dict[str, Any],
        dynamic_task_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the task."""
        super().__init__(task_data, dynamic_task_data)
        self.index_info: dict[PurePath, IndexInfo] = {}
        self.indexes: dict[PurePath, Artifact] = {}

    def build_dynamic_data(
        self, task_database: TaskDatabaseInterface
    ) -> GenerateSuiteIndexesDynamicData:
        """Resolve lookups for this task."""
        return GenerateSuiteIndexesDynamicData(
            suite_collection_id=task_database.lookup_single_collection(
                self.data.suite_collection
            ).id
        )

    def get_input_artifacts_ids(self) -> list[int]:
        """Return the list of input artifact IDs used by this task."""
        # Our dynamic data includes a collection, but no artifacts.
        return []

    @cached_property
    def suite(self) -> Collection:
        """The `debian:suite` collection this task is operating on."""
        assert self.dynamic_data is not None
        return Collection.objects.get(id=self.dynamic_data.suite_collection_id)

    @cached_property
    def components(self) -> list[str]:
        """The components for which to generate indexes in this suite."""
        try:
            components = self.suite.data["components"]
        except KeyError:
            raise TaskConfigError(
                f"Collection '{self.data.suite_collection}' has no "
                f"'components' configuration"
            )
        else:
            # TODO: This should move to Pydantic.
            if not isinstance(components, list):
                raise TaskConfigError(
                    f"Expected 'components' as a list; got {components!r}"
                )
            return components

    @cached_property
    def architectures(self) -> list[str]:
        """The architectures for which to generate indexes in this suite."""
        try:
            architectures = self.suite.data["architectures"]
        except KeyError:
            raise TaskConfigError(
                f"Collection '{self.data.suite_collection}' has no "
                f"'architectures' configuration"
            )
        else:
            # TODO: This should move to Pydantic.
            if not isinstance(architectures, list):
                raise TaskConfigError(
                    f"Expected 'architectures' as a list; got {architectures!r}"
                )
            return architectures

    def _add_artifact(
        self,
        *,
        path_in_suite: PurePath,
        path: Path,
        newer_item: CollectionItem | None,
    ) -> Artifact:
        assert self.work_request is not None
        assert self.workspace is not None

        artifact = Artifact.objects.create_from_local_artifact(
            RepositoryIndex.create(file=path, path=path_in_suite.as_posix()),
            self.workspace,
            created_by_work_request=self.work_request,
        )
        self.suite.manager.add_artifact(
            artifact,
            user=self.work_request.created_by,
            workflow=self.work_request.parent,
            variables={"path": path_in_suite.as_posix()},
            name=f"index:{path_in_suite.as_posix()}",
            created_at=self.data.generate_at,
            replaced_by=newer_item,
        )
        return artifact

    def _add_index_info(
        self,
        *,
        path_in_suite: PurePath,
        path: Path,
        artifact: Artifact | None = None,
    ) -> None:
        self.index_info[path_in_suite] = IndexInfo(
            size=path.stat().st_size,
            sha256=calculate_hash(path, "sha256").hex(),
            artifact=artifact,
        )

    @cache
    def _make_field_ordering(self, key_field: RawSQL) -> Case:
        conditions: list[When] = []
        for i, name in enumerate(_metadata_ordering):
            if name == "%":
                continue
            elif "%" in name:
                conditions.append(
                    When(CompareBoolean(key_field, "LIKE", Value(name)), then=i)
                )
            else:
                conditions.append(
                    When(CompareBoolean(key_field, "=", Value(name)), then=i)
                )
        return Case(*conditions, default=_metadata_ordering.index("%"))

    def generate_sources(
        self,
        *,
        temp_path: Path,
        component: str,
        newer_item: CollectionItem | None = None,
    ) -> None:
        """Generate a ``Sources`` file."""
        qs: QuerySet[CollectionItem] = (
            CollectionItem.objects.artifacts_in_collection(
                parent_collection=self.suite,
                category=ArtifactCategory.SOURCE_PACKAGE,
            )
            .active_at(self.data.generate_at)
            .annotate(component=KT("data__component"))
            .filter(
                component=component,
                # Redundant with the collection item category filter above,
                # but serves to tell Django that it needs to join
                # db_artifact before our manual join of dsc_fields.
                artifact__category=ArtifactCategory.SOURCE_PACKAGE,
            )
            # TODO: Once we have the debversion extension, we should sort by
            # true version ordering rather than just lexicographically by
            # version.
            .order_by(KT("data__package"), KT("data__version"))
        )
        qs.query.join(
            FunctionJoin(
                "dsc_fields",
                None,
                JSONBEach(RawSQL("db_artifact.data->'dsc_fields'", ())),
            )
        )

        checksums_sha256 = (
            FileInArtifact.objects.filter(artifact=OuterRef("artifact__id"))
            # See: https://stackoverflow.com/a/64902200
            .annotate(dummy_group_by=Value(1))
            .values("dummy_group_by")
            .annotate(
                field=Concat(
                    Value("Checksums-Sha256:"),
                    StringAgg(
                        Concat(
                            Value("\n "),
                            Encode("file__sha256", Value("hex")),
                            Value(" "),
                            Cast("file__size", CharField()),
                            Value(" "),
                            "path",
                        ),
                        delimiter="",
                        ordering="path",
                    ),
                    output_field=CharField(),
                )
            )
            .values("field")
        )

        # python-debian >= 0.1.50 parses Package-List, and if the artifact
        # was created with that version then that will be stored in
        # dsc_fields.  Make a subquery to serialize it again.
        package_list: QuerySet[Artifact, Any] = Artifact.objects.filter(
            id=OuterRef("artifact__id")
        )
        package_list.query.join(
            FunctionJoin(
                'package_list(package text, "package-type" text, '
                'section text, priority text, _other text)',
                None,
                JSONBToRecordSet(
                    RawSQL("db_artifact.data->'dsc_fields'->'Package-List'", ())
                ),
                use_alias=False,
            )
        )
        package_list_fields = {
            name: RawSQL(f'package_list."{name}"', (), output_field=CharField())
            for name in (
                "package",
                "package-type",
                "section",
                "priority",
                "_other",
            )
        }
        package_list = (
            package_list
            # See: https://stackoverflow.com/a/64902200
            .annotate(dummy_group_by=Value(1))
            .values("dummy_group_by")
            .annotate(
                field=Concat(
                    Value("Package-List: "),
                    StringAgg(
                        Concat(
                            Value("\n "),
                            package_list_fields["package"],
                            Value(" "),
                            package_list_fields["package-type"],
                            Value(" "),
                            package_list_fields["section"],
                            Value(" "),
                            package_list_fields["priority"],
                            Value(" "),
                            package_list_fields["_other"],
                        ),
                        delimiter="",
                    ),
                    output_field=CharField(),
                )
            )
            .values("field")
        )

        dsc_fields_key = RawSQL("dsc_fields.key", (), output_field=CharField())
        dsc_fields_value = RawSQL(
            "dsc_fields.value", (), output_field=JSONField()
        )
        render_dsc_field = Case(
            When(
                CompareBoolean(dsc_fields_key, "=", Value("Source")),
                then=Concat(
                    Value("Package: "), JSONStringValue(dsc_fields_value)
                ),
            ),
            # Debusine doesn't currently store MD5 or SHA-1 checksums.
            # Since dak generates a number of *-updates suites without those
            # (although unstable currently still has MD5 checksums), we can
            # probably get away without them.
            When(
                CompareBoolean(dsc_fields_key, "=", Value("Files")), then=None
            ),
            When(
                CompareBoolean(dsc_fields_key, "=", Value("Checksums-Sha256")),
                then=Subquery(checksums_sha256),
            ),
            When(
                CompareBoolean(dsc_fields_key, "LIKE", Value("Checksums-%")),
                then=None,
            ),
            When(
                CompareBoolean(dsc_fields_key, "=", Value("Package-List"))
                & CompareBoolean(
                    JSONBTypeOf(dsc_fields_value), "=", Value("array")
                ),
                then=Subquery(package_list),
            ),
            default=Concat(
                dsc_fields_key, Value(": "), JSONStringValue(dsc_fields_value)
            ),
        )

        qs = qs.values_list(
            Concat(
                # Emit fields from .dsc or related files.
                StringAgg(
                    render_dsc_field,
                    delimiter="\n",
                    ordering=self._make_field_ordering(dsc_fields_key),
                    output_field=CharField(),
                ),
                # Emit Directory.
                Concat(
                    Value(f"\nDirectory: pool/{component}/"),
                    Case(
                        When(
                            data__package__startswith="lib",
                            then=Left(KT("data__package"), 4),
                        ),
                        default=Left(KT("data__package"), 1),
                    ),
                    Value("/"),
                    Cast(KT("data__package"), CharField()),
                ),
                # Emit Priority.  (Debusine doesn't currently track priority
                # for source packages; dak at least sometimes uses
                # "Priority: source", which should be good enough.)
                Value("\nPriority: source"),
                # Emit Section.
                Concat(
                    Value("\nSection: "),
                    Coalesce(
                        Cast(KT("data__section"), CharField()), Value("misc")
                    ),
                ),
            ),
            flat=True,
        )

        sources_path_in_suite = PurePath(component, "source", "Sources")
        sources_path = temp_path / sources_path_in_suite
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        with sources_path.open("w") as f:
            for stanza in qs:
                print(stanza, file=f)
                print(file=f)
        self._add_index_info(
            path_in_suite=sources_path_in_suite, path=sources_path
        )

        sources_xz_path_in_suite = sources_path_in_suite.with_suffix(".xz")
        sources_xz_path = sources_path.with_suffix(".xz")
        with (
            sources_path.open(mode="rb") as uncompressed,
            lzma.open(
                sources_xz_path, mode="wb", format=lzma.FORMAT_XZ
            ) as compressed,
        ):
            shutil.copyfileobj(uncompressed, compressed)
        sources_xz_artifact = self._add_artifact(
            path_in_suite=sources_xz_path_in_suite,
            path=sources_xz_path,
            newer_item=newer_item,
        )
        self._add_index_info(
            path_in_suite=sources_xz_path_in_suite,
            path=sources_xz_path,
            artifact=sources_xz_artifact,
        )

    def generate_packages(
        self,
        *,
        temp_path: Path,
        component: str,
        architecture: str,
        newer_item: CollectionItem | None = None,
    ) -> None:
        """Generate a ``Packages`` file."""
        architectures = {architecture}
        if architecture != "all" and (
            "all" not in self.architectures
            or self.suite.data.get("duplicate_architecture_all", False)
        ):
            architectures.add("all")
        qs: QuerySet[CollectionItem] = (
            CollectionItem.objects.artifacts_in_collection(
                parent_collection=self.suite,
                category=ArtifactCategory.BINARY_PACKAGE,
            )
            .active_at(self.data.generate_at)
            .annotate(
                component=KT("data__component"),
                architecture=KT("data__architecture"),
            )
            .filter(
                component=component,
                architecture__in=architectures,
                # Redundant with the collection item category filter above,
                # but serves to tell Django that it needs to join
                # db_artifact before our manual join of deb_fields.
                artifact__category=ArtifactCategory.BINARY_PACKAGE,
            )
            # TODO: Once we have the debversion extension, we should sort by
            # true version ordering rather than just lexicographically by
            # version.
            .order_by(
                KT("data__srcpkg_name"),
                KT("data__package"),
                KT("data__version"),
            )
        )
        qs.query.join(
            FunctionJoin(
                "deb_fields",
                None,
                JSONBEachText(RawSQL("db_artifact.data->'deb_fields'", ())),
            )
        )
        deb_fields_key = RawSQL("deb_fields.key", (), output_field=CharField())
        deb_fields_value = RawSQL(
            "deb_fields.value", (), output_field=CharField()
        )
        render_deb_field = Case(
            When(
                CompareBoolean(deb_fields_key, "=", Value("Section")), then=None
            ),
            When(
                CompareBoolean(deb_fields_key, "=", Value("Priority")),
                then=None,
            ),
            default=Concat(deb_fields_key, Value(": "), deb_fields_value),
        )
        qs = qs.values_list(
            Concat(
                # Emit fields from .deb or related files.
                StringAgg(
                    render_deb_field,
                    delimiter="\n",
                    ordering=self._make_field_ordering(deb_fields_key),
                    output_field=CharField(),
                ),
                # Emit Section.
                Coalesce(
                    Concat(
                        Value("\nSection: "),
                        Cast(KT("data__section"), CharField()),
                    ),
                    Value(""),
                ),
                # Emit Priority.
                Coalesce(
                    Concat(
                        Value("\nPriority: "),
                        Cast(KT("data__priority"), CharField()),
                    ),
                    Value(""),
                ),
                # Emit Filename.
                # Note that debian:binary-package artifacts are guaranteed
                # to contain exactly one file.
                Concat(
                    Value(f"\nFilename: pool/{component}/"),
                    Case(
                        When(
                            data__package__startswith="lib",
                            then=Left(KT("data__srcpkg_name"), 4),
                        ),
                        default=Left(KT("data__srcpkg_name"), 1),
                    ),
                    Value("/"),
                    Cast(KT("data__srcpkg_name"), CharField()),
                    Value("/"),
                    "artifact__fileinartifact__path",
                ),
                # Emit Size.
                Concat(
                    Value("\nSize: "),
                    Cast("artifact__files__size", CharField()),
                ),
                # Emit SHA256.
                # Debusine doesn't currently store MD5 or SHA-1 checksums.
                # Since dak generates a number of *-updates suites without
                # those (although unstable currently still has MD5
                # checksums), we can probably get away without them.
                Concat(
                    Value("\nSHA256: "),
                    Encode("artifact__files__sha256", Value("hex")),
                ),
            ),
            flat=True,
        )

        packages_path_in_suite = PurePath(
            component, f"binary-{architecture}", "Packages"
        )
        packages_path = temp_path / packages_path_in_suite
        packages_path.parent.mkdir(parents=True, exist_ok=True)
        with packages_path.open("w") as f:
            for stanza in qs:
                print(stanza, file=f)
                print(file=f)
        self._add_index_info(
            path_in_suite=packages_path_in_suite, path=packages_path
        )

        packages_xz_path_in_suite = packages_path_in_suite.with_suffix(".xz")
        packages_xz_path = packages_path.with_suffix(".xz")
        with (
            packages_path.open(mode="rb") as uncompressed,
            lzma.open(
                packages_xz_path, mode="wb", format=lzma.FORMAT_XZ
            ) as compressed,
        ):
            shutil.copyfileobj(uncompressed, compressed)
        packages_xz_artifact = self._add_artifact(
            path_in_suite=packages_xz_path_in_suite,
            path=packages_xz_path,
            newer_item=newer_item,
        )
        self._add_index_info(
            path_in_suite=packages_xz_path_in_suite,
            path=packages_xz_path,
            artifact=packages_xz_artifact,
        )

    def generate_release(
        self, *, temp_path: Path, newer_item: CollectionItem | None = None
    ) -> None:
        """Generate a ``Release`` file."""
        release = Release()

        # Defaults; may be overridden by release_fields.
        release["Suite"] = release["Codename"] = self.suite.name

        release_fields = dict(self.suite.data.get("release_fields", {}))

        # Convert boolean fields.
        for field in (
            "NotAutomatic",
            "ButAutomaticUpgrades",
            "Acquire-By-Hash",
        ):
            value = release_fields.get(field)
            if isinstance(value, bool):
                if value:
                    release_fields[field] = "yes"
                else:
                    del release_fields[field]

        release.update(release_fields)

        # Set or remove some fields where we're authoritative, overriding
        # anything that might be in release_fields.
        release["Date"] = self.data.generate_at.strftime(
            "%a, %d %b %Y %H:%M:%S UTC"
        )
        # TODO: Support this by adding a field to the suite specifying the
        # validity period, relative to Date.
        release.pop("Valid-Until", None)
        if self.suite.data.get("duplicate_architecture_all", False):
            release["No-Support-for-Architecture-all"] = "Packages"
        else:
            release.pop("No-Support-for-Architecture-all", None)
        release["Architectures"] = " ".join(self.suite.data["architectures"])
        release["Components"] = " ".join(self.suite.data["components"])
        release.pop("MD5Sum", None)
        release.pop("SHA1", None)
        release.pop("SHA512", None)

        # Debusine currently only stores SHA256 checksums, so for now that's
        # all that we emit.
        # TODO: This currently only handles the files that we generate every
        # time (Packages/Sources).  Some files such as Contents may need to
        # be generated on different schedules.
        related: list[Artifact] = []
        # Deb822.__setitem__'s type for value doesn't quite match how
        # Release works.
        release["SHA256"] = []  # type: ignore[assignment]
        for rel_path, index_info in sorted(self.index_info.items()):
            release["SHA256"].append(
                {
                    "sha256": index_info.sha256,
                    "size": index_info.size,
                    "name": rel_path.as_posix(),
                }
            )
            if index_info.artifact is not None:
                related.append(index_info.artifact)

        release.sort_fields(key=_release_sort_key)
        release_path_in_suite = PurePath("Release")
        release_path = temp_path / release_path_in_suite
        with release_path.open("wb") as f:
            release.dump(f)
        release_artifact = self._add_artifact(
            path_in_suite=release_path_in_suite,
            path=release_path,
            newer_item=newer_item,
        )
        for target in related:
            ArtifactRelation.objects.create(
                artifact=release_artifact,
                target=target,
                type=ArtifactRelation.Relations.RELATES_TO,
            )

    def _find_indexes(self) -> QuerySet[CollectionItem]:
        """Find index items in the suite collection."""
        return CollectionItem.objects.filter(
            parent_collection=self.suite,
            child_type=CollectionItem.Types.ARTIFACT,
            name__regex=(
                r"^index:([^/]*/[^/]*/(Sources|Packages)[^/]*|Release)$"
            ),
        )

    def supersede_older_items(self) -> None:
        """Remove old items superseded by this task."""
        assert self.work_request is not None

        previous_generation = (
            self._find_indexes()
            .filter(
                created_at__lt=self.data.generate_at,
            )
            .order_by("-created_at")
            .first()
        )
        if previous_generation is not None:
            self._find_indexes().filter(
                created_at=previous_generation.created_at,
            ).update(
                removed_by_user=self.work_request.created_by,
                removed_by_workflow=self.work_request.parent,
                removed_at=self.data.generate_at,
            )

    def find_newer_item(self) -> CollectionItem | None:
        """Find the earliest item newer than the ones we're generating."""
        return (
            self._find_indexes()
            .filter(
                created_at__gt=self.data.generate_at,
            )
            # Fall back to ordering by ID, to improve testability.
            .order_by("created_at", "id")
            .first()
        )

    def _execute(self) -> bool:
        """Execute the task."""
        with advisory_lock(
            (
                LockType.GENERATE_SUITE_INDEXES,
                # Only use the bottom 31 bits, in order that this fits into
                # PostgreSQL's int type.  In the unlikely event that we have
                # enough mirrored collections for there to be a collision,
                # then it just means that the colliding collections can't
                # have indexes generated for them simultaneously.
                self.suite.id & (2**31 - 1),
            ),
            wait=False,
        ) as acquired:
            if not acquired:
                raise LockError(
                    f"Another GenerateSuiteIndexes task for "
                    f"{self.data.suite_collection} is already running"
                )

            self.indexes = {}
            self.supersede_older_items()
            newer_item = self.find_newer_item()
            with tempfile.TemporaryDirectory(
                prefix="debusine-generate-suite-indexes-"
            ) as temp_dir:
                temp_path = Path(temp_dir)
                for component in self.components:
                    self.generate_sources(
                        temp_path=temp_path,
                        component=component,
                        newer_item=newer_item,
                    )
                    for architecture in self.architectures:
                        self.generate_packages(
                            temp_path=temp_path,
                            component=component,
                            architecture=architecture,
                            newer_item=newer_item,
                        )
                self.generate_release(
                    temp_path=temp_path, newer_item=newer_item
                )
                return True

    def get_label(self) -> str:
        """Return the task label."""
        return (
            f"generate indexes for {self.data.suite_collection} "
            f"at {self.data.generate_at}"
        )
