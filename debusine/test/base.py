# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Debusine extensions to Python's standard unittest.TestCase."""

import contextlib
import hashlib
import inspect
import io
import os
import re
import shutil
import tempfile
import textwrap
import unittest
from collections import defaultdict
from collections.abc import Generator
from configparser import ConfigParser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, TYPE_CHECKING, cast
from unittest import mock
from unittest.util import safe_repr

import debian.deb822 as deb822
import lxml
import lxml.etree
import lxml.objectify
import responses
import yaml

from debusine.artifacts.local_artifact import deb822dict_to_dict
from debusine.artifacts.models import DebianSourcePackage
from debusine.artifacts.playground import ArtifactPlayground

if TYPE_CHECKING:
    from unittest._log import _LoggingWatcher


class TestCase(unittest.TestCase):
    """
    Collection of methods to help write unit tests.

    This augments unittest.TestCase with assert statements and factory
    functions to support Debusine unit tests.

    This is intended to be used as the TestCase for tests that do not depend on
    Django code.
    """

    _initial_cls_precious_globals: ClassVar[dict[str, Any]]
    _initial_self_precious_globals: dict[str, Any]

    # The unittest defaults mostly just get in the way of debugging.
    maxDiff = None

    @classmethod
    def get_precious_globals(cls) -> dict[str, Any]:
        """Collect global values that tests should not change."""
        return {"cwd": os.getcwd()}

    @classmethod
    def setUpClass(cls) -> None:
        """Save the values of globals that should not be changed by tests."""
        cls._initial_cls_precious_globals = cls.get_precious_globals()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        """Check that saved globals haven't been changed by tests."""
        super().tearDownClass()
        if not hasattr(cls, "_initial_cls_precious_globals"):
            raise AssertionError(
                f"{inspect.getfile(cls)}:"
                f"{cls.__qualname__}.setUpClass"
                " did not call super().setUpClass"
            )
        if cls._initial_cls_precious_globals != cls.get_precious_globals():
            raise AssertionError(
                f"{inspect.getfile(cls)}:"
                f"{cls.__qualname__}:"
                f" {cls._initial_cls_precious_globals}"
                f" != {cls.get_precious_globals()}"
            )

    def setUp(self) -> None:
        """Save the values of globals that should not be changed by tests."""
        self._initial_self_precious_globals = self.get_precious_globals()
        super().setUp()

    def tearDown(self) -> None:
        """Check that saved globals haven't been changed by tests."""
        super().tearDown()
        initial = getattr(self, "_initial_self_precious_globals", None)
        self.assertIsNotNone(initial, "setUp did not call super().setUp")
        self.assertEqual(
            self._initial_self_precious_globals,
            self.get_precious_globals(),
        )

    def create_temp_config_directory(self, config: dict[str, Any]) -> str:
        """
        Create a temp directory with a config.ini file inside.

        The method also register the automatic removal of said directory.
        """
        temp_directory = tempfile.mkdtemp()
        config_file_name = os.path.join(temp_directory, 'config.ini')
        with open(config_file_name, 'w') as config_file:
            config_writer = ConfigParser()

            for section, values in config.items():
                config_writer[section] = values

            config_writer.write(config_file)

        self.addCleanup(shutil.rmtree, temp_directory)

        return temp_directory

    def assertDictContainsAll(
        self,
        dictionary: dict[Any, Any],
        subset: dict[Any, Any],
        msg: Any = None,
    ) -> None:
        """
        Implement a replacement of deprecated TestCase.assertDictContainsSubset.

        Assert that the keys and values of subset is in dictionary.

        The order of the arguments in TestCase.assertDictContainsSubset
        and this implementation differs.
        """
        self.assertIsInstance(
            dictionary, dict, 'First argument is not a dictionary'
        )
        self.assertIsInstance(
            subset, dict, 'Second argument is not a dictionary'
        )

        if dictionary != dictionary | subset:
            msg = self._formatMessage(
                msg,
                '%s does not contain the subset %s'
                % (safe_repr(dictionary), safe_repr(subset)),
            )

            raise self.failureException(msg)

    def assert_token_key_included_in_all_requests(
        self, expected_token: str
    ) -> None:
        """Assert that the requests in responses.calls had the Token."""
        for call in responses.calls:
            headers = call.request.headers

            if 'Token' not in headers:
                raise self.failureException(
                    'Token missing in the headers for '
                    'the request %s' % (safe_repr(call.request.url))
                )

            if (actual_token := headers['Token']) != expected_token:
                raise self.failureException(
                    'Unexpected token. In the request: %s Actual: %s '
                    'Expected: %s'
                    % (
                        safe_repr(call.request.url),
                        safe_repr(actual_token),
                        safe_repr(expected_token),
                    )
                )

    def create_temporary_file(
        self,
        *,
        prefix: str | None = None,
        suffix: str | None = None,
        contents: bytes | None = None,
        directory: Path | str | None = None,
    ) -> Path:
        """
        Create a temporary file and schedules the deletion via self.addCleanup.

        :param prefix: prefix is "debusine-tests-" + prefix or "debusine-tests-"
        :param suffix: suffix for the created file
        :param contents: contents is written into the file. If it's none the
          file is left empty
        :param directory: the directory that the file is created into.
        """
        if prefix is None:
            prefix = "debusine-tests-"
        else:
            prefix = "debusine-tests-" + prefix

        suffix = suffix or ""

        file = tempfile.NamedTemporaryFile(
            prefix=f"{prefix}-", suffix=suffix, delete=False, dir=directory
        )

        if contents is not None:
            file.write(contents)
            file.close()

        file.close()
        file_path = Path(file.name)

        self.addCleanup(file_path.unlink, missing_ok=True)

        return file_path

    def create_temporary_directory(
        self, *, directory: Path | str | None = None
    ) -> Path:
        """
        Create and return a temporary directory. Schedules deletion.

        :param directory: directory to create the temporary directory. If None,
          use the default for tempfile.TemporaryDirectory.
        """
        temp_dir = tempfile.TemporaryDirectory(
            prefix="debusine-tests-", dir=directory
        )

        self.addCleanup(temp_dir.cleanup)

        return Path(temp_dir.name)

    @contextlib.contextmanager
    def assertRaisesSystemExit(
        self, exit_code: int
    ) -> Generator[None, None, None]:
        """Assert that raises SystemExit with the specific exit_code."""
        with self.assertRaisesRegex(
            SystemExit,
            rf'^{exit_code}$',
            msg=f'Did not raise SystemExit with exit_code=^{exit_code}$',
        ):
            yield

    @contextlib.contextmanager
    def assertLogsContains(
        self, message: str, expected_count: int = 1, **assert_logs_kwargs: Any
    ) -> "Generator[_LoggingWatcher, None, None]":
        """
        Raise failureException if message is not in the logs.

        Yields the same context manager as self.assertLogs(). This allows
        further checks in the logs.

        :param message: message to find in the logs
        :param expected_count: expected times that the message
          must be in the logs
        :param assert_logs_kwargs: arguments for self.assertLogs()
        """

        def failure_exception_if_needed(
            logs: "_LoggingWatcher", message: str, expected_count: int
        ) -> None:
            all_logs = '\n'.join(logs.output)

            actual_times = all_logs.count(message)

            if actual_times != expected_count:
                raise self.failureException(
                    'Expected: "%s"\n'
                    'Actual: "%s"\n'
                    'Expected msg found %s times, expected %s times'
                    % (message, all_logs, actual_times, expected_count)
                )

        with self.assertLogs(**assert_logs_kwargs) as logs:
            try:
                yield logs
            except BaseException as exc:
                failure_exception_if_needed(logs, message, expected_count)
                raise exc

        failure_exception_if_needed(logs, message, expected_count)

    @staticmethod
    def write_dsc_example_file(path: Path) -> dict[str, Any]:
        """Write a DSC file into file. Files in .dsc are not created."""
        metadata = {
            "source": "hello",
            "version": "2.10-2",
        }
        text = textwrap.dedent(
            f"""\
            -----BEGIN PGP SIGNED MESSAGE-----
            Hash: SHA256

            Format: 3.0 (quilt)
            Source: {metadata['source']}
            Binary: hello
            Architecture: any
            Version: {metadata['version']}
            Maintainer: Santiago Vila <sanvila@debian.org>
            Homepage: http://www.gnu.org/software/hello/
            Standards-Version: 4.3.0
            Build-Depends: debhelper-compat (= 9)
            Package-List:
             hello deb devel optional arch=any
            Checksums-Sha1:
             f7bebf6f9c62a2295e889f66e05ce9bfaed9ace3 725946 hello_2.10.orig.tar.gz
             a35d97bd364670b045cdd86d446e71b171e915cc 6132 hello_2.10-2.debian.tar.xz
            Checksums-Sha256:
             31e066137a962676e89f69d1b65382de95a7ef7d914b8cb956f41ea72e0f516b 725946 hello_2.10.orig.tar.gz
             811ad0255495279fc98dc75f4460da1722f5c1030740cb52638cb80d0fdb24f0 6132 hello_2.10-2.debian.tar.xz
            Files:
             6cd0ffea3884a4e79330338dcc2987d6 725946 hello_2.10.orig.tar.gz
             e522e61c27eb0401c86321b9d8e137ae 6132 hello_2.10-2.debian.tar.xz

            -----BEGIN PGP SIGNATURE-----
            """  # noqa: E501
        )
        path.write_text(text)

        dsc = deb822.Dsc(text.encode("utf-8"))

        return deb822dict_to_dict(dsc)

    @classmethod
    def write_dsc_file(
        cls, path: Path, files: list[Path], version: str = "2.10-5"
    ) -> dict[str, Any]:
        """Write a debian .dsc file."""
        return ArtifactPlayground.write_deb822_file(
            deb822.Dsc, path, files, version=version
        )

    def write_deb_file(
        self,
        path: Path,
        *,
        source_name: str | None = None,
        source_version: str | None = None,
        control_file_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Write a debian control file."""
        return ArtifactPlayground.write_deb_file(
            path,
            source_name=source_name,
            source_version=source_version,
            control_file_names=control_file_names,
        )

    @classmethod
    def write_changes_file(
        cls,
        path: Path,
        files: list[Path],
        binaries: list[str] | None = None,
        version: str = "2.10-5",
        binnmu: bool = False,
    ) -> dict[str, Any]:
        """Write a debian .changes file."""
        return ArtifactPlayground.write_deb822_file(
            deb822.Changes,
            path,
            files,
            binaries=binaries,
            version=version,
            binnmu=binnmu,
        )

    def mock_is_command_available(self, commands: dict[str, bool]) -> None:
        """
        Configure a fake is_command_available.

        It responds by looking up the requested command in the given
        `commands` dictionary.
        """
        patcher = mock.patch(
            "debusine.utils.is_command_available",
            side_effect=lambda cmd: commands.get(cmd, False),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_source_artifact_equal(
        self,
        source: DebianSourcePackage,
        name: str = "hello",
        version: str = "1.0-1",
    ) -> None:
        """
        Verify the contents of a source package artifact.

        File contents are assumed to have been autogenerated using playground
        defaults.
        """
        files: list[str]
        if "-" in version:
            files = [
                f"{name}_{version}.debian.tar.xz",
                f"{name}_{version.split('-')[0]}.orig.tar.gz",
            ]
        else:
            files = [
                f"{name}_{version}.tar.gz",
            ]
        self.assertEqual(source.name, name)
        self.assertEqual(source.version, version)
        self.assertEqual(source.type, "dpkg")

        def csum(name: str, text: str) -> str:
            hasher = hashlib.new(name)
            hasher.update(text.encode())
            return hasher.hexdigest()

        checksums_sha1: list[dict[str, str]] = [
            {"name": name, "sha1": csum("sha1", name), "size": str(len(name))}
            for name in files
        ]
        checksums_sha256: list[dict[str, str]] = [
            {
                "name": name,
                "sha256": csum("sha256", name),
                "size": str(len(name)),
            }
            for name in files
        ]
        checksums_md5: list[dict[str, str]] = [
            {"name": name, "md5sum": csum("md5", name), "size": str(len(name))}
            for name in files
        ]
        package_list = source.dsc_fields.pop("Package-List")
        if isinstance(package_list, str):  # pragma: no cover
            # python-debian < 0.1.50
            self.assertEqual(
                package_list, f"\n {name} deb devel optional arch=any"
            )
        else:  # pragma: no cover
            # python-debian >= 0.1.50
            self.assertEqual(
                package_list,
                [
                    {
                        "package": name,
                        "package-type": "deb",
                        "section": "devel",
                        "priority": "optional",
                        "_other": "arch=any",
                    }
                ],
            )
        self.assertEqual(
            source.dsc_fields,
            {
                'Architecture': 'any',
                'Binary': 'hello',
                'Checksums-Sha1': checksums_sha1,
                'Checksums-Sha256': checksums_sha256,
                'Files': checksums_md5,
                'Format': '3.0 (quilt)',
                'Homepage': f'http://www.gnu.org/software/{name}/',
                'Maintainer': 'Example Maintainer <example@example.org>',
                'Source': name,
                'Standards-Version': '4.3.0',
                'Version': version,
            },
        )

    # LXML does not seem to know about HTML5 structural tags
    re_lxml_false_positive_tags = re.compile(
        r"Tag (?:nav|footer|header|article|details|summary) invalid"
    )

    def _filter_parser_error_log(
        self, error_log: lxml.etree._ListErrorLog
    ) -> list[str]:
        """Filter lxml parser error log for known false positives."""
        # _LogEntry documentation:
        # https://lxml.de/apidoc/lxml.etree.html#lxml.etree._LogEntry
        Domains = lxml.etree.ErrorDomains

        errors: list[str] = []
        for error in error_log:
            match (error.domain, error.type_name):
                # Nice to have if available, but libxml2 >= 2.14.0 no longer
                # provides unknown-tag checks at all so we can't reliably
                # cover this.
                case Domains.HTML, "HTML_UNKNOWN_TAG":  # pragma: no cover
                    if self.re_lxml_false_positive_tags.match(error.message):
                        continue

            errors.append(f"{error.line}:{error.type_name}:{error.message}")
        return errors

    def dump_html(
        self,
        content: str,
        *,
        title: str | None = None,
        parser: lxml.etree.HTMLParser | None = None,
    ) -> None:
        """
        Dump HTML on standard output.

        :param content: HTML string to dump
        :param title: optional title text to write before the dump
        :param parser: optional parser instance that parsed the HTML, to use to
                       annotate code with parser errors
        """
        from rich.console import Console
        from rich.syntax import Syntax

        console = Console()
        if title is not None:
            console.print("")
            console.print(
                f"    {title}",
                markup=False,
                highlight=False,
                style="bold bright_red",
            )
            console.print("")

        errors_by_line: dict[int, list[str]] | None = None
        if parser:
            errors_by_line = defaultdict(list)
            for e in parser.error_log:
                errors_by_line[e.line].append(f"{e.type_name}:{e.message}")
        else:
            errors_by_line = {}

        syntax = Syntax(content, "html", line_numbers=True)
        text = syntax.highlight(content)
        for lineno, line in enumerate(text.split(), start=1):
            console.print(
                f"{lineno:03d}", end=" ", style="bright_black", highlight=False
            )
            console.print(line)
            if line_errors := errors_by_line.get(lineno):
                for error in line_errors:
                    console.print(
                        f"  → {error}",
                        style="red",
                        markup=False,
                        highlight=False,
                    )

    def dump_element(
        self,
        element: lxml.objectify.ObjectifiedElement,
        *,
        title: str | None = None,
    ) -> None:
        """
        Dump a LXML element on standard output.

        :param element: element to dump
        :param title: optional title text to write before the dump
        """
        self.dump_html(lxml.etree.tostring(element).decode(), title=title)

    def assertHTMLValid(
        self,
        content: str | bytes,
        dump_on_error: bool = False,
        check_widget_errors: bool = True,
    ) -> lxml.objectify.ObjectifiedElement:
        """
        Parse the response contents as HTML and checks contents.

        Assert:
        - HTML parses without errors
        - No "debusine-widget-errors" in the contents

        Returns the parsed tree.
        """
        if isinstance(content, str):
            content = content.encode()
            parser = lxml.etree.HTMLParser(
                remove_blank_text=True, encoding="utf8"
            )
        else:
            parser = lxml.etree.HTMLParser(remove_blank_text=True)
        parser.set_element_class_lookup(
            lxml.objectify.ObjectifyElementClassLookup()
        )
        with io.BytesIO(content) as fd:
            tree = lxml.etree.parse(fd, parser)
        errors = self._filter_parser_error_log(parser.error_log)

        # Dump source with errors if requested
        if errors and dump_on_error:
            self.dump_html(
                content.decode(), parser=parser, title="HTML parser errors"
            )

        self.assertEqual(errors, [])

        if check_widget_errors:
            # Assert that no "debusine-widget-error" is displayed. It appears
            # if a widget has an error
            self.assertEqual(
                tree.xpath('//*[@data-role="debusine-widget-error"]'), []
            )

        return cast(lxml.objectify.ObjectifiedElement, tree.getroot())

    def assertHasElement(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        xpath: str,
        dump_on_error: bool = False,
    ) -> lxml.objectify.ObjectifiedElement:
        """
        Ensure that the tree has strictly one matching element.

        :param tree: tree to search
        :param xpath: XPath query to match
        :returns: resulting element
        """
        el = tree.xpath(xpath)
        if not el:
            message = f"{xpath!r} not found in tree"
            if dump_on_error:
                self.dump_element(tree, title=message)
            self.fail(message)
        if len(el) > 1:
            message = f"{xpath!r} matched {len(el)} elements instead of one"
            if dump_on_error:
                self.dump_element(tree, title=message)
            self.fail(message)
        assert isinstance(el[0], lxml.objectify.ObjectifiedElement)
        return el[0]

    def assertElementHasClass(
        self, element: lxml.objectify.ObjectifiedElement, name: str
    ) -> None:
        """Ensure the element has the given class in the DOM."""
        dom_class = element.get("class")
        if dom_class is None:
            self.fail("Element does not have a class attribute")
        self.assertIn(name, dom_class.split())

    def assertElementHasNoClass(
        self, element: lxml.objectify.ObjectifiedElement, name: str
    ) -> None:
        """Ensure the element does not have the given class in the DOM."""
        if (dom_class := element.get("class")) is None:
            return
        self.assertNotIn(name, dom_class.split())

    def get_node_text_normalized(
        self, node: lxml.objectify.ObjectifiedElement
    ) -> str:
        """Get the node text contents, with spaces normalized."""
        # itertext will iterate on whitespace-only blocks, so it needs two
        # passes: one to reconstruct the text, and one to normalise whitespace
        sample = "".join(node.itertext())
        return " ".join(sample.strip().split())

    def assertTextContentEqual(
        self,
        node: lxml.objectify.ObjectifiedElement,
        text: str,
        dump_on_error: bool = False,
    ) -> None:
        """
        Ensure that node.text matches the given text.

        Both expected and actual text are normalised so that consecutive
        whitespace and newlines become a single space, to simplify dealing with
        the way HTML collapses whitespace.
        """
        sample = self.get_node_text_normalized(node)
        text = " ".join(text.strip().split())
        if dump_on_error and sample != text:
            self.dump_element(node, title="Text content mismatch")
        self.assertEqual(sample, text)

    def assertDatetimeContentEqual(
        self,
        node: lxml.objectify.ObjectifiedElement,
        dt: datetime,
        anywhere: bool = False,
    ) -> None:
        """
        Ensure the row contains a datetime matching the given one.

        Text surrounding the datetime value is ignored.
        """
        text = self.get_node_text_normalized(node)
        regex = r"\d+-\d+-\d+ \d+:\d+"
        if not anywhere:
            regex = f"^{regex}$"

        if not (m := re.search(regex, text)):
            self.fail(
                f"text content {text!r} does not contain"
                " a YYYY-MM-DD HH:MM datetime"
            )
        actual = datetime.strptime(m.group(), "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc
        )
        expected = dt.replace(second=0, microsecond=0)
        self.assertEqual(actual, expected)

    def assertYAMLContentEqual(
        self, node: lxml.objectify.ObjectifiedElement, data: Any
    ) -> None:
        """Ensure that the node contents match yaml-formatted data."""
        encoded = "".join(node.itertext())
        try:
            actual = yaml.safe_load(encoded)
        except yaml.parser.ParserError as e:
            self.fail(f"node text {encoded!r} does not parse as YAML: {e}")
        self.assertEqual(actual, data)
