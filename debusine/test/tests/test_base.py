# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for debusine.tests.base.TestCase."""

import contextlib
import io
import logging
import os
import re
from configparser import ConfigParser
from datetime import datetime, timezone
from unittest import mock

import lxml
import requests
import responses

from debusine.test.base import TestCase


class TestCaseTests(TestCase):
    """Tests for methods in debusine.test.base.TestCase."""

    def test_create_temp_config_directory(self) -> None:
        """create_temp_config_directory write the configuration."""
        config = {
            'General': {'default-server': 'debian'},
            'server:debian': {
                'url': 'https://debusine.debian.org',
                'token': 'token-for-debian',
            },
        }
        directory = self.create_temp_config_directory(config)

        actual_config = ConfigParser()
        actual_config.read(os.path.join(directory, 'config.ini'))

        expected_config = ConfigParser()
        expected_config.read_dict(config)

        self.assertEqual(actual_config, expected_config)

    def test_assert_dict_contains_subset_raises_exception(self) -> None:
        """Raise an exception (subset not in dictionary)."""
        expected_message = "{'b': 1} does not contain the subset {'a': 1}"
        with self.assertRaisesRegex(self.failureException, expected_message):
            self.assertDictContainsAll({'b': 1}, {'a': 1})

    def test_assert_dict_use_error_msg(self) -> None:
        """Raise exception using a specific error message."""
        expected_message = 'Missing values'

        with self.assertRaisesRegex(self.failureException, expected_message):
            self.assertDictContainsAll({}, {'a': 1}, expected_message)

    def test_assert_dict_contains_subset_does_not_raise_exception(self) -> None:
        """Do not raise any exception (subset in dictionary)."""
        self.assertDictContainsAll({'a': 1, 'b': 2}, {'a': 1})

    def test_assert_dict_contains_subset_arg1_not_a_dictionary(self) -> None:
        """Raise exception because of wrong type argument 1."""
        expected_message = (
            "'a' is not an instance of <class 'dict'> : "
            "First argument is not a dictionary"
        )
        with self.assertRaisesRegex(self.failureException, expected_message):
            self.assertDictContainsAll('a', {})  # type: ignore[arg-type]

    def test_assert_dict_contains_subset_arg2_not_a_dictionary(self) -> None:
        """Raise exception because of wrong type argument 2."""
        expected_message = (
            "'b' is not an instance of <class 'dict'> : "
            "Second argument is not a dictionary"
        )
        with self.assertRaisesRegex(self.failureException, expected_message):
            self.assertDictContainsAll({}, 'b')  # type: ignore[arg-type]

    def test_assert_raises_system_exit_no_system_exit(self) -> None:
        """Raise self.failureException because missing SystemExit."""
        expected_message = (
            r'SystemExit not raised : Did not raise '
            r'SystemExit with exit_code=\^3\$'
        )
        with self.assertRaisesRegex(self.failureException, expected_message):
            with self.assertRaisesSystemExit(3):
                pass

    def test_assert_raises_system_exit_unexpected_exit_code(self) -> None:
        """Raise self.failureException because wrong exit_code in SystemExit."""
        expected_message = (
            r'\^3\$" does not match "7" : Did not raise '
            r'SystemExit with exit_code=\^3\$'
        )
        with self.assertRaisesRegex(self.failureException, expected_message):
            with self.assertRaisesSystemExit(3):
                raise SystemExit(7)

    def test_assert_raises_system_exit_success(self) -> None:
        """Do not raise self.failureException: expected SystemExit is raised."""
        with self.assertRaisesSystemExit(3):
            raise SystemExit(3)

    @responses.activate
    def test_assert_token_key_included_in_all_requests(self) -> None:
        """Do not raise any exception (all requests had the token key)."""
        responses.add(
            responses.GET,
            'https://example.net/something',
        )
        responses.add(
            responses.GET,
            'https://example.net/something',
        )

        token_key = 'some-key'

        requests.get(
            'https://example.net/something',
            headers={'Token': token_key},
        )

        self.assert_token_key_included_in_all_requests(token_key)

    @responses.activate
    def test_assert_token_key_included_in_all_requests_raise_missing(
        self,
    ) -> None:
        """Raise exception because token not included in the request."""
        responses.add(
            responses.GET,
            'https://example.net/something',
        )

        requests.get('https://example.net/something')

        expected_message = (
            "Token missing in the headers for the request "
            "'https://example.net/something'"
        )

        with self.assertRaisesRegex(self.failureException, expected_message):
            self.assert_token_key_included_in_all_requests('some-token')

    @responses.activate
    def test_assert_token_key_included_in_all_requests_raise_mismatch(
        self,
    ) -> None:
        """Raise exception because token mismatch included in the request."""
        responses.add(
            responses.GET,
            'https://example.net/something',
        )

        token = 'token-for-server'

        requests.get(
            'https://example.net/something',
            headers={'Token': 'some-invalid-token'},
        )

        expected_message = (
            "Unexpected token. In the request: "
            "'https://example.net/something' "
            "Actual: 'some-invalid-token' Expected: 'token-for-server'"
        )

        with self.assertRaisesRegex(self.failureException, expected_message):
            self.assert_token_key_included_in_all_requests(token)

    def test_assertLogsContains_log_found(self) -> None:
        """assertLogsContains() does not raise self.failureException."""
        with self.assertLogsContains('required-log') as logs:
            logging.warning('required-log')

        self.assertEqual(logs.output, ["WARNING:root:required-log"])

    def test_assertLogsContains_log_expected_count_wrong(self) -> None:
        """assertLogsContains() raise self.failureException (wrong count)."""
        expected_message = (
            '^Expected: "required-log"\n'
            'Actual: "WARNING:root:required-log"\n'
            'Expected msg found 1 times, expected 2 times$'
        )

        with self.assertRaisesRegex(self.failureException, expected_message):
            with self.assertLogsContains('required-log', expected_count=2):
                logging.warning('required-log')

    def test_assertLogsContains_log_expected_not_found(self) -> None:
        """assertLogsContains() raise self.failureException (wrong count)."""
        expected_message = (
            '^Expected: "required-log"\n'
            'Actual: "WARNING:root:some-log"\n'
            'Expected msg found 0 times, expected 1 times$'
        )

        with self.assertRaisesRegex(self.failureException, expected_message):
            with self.assertLogsContains('required-log'):
                logging.warning('some-log')

    def test_assertLogsContains_log_expected_not_found_wrong_level(
        self,
    ) -> None:
        """assertLogsContains() raise self.failureException (wrong level)."""
        expected_message = (
            'no logs of level WARNING or higher triggered on root'
        )

        with self.assertRaisesRegex(self.failureException, expected_message):
            with self.assertLogsContains('required-log', level=logging.WARNING):
                logging.debug('some-log')

    def test_assertLogsContains_log_not_found_in_raise_exception(self) -> None:
        """
        assertLogsContains() raise self.failureException.

        It handles raised exceptions in the context code.
        """
        expected_message = (
            '^Expected: "The wanted message"\n'
            'Actual: "WARNING:root:Unrelated message"\n'
            'Expected msg found 0 times, expected 1 times$'
        )

        with self.assertRaisesRegex(self.failureException, expected_message):
            with (
                self.assertRaisesRegex(SystemExit, '3'),
                self.assertLogsContains('The wanted message'),
            ):
                logging.warning('Unrelated message')
                raise SystemExit(3)

    def test_dump_html(self) -> None:
        """Test dumping HTML to stdout."""
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.dump_html("<test>")
        self.assertEqual(stdout.getvalue().splitlines(), ["001 <test>"])

    def test_dump_html_with_title(self) -> None:
        """Test dumping HTML to stdout, with title."""
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.dump_html("<test>", title="title")
        self.assertEqual(
            stdout.getvalue().splitlines(), ["", "    title", "", "001 <test>"]
        )

    def test_dump_element(self) -> None:
        """Test dumping an element to stdout."""
        content = b"<!DOCTYPE html><html></html>"
        tree = self.assertHTMLValid(content)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.dump_element(tree, title="title")
            stdout.getvalue().splitlines(), ["", "    title", "", "001 <html/>"]

    def test_dump_element_with_title(self) -> None:
        """Test dumping HTML to stdout, with title."""
        content = b"<!DOCTYPE html><html></html>"
        tree = self.assertHTMLValid(content)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.dump_element(tree)
        self.assertEqual(stdout.getvalue().splitlines(), ["001 <html/>"])

    def test_invalid_html(self) -> None:
        """Test warnings on invalid HTML."""
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            content = b"<!DOCTYPE html><html><does-not-exist></html>"
            with self.assertRaisesRegex(
                AssertionError, r"1:HTML_UNKNOWN_TAG:Tag does-not-exist invalid"
            ):
                self.assertHTMLValid(content)

            content = b"<!DOCTYPE html><html"
            with self.assertRaisesRegex(
                AssertionError,
                r"1:ERR_GT_REQUIRED:Couldn't find end of Start Tag html",
            ):
                self.assertHTMLValid(content)

        self.assertEqual(stdout.getvalue(), "")

    def test_invalid_html_dump_on_error(self) -> None:
        """Test warnings on invalid HTML with dump_on_error."""
        content = b"<!DOCTYPE html><html>\n<does-not-exist>\n</html>"

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with self.assertRaisesRegex(
                AssertionError, r"2:HTML_UNKNOWN_TAG:Tag does-not-exist invalid"
            ):
                self.assertHTMLValid(content, dump_on_error=True)

        lines = stdout.getvalue().splitlines()
        self.assertEqual(lines[1], "    HTML parser errors")
        self.assertEqual(lines[3], "001 <!DOCTYPE html><html>")
        self.assertEqual(lines[4], "002 <does-not-exist>")
        self.assertEqual(
            lines[5], "  → HTML_UNKNOWN_TAG:Tag does-not-exist invalid"
        )

    def test_assert_has_element(self) -> None:
        """Test that assertHasElement works as expected."""
        tree = lxml.objectify.fromstring("<p><b>this</b> is a <b>test</b></p>")

        p = self.assertHasElement(tree, "//p")
        self.assertTextContentEqual(p, "this is a test")

        with self.assertRaisesRegex(AssertionError, "'//i' not found in tree"):
            self.assertHasElement(tree, "//i")
        with self.assertRaisesRegex(
            AssertionError, "'//b' matched 2 elements instead of one"
        ):
            self.assertHasElement(tree, "//b")

    def test_assert_has_element_dump_on_error(self) -> None:
        """Test assertHasElement with dump_on_error."""
        tree = lxml.objectify.fromstring("<p><b>this</b> is a <b>test</b></p>")

        for xpath, message in (
            ("//i", "'//i' not found in tree"),
            ("//b", "'//b' matched 2 elements instead of one"),
        ):
            with self.subTest(xpath=xpath):
                with mock.patch(
                    "debusine.test.base.TestCase.dump_element"
                ) as dump:
                    with self.assertRaisesRegex(
                        AssertionError, re.escape(message)
                    ):
                        self.assertHasElement(tree, xpath, dump_on_error=True)
                dump.assert_called_with(tree, title=message)

    def test_assertElementHasClass(self) -> None:
        """Test assertElementHasClass."""
        for html, message in (
            ("<a></a>", "Element does not have a class attribute"),
            ("<a class=''></a>", "'foo' not found in []"),
            ("<a class='bar'></a>", "'foo' not found in ['bar']"),
            ("<a class='foo'></a>", None),
            ("<a class='foo bar'></a>", None),
            ("<a class='bar foo'></a>", None),
        ):
            with self.subTest(html=html):
                tree = self.assertHTMLValid(html)
                el = self.assertHasElement(tree, "body/a")
                if message is None:
                    self.assertElementHasClass(el, "foo")
                else:
                    with self.assertRaisesRegex(
                        AssertionError, re.escape(message)
                    ):
                        self.assertElementHasClass(el, "foo")

    def test_assertElementHasNoClass(self) -> None:
        """Test assertElementHasClass."""
        for html, message in (
            ("<a></a>", None),
            ("<a class=''></a>", None),
            ("<a class='bar'></a>", None),
            ("<a class='foo'></a>", "'foo' unexpectedly found in ['foo']"),
            (
                "<a class='bar foo'></a>",
                "'foo' unexpectedly found in ['bar', 'foo']",
            ),
            (
                "<a class='bar foo bar'></a>",
                "'foo' unexpectedly found in ['bar', 'foo', 'bar']",
            ),
        ):
            with self.subTest(html=html):
                tree = self.assertHTMLValid(html)
                el = self.assertHasElement(tree, "body/a")
                if message is None:
                    self.assertElementHasNoClass(el, "foo")
                else:
                    with self.assertRaisesRegex(
                        AssertionError, re.escape(message)
                    ):
                        self.assertElementHasNoClass(el, "foo")

    def test_assertDatetimeContentEqual(self) -> None:
        """Test :py:meth:`assertDatetimeContentEqual`."""
        tree = self.assertHTMLValid(
            "<p>created on <b>2025-01-01 12:30</b> at lunchtime</p>"
        )
        self.assertDatetimeContentEqual(
            tree.xpath("//b")[0],
            datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc),
        )

    def test_assertDatetimeContentEqual_anywhere(self) -> None:
        """Test :py:meth:`assertDatetimeContentEqual`."""
        tree = self.assertHTMLValid(
            "<p>created on <b>2025-01-01 12:30</b> at lunchtime</p>"
        )
        self.assertDatetimeContentEqual(
            tree.xpath("//p")[0],
            datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc),
            anywhere=True,
        )

    def test_assertDatetimeContentEqualNoDatetime(self) -> None:
        """Test :py:meth:`assertDatetimeContentEqual` with no datetime."""
        tree = self.assertHTMLValid("<ul><li>none</li></ul>")
        with self.assertRaisesRegex(
            self.failureException,
            r"text content 'none' does not contain"
            r" a YYYY-MM-DD HH:MM datetime",
        ):
            self.assertDatetimeContentEqual(
                tree.xpath("//ul")[0],
                datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc),
            )

    def test_assertDatetimeContentEqualDifferentDatetime(self) -> None:
        """Test :py:meth:`assertDatetimeContentEqual` with another datetime."""
        tree = self.assertHTMLValid("<ul><li>2025-01-01 12:31</li></ul>")
        with self.assertRaisesRegex(
            self.failureException,
            r"datetime.datetime\(2025, 1, 1, 12, 31, tzinfo=\S+\)"
            r" != datetime.datetime\(2025, 1, 1, 12, 30, tzinfo=\S+\)",
        ):
            self.assertDatetimeContentEqual(
                tree.xpath("//ul")[0],
                datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc),
            )

    def test_assertDatetimeContentEqualIgnoreSeconds(self) -> None:
        """:py:meth:`assertDatetimeContentEqual` ignores seconds."""
        tree = self.assertHTMLValid("<ul><li>2025-01-01 12:30</li></ul>")
        for expected in (
            datetime(2025, 1, 1, 12, 30, 0, tzinfo=timezone.utc),
            datetime(2025, 1, 1, 12, 30, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 1, 12, 30, 1, 12345, tzinfo=timezone.utc),
            datetime(2025, 1, 1, 12, 30, 0, 12345, tzinfo=timezone.utc),
        ):
            with self.subTest(expected=expected):
                self.assertDatetimeContentEqual(tree.xpath("//ul")[0], expected)

    def test_assertYAMLContentEqual(self) -> None:
        """Test :py:meth:`assertYAMLContentEqual`."""
        from debusine.web.views.view_utils import format_yaml

        for data in (
            None,
            42,
            "test",
            [],
            {},
            [1, 2, 3],
            ["one", "two", "three"],
            {1: "one", "two": 2},
        ):
            with self.subTest(data=data):
                html = format_yaml(data)
                tree = self.assertHTMLValid(html)
                self.assertYAMLContentEqual(tree, data)

    def test_assertYAMLContentEqualBadYAML(self) -> None:
        """Test :py:meth:`assertYAMLContentEqual` with bad YAML."""
        for text in (
            "[",
            "{",
        ):
            with self.subTest(text=text):
                tree = self.assertHTMLValid(f"<pre>{text}</pre>")
                with self.assertRaisesRegex(
                    self.failureException,
                    r"node text .+ does not parse as YAML",
                ):
                    self.assertYAMLContentEqual(tree, {})
