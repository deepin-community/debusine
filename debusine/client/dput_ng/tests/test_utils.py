# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Testing utilities for Debusine dput-ng integration."""

import warnings
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import SkipTest, mock

from dput.interface import AbstractInterface
from dput.profile import load_profile


class FakeInterface(AbstractInterface):  # type: ignore[misc]
    """A dput-ng user interface that does nothing."""

    def initialize(self) -> None:  # pragma: no cover
        pass

    def shutdown(self) -> None:  # pragma: no cover
        pass


@contextmanager
def mock_client_config(api_url: str, token: str) -> Generator[None, None, None]:
    """Mock the debusine client handler with given configuration."""
    with mock.patch(
        "debusine.client.dput_ng.dput_ng_utils.get_debusine_client_config",
        return_value={"api-url": api_url, "token": token},
    ):
        yield


@contextmanager
def use_local_dput_ng_config(
    include_core: bool = False,
) -> Generator[None, None, None]:
    """
    Use our local dput-ng configuration.

    :param include_core: if True, also include the core dput-ng
      configuration; this is useful for integration tests.
    """
    config_locations = {str(Path(__file__).parent.parent / "skel"): 100}
    if include_core:
        if not Path("/usr/share/dput-ng").exists():  # pragma: no cover
            raise SkipTest("/usr/share/dput-ng missing (is dput-ng installed?)")
        config_locations["/usr/share/dput-ng"] = 30

    with (
        mock.patch("dput.core.CONFIG_LOCATIONS", config_locations),
        mock.patch("dput.core.DPUT_CONFIG_LOCATIONS", {}),
        mock.patch("dput.profile._multi_config", None),
    ):
        yield


@contextmanager
def ignore_dput_ng_warnings() -> Generator[None, None, None]:
    """Ignore some warnings that should be fixed in dput-ng."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=ResourceWarning, module="dput"
        )
        yield


def load_local_profile(host: str) -> dict[str, Any]:
    """Load one of our local profiles."""
    with use_local_dput_ng_config(), ignore_dput_ng_warnings():
        profile = load_profile(host)
        assert isinstance(profile, dict)
        return profile
