# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Utilities for debusine's dput-ng integration."""

from collections.abc import MutableMapping
from typing import Any
from urllib.parse import urlparse

from dput.core import logger

from debusine.client.config import ConfigHandler
from debusine.client.debusine import Debusine


def get_debusine_client_config(fqdn: str) -> MutableMapping[str, str]:
    """
    Get debusine client configuration for a given FQDN.

    This is a useful hook for testing.
    """
    configuration = ConfigHandler()
    for section in configuration.sections():
        if (
            section.startswith("server:")
            and (api_url := configuration[section].get("api-url")) is not None
            and urlparse(api_url).hostname == fqdn
        ):
            configuration._server_name = section[len("server:") :]
            break
    else:
        raise ValueError(
            f"No debusine client configuration for {fqdn}; run 'debusine setup'"
        )
    return configuration.server_configuration()


def make_debusine_client(profile: dict[str, Any]) -> Debusine:
    """Make a suitable debusine client."""
    fqdn = profile["fqdn"]
    scope = profile["debusine_scope"]

    config = get_debusine_client_config(fqdn)
    return Debusine(
        base_api_url=config["api-url"],
        api_token=config["token"],
        scope=scope,
        logger=logger,
    )
