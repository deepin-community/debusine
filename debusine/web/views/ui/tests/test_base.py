# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests the UI helper infrastructure."""

from typing import cast
from unittest import mock

from django.db.models import Model
from django.http import HttpRequest
from django.test import RequestFactory

from debusine.test.django import TestCase
from debusine.web.views.ui.base import UI, _helper_registry


def make_mock_model(label: str = "test") -> type[Model]:
    """Create a mock model class."""

    class _MockModel:
        _meta = mock.Mock()
        _meta.label_lower = label

        def __init__(self, pk: int = 1) -> None:
            self.pk = pk

    return cast(type[Model], _MockModel)


def make_helper(model_class: type[Model]) -> type[UI[Model]]:
    """Create a mock helper for a model class."""

    class Helper(UI[model_class]):  # type: ignore[valid-type]
        pass

    return Helper


class UITests(TestCase):
    """Test the UI base class."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(UI.preserve_registry())

    def request(self) -> HttpRequest:
        factory = RequestFactory()
        request = factory.get("/")
        return request

    def test_helper_registry(self) -> None:
        MockModel = make_mock_model()
        Helper = make_helper(MockModel)

        self.assertIs(_helper_registry["test"], Helper)

    def test_only_one_helper_per_model(self) -> None:
        MockModel = make_mock_model()
        Helper = make_helper(MockModel)
        with self.assertRaisesRegex(
            AssertionError,
            f"{Helper} already registered as helper for {MockModel}",
        ):
            make_helper(MockModel)

    def test_get(self) -> None:
        request = self.request()
        MockModel = make_mock_model()
        Helper = make_helper(MockModel)

        instance = MockModel(pk=1)
        helper = Helper.get(request, instance)
        self.assertIsInstance(helper, Helper)
        self.assertIs(helper.request, request)
        self.assertIs(helper.instance, instance)

        # Instances are reused
        self.assertIs(Helper.get(request, MockModel(pk=1)), helper)

        # An instance with a different pk gets a different helper instance
        instance2 = MockModel(pk=2)
        helper2 = Helper.get(request, instance2)
        self.assertIsInstance(helper2, Helper)
        self.assertIs(helper2.request, request)
        self.assertIs(helper2.instance, instance2)
        self.assertIs(Helper.get(request, MockModel(pk=2)), helper2)

    def test_for_instance(self) -> None:
        request = self.request()

        FooModel = make_mock_model("foo")
        FooHelper = make_helper(FooModel)
        BarModel = make_mock_model("bar")
        BarHelper = make_helper(BarModel)

        foo = FooModel()
        foo_helper = UI.for_instance(request, foo)
        self.assertIsInstance(foo_helper, FooHelper)
        self.assertIs(UI.for_instance(request, FooModel()), foo_helper)

        bar = BarModel()
        bar_helper = UI.for_instance(request, bar)
        self.assertIsInstance(bar_helper, BarHelper)
        self.assertIs(UI.for_instance(request, BarModel()), bar_helper)

    def test_request_isolation(self) -> None:
        """UI helpers are request specific."""
        requestA = self.request()
        requestB = self.request()
        MockModel = make_mock_model()
        Helper = make_helper(MockModel)

        instance = MockModel(pk=1)

        helperA = Helper.get(requestA, instance)
        helperB = Helper.get(requestB, instance)

        self.assertIsNot(helperA, helperB)
        self.assertIs(Helper.get(requestA, instance), helperA)
        self.assertIs(Helper.get(requestB, instance), helperB)
