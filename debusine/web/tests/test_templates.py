# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""
Tests for generic templates or included templates.

The majority of template code is tested via test_views.py. Any other specific
code for templates is tested in this file.
"""
from django.core.handlers import exception
from django.template.loader import get_template
from django.test import TestCase
from django.urls import Resolver404

from debusine.db.models import WorkRequest


def _html_work_request_result(result: str) -> str:
    match result:
        case "success":
            text_bg = "text-bg-success"
            text = "Success"
        case "failure":
            text_bg = "text-bg-warning"
            text = "Failure"
        case "error":
            text_bg = "text-bg-danger"
            text = "Error"
        case "skipped":
            text_bg = "text-bg-light"
            text = "Skipped"
        case _ as unreachable:
            raise AssertionError(f"unsupported result {unreachable}")

    return f'<span class="badge {text_bg}">{text}</span>'


def _html_work_request_status(status: str) -> str:
    if status == "pending":
        text_bg = "text-bg-secondary"
        text = "Pending"
    elif status == "running":
        text_bg = "text-bg-secondary"
        text = "Running"
    elif status == "completed":
        text_bg = "text-bg-primary"
        text = "Completed"
    elif status == "aborted":
        text_bg = "text-bg-dark"
        text = "Aborted"
    elif status == "blocked":
        text_bg = "text-bg-dark"
        text = "Blocked"
    else:
        assert False

    return f'<span class="badge {text_bg}">{text}</span>'


class TemplateTests(TestCase):
    """Tests for the project level templates."""

    def test_404_render_exception(self) -> None:
        """404.html template renders the exception message."""
        template = get_template("404.html")
        exception_msg = "This is an exception message"
        context = {"exception": exception_msg}

        rendered_template = template.render(context)

        self.assertIn(exception_msg, rendered_template)

    def test_404_render_error(self) -> None:
        """404.html render error."""
        template = get_template("404.html")
        error = "This is an error message"
        context = {"error": error}

        self.assertIn(error, template.render(context))

    def test_404_no_error_no_useful_exception(self) -> None:
        """
        Template 404.html does not render exception.__class__.__name.__.

        In 404.html it's hardcoded as "Resolver404".
        """
        template = get_template("404.html")
        context = {"exception": Resolver404()}
        rendered_template = template.render(context)

        self.assertNotIn(str(exception.__class__.__name__), rendered_template)


class WorkRequestResultTests(TestCase):
    """Tests for _work_request-result.html."""

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.template = get_template("web/_work_request-result.html")

    def test_work_request_results(self) -> None:
        """Test rendering for possible results."""
        for result in ["success", "failure", "error", "skipped"]:
            with self.subTest(result=result):
                rendered_template = self.template.render({"result": result})
                self.assertHTMLEqual(
                    rendered_template, _html_work_request_result(result)
                )

    def test_work_request_other(self) -> None:
        """Test for result is empty."""
        context = {"result": ""}
        rendered_template = self.template.render(context)
        self.assertHTMLEqual(rendered_template, "")


class WorkRequestResultSmallTests(TestCase):
    """Tests for _work_request-result-small.html."""

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.template = get_template("web/_work_request-result-small.html")

    def test_work_request_result_small(self) -> None:
        """Test rendering for different results."""
        wr = WorkRequest.Results

        def _render(css_class: str, title: str, text: str) -> str:
            """Return HTML."""
            return (
                f'<span title="{title}" class="badge {css_class}">{text}</span>'
            )

        result_to_html = {
            wr.SUCCESS: _render("text-bg-success", "Success", "S"),
            wr.FAILURE: _render("text-bg-warning", "Failure", "F"),
            wr.ERROR: _render("text-bg-danger", "Error", "E"),
            wr.SKIPPED: _render("text-bg-light", "Skipped", "s"),
            "unknown-result": "unknown-result",
        }

        for result, rendered in result_to_html.items():
            with self.subTest(result=result):
                rendered_template = self.template.render({"result": result})

                self.assertHTMLEqual(rendered_template, rendered)


class WorkRequestStatusTests(TestCase):
    """Tests for _work_request-status.html."""

    def setUp(self) -> None:
        """Initialize test."""
        super().setUp()
        self.template = get_template("web/_work_request-status.html")

    def test_work_request_status(self) -> None:
        """Test rendering for different status."""
        for status, _ in WorkRequest.Statuses.choices:
            with self.subTest(status=status):
                rendered_template = self.template.render({"status": status})

                self.assertHTMLEqual(
                    rendered_template, _html_work_request_status(status)
                )

    def test_work_request_unexpected(self) -> None:
        """Test rendering function raise assertion."""
        with self.assertRaises(AssertionError):
            _html_work_request_status("")


class BadgeCountTests(TestCase):
    """Tests for _badge-count.html."""

    def setUp(self) -> None:
        """Set up tests."""
        super().setUp()
        self.template = get_template("web/_badge-count.html")

    def test_count_is_zero(self) -> None:
        """Test count is zero background is bg-light."""
        self.assertHTMLEqual(
            self.template.render(context={"count": 0, "title": "count of..."}),
            '<span class="badge text-bg-light" title="count of...">0</span>',
        )

    def test_count_is_not_zero(self) -> None:
        """Test count is zero background is as passed."""
        self.assertHTMLEqual(
            self.template.render(
                context={
                    "count": 11,
                    "title": "count of...",
                    "bg_class": "primary",
                }
            ),
            (
                '<span class="badge text-bg-primary" '
                'title="count of...">11</span>'
            ),
        )


class DictToHtmlTests(TestCase):
    """Tests for _dict-to-html.html."""

    def setUp(self) -> None:
        """Set up tests."""
        super().setUp()
        self.template = get_template("web/_dict_to_ul.html")

    def test_recursive_dict_and_list(self) -> None:
        """Test recursive dict."""
        rendered = self.template.render(
            context={
                "dict": {
                    "keyA": "valueB",
                    "keyC": {
                        "keyD": "valueE",
                    },
                    "keyF": [1, 2],
                }
            }
        )
        self.assertHTMLEqual(
            rendered,
            """
            <ul>
                <li><b>keyA:</b> valueB</li>
                <li><b>keyC:</b>
                    <ul>
                        <li><b>keyD:</b> valueE</li>
                    </ul>
                </li>
                <li><b>keyF:</b>
                    <ul>
                        <li>1</li>
                        <li>2</li>
                    </ul>
                </li>
            </ul>
            """,
        )
