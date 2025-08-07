# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Unit tests for task models."""

import re
import textwrap
from datetime import datetime, timezone
from typing import get_args
from unittest import TestCase

import debusine.tasks.models as task_models
from debusine.artifacts.models import ArtifactCategory


class LookupTests(TestCase):
    """Tests for collection item lookups."""

    def test_string(self) -> None:
        """Single-item lookups accept strings."""
        self.assertIsInstance(
            "debian@debian:archive",
            get_args(task_models.LookupSingle),
        )

    def test_integer(self) -> None:
        """Single-item lookups accept integers."""
        self.assertIsInstance(1, get_args(task_models.LookupSingle))

    def test_dict(self) -> None:
        """Multiple-item lookups accept dicts."""
        raw_lookup = {"collection": "debian@debian:archive"}
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        self.assertEqual(
            lookup.dict(),
            {
                "__root__": (
                    {
                        "collection": "debian@debian:archive",
                        "child_type": "artifact",
                        "category": None,
                        "name_matcher": None,
                        "data_matchers": (),
                        "lookup_filters": (),
                    },
                )
            },
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_child_type(self) -> None:
        """`child_type` is accepted."""
        raw_lookup = {
            "collection": "debian@debian:archive",
            "child_type": "collection",
        }
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        self.assertEqual(
            lookup.dict(),
            {
                "__root__": (
                    {
                        "collection": "debian@debian:archive",
                        "child_type": "collection",
                        "category": None,
                        "name_matcher": None,
                        "data_matchers": (),
                        "lookup_filters": (),
                    },
                )
            },
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_category(self) -> None:
        """`child_type` is accepted."""
        raw_lookup = {
            "collection": "bookworm@debian:suite",
            "category": ArtifactCategory.SOURCE_PACKAGE,
        }
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        self.assertEqual(
            lookup.dict(),
            {
                "__root__": (
                    {
                        "collection": "bookworm@debian:suite",
                        "child_type": "artifact",
                        "category": ArtifactCategory.SOURCE_PACKAGE,
                        "name_matcher": None,
                        "data_matchers": (),
                        "lookup_filters": (),
                    },
                )
            },
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_name_matcher(self) -> None:
        """A `name` lookup is accepted."""
        raw_lookup = {"collection": "debian@debian:archive", "name": "foo"}
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        self.assertEqual(
            lookup.dict()["__root__"][0]["name_matcher"],
            task_models.CollectionItemMatcher(
                kind=task_models.CollectionItemMatcherKind.EXACT, value="foo"
            ),
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_name_matcher_rejects_exact(self) -> None:
        """`name__exact` is rejected."""
        with self.assertRaisesRegex(
            ValueError, "Unrecognized matcher: name__exact"
        ):
            task_models.LookupMultiple.parse_obj(
                {"collection": "debian@debian:archive", "name__exact": "foo"}
            )

    def test_name_matcher_lookup(self) -> None:
        """Lookups such as `name__contains` are accepted."""
        raw_lookup = {
            "collection": "debian@debian:archive",
            "name__contains": "foo",
        }
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        self.assertEqual(
            lookup.dict()["__root__"][0]["name_matcher"],
            task_models.CollectionItemMatcher(
                kind=task_models.CollectionItemMatcherKind.CONTAINS, value="foo"
            ),
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_name_matcher_rejects_extra_segments(self) -> None:
        """A `name` lookup followed by two segments is rejected."""
        with self.assertRaisesRegex(
            ValueError, "Unrecognized matcher: name__foo__bar"
        ):
            task_models.LookupMultiple.parse_obj(
                {"collection": "debian@debian:archive", "name__foo__bar": "foo"}
            )

    def test_name_matcher_conflict(self) -> None:
        """Conflicting name lookups are rejected."""
        with self.assertRaisesRegex(
            ValueError,
            re.escape(
                "Conflicting matchers: ['name', 'name__contains', "
                "'name__endswith', 'name__startswith']"
            ),
        ):
            task_models.LookupMultiple.parse_obj(
                {
                    "collection": "debian@debian:archive",
                    "name": "foo",
                    "name__contains": "foo",
                    "name__endswith": "foo",
                    "name__startswith": "foo",
                }
            )

    def test_data_matcher(self) -> None:
        """A `data__KEY` lookup is accepted."""
        raw_lookup = {
            "collection": "debian@debian:archive",
            "data__package": "foo",
        }
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        self.assertEqual(
            lookup.dict()["__root__"][0]["data_matchers"],
            (
                (
                    "package",
                    task_models.CollectionItemMatcher(
                        kind=task_models.CollectionItemMatcherKind.EXACT,
                        value="foo",
                    ),
                ),
            ),
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_data_matcher_rejects_exact(self) -> None:
        """`data__KEY__exact` is rejected."""
        with self.assertRaisesRegex(
            ValueError, "Unrecognized matcher: data__package__exact"
        ):
            task_models.LookupMultiple.parse_obj(
                {
                    "collection": "debian@debian:archive",
                    "data__package__exact": "foo",
                }
            )

    def test_data_matcher_lookup(self) -> None:
        """Lookups such as `data__KEY__contains` are accepted."""
        raw_lookup = {
            "collection": "debian@debian:archive",
            "data__package__contains": "foo",
            "data__version__startswith": "1.",
        }
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        self.assertEqual(
            lookup.dict()["__root__"][0]["data_matchers"],
            (
                (
                    "package",
                    task_models.CollectionItemMatcher(
                        kind=task_models.CollectionItemMatcherKind.CONTAINS,
                        value="foo",
                    ),
                ),
                (
                    "version",
                    task_models.CollectionItemMatcher(
                        kind=task_models.CollectionItemMatcherKind.STARTSWITH,
                        value="1.",
                    ),
                ),
            ),
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_data_matcher_rejects_extra_segments(self) -> None:
        """A `data` lookup followed by three segments is rejected."""
        with self.assertRaisesRegex(
            ValueError, "Unrecognized matcher: data__package__foo__bar"
        ):
            task_models.LookupMultiple.parse_obj(
                {
                    "collection": "debian@debian:archive",
                    "data__package__foo__bar": "foo",
                }
            )

    def test_data_matcher_conflict(self) -> None:
        """Conflicting data lookups are rejected."""
        with self.assertRaisesRegex(
            ValueError,
            re.escape(
                "Conflicting matchers: ['data__package', "
                "'data__package__contains', 'data__package__endswith', "
                "'data__package__startswith']"
            ),
        ):
            task_models.LookupMultiple.parse_obj(
                {
                    "collection": "debian@debian:archive",
                    "data__package": "foo",
                    "data__package__contains": "foo",
                    "data__package__endswith": "foo",
                    "data__package__startswith": "foo",
                }
            )

    def test_lookup_filter_single(self) -> None:
        """`lookup__KEY` with a single-lookup value is accepted."""
        subordinate_lookup = "single"
        raw_lookup = {
            "collection": "_@debian:package-build-logs",
            "lookup__foo": subordinate_lookup,
        }
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        assert isinstance(lookup.__root__[0], task_models.LookupDict)
        self.assertEqual(
            lookup.dict()["__root__"][0]["lookup_filters"],
            (("foo", subordinate_lookup),),
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_lookup_filter_multiple(self) -> None:
        """`lookup__KEY` with a multiple-lookup value is accepted."""
        subordinate_lookup = ["internal@collections/name:build-amd64"]
        raw_lookup = {
            "collection": "_@debian:package-build-logs",
            "lookup__same_work_request": subordinate_lookup,
        }
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        assert isinstance(lookup.__root__[0], task_models.LookupDict)
        self.assertEqual(
            lookup.__root__[0].lookup_filters,
            (
                (
                    "same_work_request",
                    task_models.LookupMultiple.parse_obj(subordinate_lookup),
                ),
            ),
        )
        self.assertEqual(
            lookup.dict()["__root__"][0]["lookup_filters"],
            (("same_work_request", tuple(subordinate_lookup)),),
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_list(self) -> None:
        """Multiple-item lookups accept lists."""
        raw_lookup = [
            {"collection": "debian@debian:archive"},
            {"collection": "kali@debian:archive"},
            "123@artifacts",
            124,
        ]
        lookup = task_models.LookupMultiple.parse_obj(raw_lookup)
        self.assertEqual(
            lookup.dict(),
            {
                "__root__": (
                    {
                        "collection": "debian@debian:archive",
                        "child_type": "artifact",
                        "category": None,
                        "name_matcher": None,
                        "data_matchers": (),
                        "lookup_filters": (),
                    },
                    {
                        "collection": "kali@debian:archive",
                        "child_type": "artifact",
                        "category": None,
                        "name_matcher": None,
                        "data_matchers": (),
                        "lookup_filters": (),
                    },
                    "123@artifacts",
                    124,
                )
            },
        )
        self.assertEqual(lookup.export(), raw_lookup)

    def test_wrong_type(self) -> None:
        """Multiple-item lookups only accept dicts or lists."""
        with self.assertRaisesRegex(
            ValueError,
            "Lookup of multiple collection items must be a dictionary or a "
            "list",
        ):
            task_models.LookupMultiple.parse_obj("foo")


class ActionRetryWithDelaysTests(TestCase):
    """Tests for :py:class:`ActionRetryWithDelays`."""

    def test_delays(self) -> None:
        """A valid `delays` field is accepted."""
        delays = ["30m", "1h", "2d", "1w"]
        action = task_models.ActionRetryWithDelays(delays=delays)
        self.assertEqual(
            action.dict(), {"action": "retry-with-delays", "delays": delays}
        )

    def test_delays_bad_format(self) -> None:
        """Items in `delays` must be integers followed by m/h/d/w."""
        with self.assertRaisesRegex(
            ValueError,
            "Item in delays must be an integer followed by m, h, d, or w; got "
            "'2 weeks'",
        ):
            task_models.ActionRetryWithDelays(delays=["2 weeks"])


class EnumTests(TestCase):
    """Tests for SystemImageBuild task data."""

    def test_enums_str(self) -> None:
        """Test enum stringification."""
        for enum_cls in (
            task_models.AutopkgtestNeedsInternet,
            task_models.BlhcFlags,
            task_models.DebDiffFlags,
            task_models.DebootstrapVariant,
            task_models.DiskImageFormat,
            task_models.LintianFailOnSeverity,
            task_models.MmDebstrapVariant,
            task_models.SbuildBuildComponent,
            task_models.SystemBootstrapRepositoryCheckSignatureWith,
            task_models.SystemBootstrapRepositoryType,
        ):
            with self.subTest(enum_cls=enum_cls):
                for el in enum_cls:
                    with self.subTest(el=el):
                        self.assertEqual(str(el), el.value)


class LintianDataTests(TestCase):
    """Tests for Lintian task data."""

    def test_input_validation(self) -> None:
        """Test LintianInput validation."""
        task_models.LintianInput(source_artifact=1)
        task_models.LintianInput(
            binary_artifacts=task_models.LookupMultiple.parse_obj([1])
        )
        task_models.LintianInput(
            binary_artifacts=task_models.LookupMultiple.parse_obj([1, 2])
        )

        error_msg = "One of source_artifact or binary_artifacts must be set"
        with self.assertRaisesRegex(ValueError, error_msg):
            task_models.LintianInput()
        with self.assertRaisesRegex(ValueError, error_msg):
            task_models.LintianInput(
                binary_artifacts=task_models.empty_lookup_multiple()
            )


class SystemBootstrapDataTests(TestCase):
    """Tests for SystemBootstrap task data."""

    def test_repository_validation(self) -> None:
        """Test SystemBootstrapRepository validation."""
        common_kwargs = {
            "mirror": "https://deb.debian.org/deb",
            "suite": "bookworm",
        }

        task_models.SystemBootstrapRepository.parse_obj(common_kwargs)

        error_msg = "ensure this value has at least 1 items"
        with self.assertRaisesRegex(ValueError, error_msg):
            task_models.SystemBootstrapRepository.parse_obj(
                dict(types=[], **common_kwargs)
            )

        task_models.SystemBootstrapRepository.parse_obj(
            dict(
                check_signature_with="external",
                keyring={"url": "https://example.com/keyring_file.txt"},
                **common_kwargs,
            )
        )

        error_msg = (
            "repository requires 'keyring':"
            " 'check_signature_with' is set to 'external'"
        )
        with self.assertRaisesRegex(ValueError, error_msg):
            task_models.SystemBootstrapRepository.parse_obj(
                dict(check_signature_with="external", **common_kwargs)
            )

    def test_repository_validation_duplicate_list_items(self) -> None:
        """Test SystemBootstrapRepository validation of duplicate list items."""
        common_kwargs = {
            "mirror": "https://deb.debian.org/deb",
            "suite": "bookworm",
        }

        task_models.SystemBootstrapRepository.parse_obj(common_kwargs)

        error_msg = "the list has duplicated items"
        with self.assertRaisesRegex(ValueError, error_msg):
            task_models.SystemBootstrapRepository.parse_obj(
                dict(types=["deb", "deb"], **common_kwargs)
            )
        with self.assertRaisesRegex(ValueError, error_msg):
            task_models.SystemBootstrapRepository.parse_obj(
                dict(components=["main", "main"], **common_kwargs)
            )

    def test_keyring_file_url_under_usr_share_keyrings(self) -> None:
        """Keyring file:// URLs under /usr/share/keyrings/ are allowed."""
        task_models.SystemBootstrapRepository.parse_obj(
            {
                "mirror": "https://deb.debian.org/deb",
                "suite": "bookworm",
                "check_signature_with": "external",
                "keyring": {
                    "url": (
                        "file:///usr/share/keyrings/debian-archive-keyring.gpg"
                    )
                },
            }
        )

    def test_keyring_file_url_under_usr_local_share_keyrings(self) -> None:
        """Keyring file:// URLs under /usr/local/share/keyrings/ are allowed."""
        task_models.SystemBootstrapRepository.parse_obj(
            {
                "mirror": "https://deb.debian.org/deb",
                "suite": "bookworm",
                "check_signature_with": "external",
                "keyring": {
                    "url": (
                        "file:///usr/local/share/keyrings/local-keyring.gpg"
                    )
                },
            }
        )

    def test_keyring_file_url_not_under_allowed_directory(self) -> None:
        """Keyring file:// URLs not under an allowed directory are rejected."""
        common_kwargs = {
            "mirror": "https://deb.debian.org/deb",
            "suite": "bookworm",
            "check_signature_with": "external",
        }

        error_msg = (
            "file:// URLs for keyrings must be under /usr/share/keyrings/ or "
            "/usr/local/share/keyrings/"
        )
        with self.assertRaisesRegex(ValueError, error_msg):
            task_models.SystemBootstrapRepository.parse_obj(
                {"keyring": {"url": "file:///etc/passwd", **common_kwargs}}
            )
        with self.assertRaisesRegex(ValueError, error_msg):
            task_models.SystemBootstrapRepository.parse_obj(
                {
                    "keyring": {
                        "url": "file:///usr/share/keyringssuffix",
                        **common_kwargs,
                    }
                }
            )
        with self.assertRaisesRegex(ValueError, error_msg):
            task_models.SystemBootstrapRepository.parse_obj(
                {
                    "keyring": {
                        "url": "file:///usr/share/keyrings/../escape",
                        **common_kwargs,
                    }
                }
            )


class AutopkgtestDataTests(TestCase):
    """Tests for Autopkgtest task data."""

    def test_validation(self) -> None:
        """Test AutopkgtestData validation."""
        common_kwargs = {
            "input": {
                "source_artifact": 1,
                "binary_artifacts": [1, 2],
            },
            "host_architecture": "amd64",
            "environment": 1,
        }

        task_models.AutopkgtestData.parse_obj(common_kwargs)

        for field in ("global", "factor", "short", "install", "test", "copy"):
            with self.subTest(field=field):
                error_msg = (
                    f"timeout -> {field}\n"
                    f"  ensure this value is greater than or equal to 0"
                )
                with self.assertRaisesRegex(ValueError, error_msg):
                    task_models.AutopkgtestData.parse_obj(
                        dict(timeout={field: -1}, **common_kwargs)
                    )


class ExtraRepositoryTests(TestCase):
    """Tests for ExtraRepository."""

    def test_valid_sources(self) -> None:
        """Accept a typical valid source."""
        task_models.ExtraRepository.parse_obj(
            {
                "url": "http://deb.debian.org/debian",
                "suite": "bookworm",
                "components": ["main", "non-free-firmware"],
            }
        )

    def test_source_with_key(self) -> None:
        """Accept a valid flat source with a key."""
        task_models.ExtraRepository.parse_obj(
            {
                "url": "http://example.com/repo",
                "suite": "./",
                "signing_key": "PUBLIC KEY",
            }
        )

    def test_flat_source_with_empty_components_list(self) -> None:
        """Rewrite an empty components list to None."""
        repo = task_models.ExtraRepository.parse_obj(
            {
                "url": "http://example.com/repo",
                "suite": "./",
                "components": [],
            }
        )
        self.assertIsNone(repo.components)

    def test_file_url(self) -> None:
        """Reject a file:// URL."""
        with self.assertRaisesRegex(
            ValueError, "invalid or missing URL scheme"
        ):
            task_models.ExtraRepository.parse_obj(
                {
                    "url": "file:/etc/passwd",
                    "suite": "bookworm",
                    "components": ["main"],
                }
            )

    def test_crazy_suite(self) -> None:
        """Reject an unreasonable suite."""
        with self.assertRaisesRegex(ValueError, "Invalid suite b@d_suite"):
            task_models.ExtraRepository.parse_obj(
                {
                    "url": "http://example.com/",
                    "suite": "b@d_suite",
                    "components": ["main"],
                }
            )

    def test_flat_repository_with_component(self) -> None:
        """Reject an flat repository with a component."""
        with self.assertRaisesRegex(
            ValueError, "Components cannot be specified for a flat repository"
        ):
            task_models.ExtraRepository.parse_obj(
                {
                    "url": "http://example.com/",
                    "suite": "flat/",
                    "components": ["main"],
                }
            )

    def test_crazy_component(self) -> None:
        """Reject an unreasonable component."""
        with self.assertRaisesRegex(ValueError, "Invalid component foo/bar"):
            task_models.ExtraRepository.parse_obj(
                {
                    "url": "http://example.com/",
                    "suite": "bookworm",
                    "components": ["foo/bar"],
                }
            )

    def test_as_oneline_source_flat(self) -> None:
        """Render a flat sources.list entry to a string."""
        repo = task_models.ExtraRepository.parse_obj(
            {
                "url": "http://example.com/",
                "suite": "flat/",
            }
        )
        self.assertEqual(
            repo.as_oneline_source(), "deb http://example.com/ flat/"
        )

    def test_as_oneline_source_components(self) -> None:
        """Render a regular sources.list entry to a string."""
        repo = task_models.ExtraRepository.parse_obj(
            {
                "url": "http://example.com/",
                "suite": "bookworm",
                "components": ["foo", "bar"],
            }
        )
        self.assertEqual(
            repo.as_oneline_source(), "deb http://example.com/ bookworm foo bar"
        )

    def test_as_oneline_source_signed(self) -> None:
        """Render a signed-by sources.list entry to a string."""
        repo = task_models.ExtraRepository.parse_obj(
            {
                "url": "http://example.com/",
                "suite": "bookworm",
                "components": ["foo", "bar"],
                "signing_key": "PUBLIC KEY",
            }
        )

        with self.assertRaisesRegex(
            ValueError, r"No signed_by_filename specified"
        ):
            repo.as_oneline_source()

        self.assertEqual(
            repo.as_oneline_source(signed_by_filename="/signature.asc"),
            (
                "deb [signed-by=/signature.asc] http://example.com/ bookworm "
                "foo bar"
            ),
        )

    def test_as_deb822_source_flat(self) -> None:
        """Render a flat sources.list entry to a string."""
        repo = task_models.ExtraRepository.parse_obj(
            {
                "url": "http://example.com/",
                "suite": "flat/",
            }
        )
        self.assertEqual(
            repo.as_deb822_source(),
            textwrap.dedent(
                """\
                Types: deb
                URIs: http://example.com/
                Suites: flat/
                """
            ),
        )

    def test_as_deb822_source_components(self) -> None:
        """Render a regular sources.list entry to a string."""
        repo = task_models.ExtraRepository.parse_obj(
            {
                "url": "http://example.com/",
                "suite": "bookworm",
                "components": ["foo", "bar"],
            }
        )
        self.assertEqual(
            repo.as_deb822_source(),
            textwrap.dedent(
                """\
                Types: deb
                URIs: http://example.com/
                Suites: bookworm
                Components: foo bar
                """
            ),
        )

    def test_as_deb822_source_signed(self) -> None:
        """Render a signed-by sources.list entry to a string."""
        repo = task_models.ExtraRepository.parse_obj(
            {
                "url": "http://example.com/",
                "suite": "bookworm",
                "components": ["foo", "bar"],
                "signing_key": "\n".join(
                    (
                        "-----BEGIN PGP PUBLIC KEY BLOCK-----",
                        "",
                        "ABCDEFGHI",
                        "-----END PGP PUBLIC KEY BLOCK-----",
                    )
                ),
            }
        )
        self.assertEqual(
            repo.as_deb822_source(),
            textwrap.dedent(
                """\
                Types: deb
                URIs: http://example.com/
                Suites: bookworm
                Components: foo bar
                Signed-By:
                 -----BEGIN PGP PUBLIC KEY BLOCK-----
                 .
                 ABCDEFGHI
                 -----END PGP PUBLIC KEY BLOCK-----
                """
            ),
        )
        self.assertEqual(
            repo.as_deb822_source(signed_by_filename="/signature.asc"),
            textwrap.dedent(
                """\
                Types: deb
                URIs: http://example.com/
                Suites: bookworm
                Components: foo bar
                Signed-By: /signature.asc
                """
            ),
        )


class SbuildDynamicDataTests(TestCase):
    """Tests for SbuildDynamicData."""

    def test_binnmu_maintainer_not_strict(self) -> None:
        """binnmu_maintainer does not have to be a deliverable email address."""
        task_models.SbuildDynamicData(
            input_source_artifact_id=1,
            binnmu_maintainer="Debusine <noreply@debusine-dev>",
        )


class ImageCacheUsageLogEntryTests(TestCase):
    """Tests for ImageCacheUsageLogEntry."""

    def test_timestamp_validation_succeeds(self) -> None:
        """Test ImageCacheUsageLogEntry accepts aware timestamp."""
        task_models.ImageCacheUsageLogEntry(
            filename="foo", timestamp=datetime.now(timezone.utc)
        )

    def test_timestamp_validation_fails(self) -> None:
        """Test ImageCacheUsageLogEntry rejects naive timestamp."""
        with self.assertRaisesRegex(ValueError, "timestamp is TZ-naive"):
            task_models.ImageCacheUsageLogEntry(
                filename="foo", timestamp=datetime.now()
            )


class ImageCacheUsageLogTests(TestCase):
    """Tests for ImageCacheUsageLog."""

    def test_version_validation_succeeds(self) -> None:
        """Test ImageCacheUsageLog accepts version 1."""
        task_models.ImageCacheUsageLog(version=1)

    def test_version_validation_fails(self) -> None:
        """Test ImageCacheUsageLog rejects version 99."""
        with self.assertRaisesRegex(ValueError, "Unknown usage log version 99"):
            task_models.ImageCacheUsageLog(version=99)


class ExtractForSigningDataTests(TestCase):
    """Tests for ExtractForSigningData."""

    def test_backend_validation_succeeds(self) -> None:
        """Test ExtractForSigningData accepts no backend or backend=auto."""
        task_models.ExtractForSigningData.parse_obj(
            {
                "environment": 1,
                "input": {"template_artifact": 2, "binary_artifacts": [3]},
            }
        )
        task_models.ExtractForSigningData.parse_obj(
            {
                "environment": 1,
                "backend": task_models.BackendType.AUTO,
                "input": {"template_artifact": 2, "binary_artifacts": [3]},
            }
        )

    def test_backend_validation_fails(self) -> None:
        """Test ExtractForSigningData rejects backends other than auto."""
        with self.assertRaisesRegex(
            ValueError,
            'ExtractForSigning only accepts backend "auto", not "qemu"',
        ):
            task_models.ExtractForSigningData.parse_obj(
                {
                    "environment": 1,
                    "backend": task_models.BackendType.QEMU,
                    "input": {"template_artifact": 2, "binary_artifacts": [3]},
                }
            )


class AssembleSignedSourceDataTests(TestCase):
    """Tests for AssembleSignedSourceData."""

    def test_backend_validation_succeeds(self) -> None:
        """Test AssembleSignedSourceData accepts no backend or backend=auto."""
        task_models.AssembleSignedSourceData.parse_obj(
            {
                "environment": 1,
                "template": 2,
                "signed": [3],
            }
        )
        task_models.AssembleSignedSourceData.parse_obj(
            {
                "environment": 1,
                "backend": task_models.BackendType.AUTO,
                "template": 2,
                "signed": [3],
            }
        )

    def test_backend_validation_fails(self) -> None:
        """Test AssembleSignedSourceData rejects backends other than auto."""
        with self.assertRaisesRegex(
            ValueError,
            'AssembleSignedSource only accepts backend "auto", not "qemu"',
        ):
            task_models.AssembleSignedSourceData.parse_obj(
                {
                    "environment": 1,
                    "backend": task_models.BackendType.QEMU,
                    "template": 2,
                    "signed": [3],
                }
            )


class EventReactionsTests(TestCase):
    """Tests for EventReactions."""

    def test_deserialize(self) -> None:
        """Test basic deserialization."""
        task_models.EventReactions.parse_raw(
            """
            {
                "on_success": [
                    {
                        "action": "update-collection-with-artifacts",
                        "collection": "internal@collections",
                        "artifact_filters": {
                            "category": "debian:binary-package",
                            "data__deb_fields__Section": "python"
                        },
                        "name_template": "{package}_{version}",
                        "variables": {
                            "$package": "deb_fields.Package",
                            "$version": "deb_fields.Version"
                        }
                    }
                ],
                "on_failure": [
                    {
                        "action": "send-notification",
                        "channel": "admin-team",
                        "data": {
                            "cc": [ "qa-team@example.org " ],
                            "subject": "Work request ${work_request_id}"
                        }
                    },
                    {
                        "action": "send-notification",
                        "channel": "security-team"
                    }
                ]
            }
        """
        )
