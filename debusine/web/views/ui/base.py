# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Base structure of UI helpers for model instances."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, Generic, Self, TypeVar, cast

from django.db.models import Model
from django.http import HttpRequest

from debusine.utils import extract_generic_type_arguments

M = TypeVar("M", bound=Model)

REQUEST_CACHE_NAME = "_debusine_ui_helpers"

#: Registry of helper classes indexed by model class
_helper_registry: dict[str, type["UI[Any]"]] = {}


def model_name(m: type[Model] | Model) -> str:
    """Return the model name for a model class or instance."""
    return m._meta.label_lower


class UI(Generic[M]):
    """
    Per-model UI helpers.

    This is the root of a hierarchy of model companion classes that compute and
    cache per-request UI-specific information for a model instance.

    Helper classes are cached in the request and their lifetime corresponds to
    the request, so they can use @cached_property and cache other data in
    member variables at will.

    There is intentionally only one helper class per model, that acts as root
    for all use cases: this allows different UI needs to share intermediate
    cached results.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Register the subclass as the helper for M.

        The registry is used by the ``ui`` template tag.

        It is not used for instantiation from Python, which uses
        HelperClass.get() in order to give a correctly typed result.
        """
        (model_class,) = extract_generic_type_arguments(cls, UI)
        mname = model_name(model_class)
        if (old := _helper_registry.get(mname)) is not None:
            raise AssertionError(
                f"{old} already registered as helper for {model_class}"
            )
        _helper_registry[mname] = cls

    def __init__(self, request: HttpRequest, instance: M) -> None:
        """
        Instantiate the helper.

        :param request: the Django request
        :param ``instance``: the model instance
        """
        #: Current request
        self.request = request
        #: Model instance to wrap
        self.instance = instance

    @classmethod
    def get(cls, request: HttpRequest, instance: M) -> Self:
        """
        Return the UI helper for a model instance.

        The result is cached in the request, and possibly reused.
        """
        # Get the cache dict from the request
        ui_helpers_cache: dict[tuple[str, int], UI[Any]] | None = getattr(
            request, REQUEST_CACHE_NAME, None
        )
        if ui_helpers_cache is None:
            ui_helpers_cache = {}
            setattr(request, REQUEST_CACHE_NAME, ui_helpers_cache)

        # Lookup an existing helper class in the cache
        cache_key = (model_name(instance), instance.pk)
        if cached := ui_helpers_cache.get(cache_key):
            return cast(Self, cached)

        # Cache miss: create it and cache it
        res = cls(request, instance)
        ui_helpers_cache[cache_key] = res
        return res

    @classmethod
    def for_instance(cls, request: HttpRequest, instance: M) -> "UI[M]":
        """
        Return the UI helper for a model instance.

        The result is cached in the request, and possibly reused.

        This is the factory used when the helper class is not known, such as in
        Django template tags. Using get() when possible means better typing of
        the result value.
        """
        helper_class = _helper_registry[model_name(instance)]
        return helper_class.get(request, instance)

    @classmethod
    @contextmanager
    def preserve_registry(cls) -> Generator[None, None, None]:
        """Undo changes to the helper registry on exit."""
        orig = _helper_registry.copy()
        try:
            yield
        finally:
            # Update in place, so that it also works for tests that imported
            # _helper_registry from base
            _helper_registry.clear()
            _helper_registry.update(orig)
