# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""debusine-admin command to print server configuration information."""

import shutil
import socket
import textwrap
from typing import Any, NoReturn

from django.conf import settings

from debusine.django.management.debusine_base_command import DebusineBaseCommand


class Command(DebusineBaseCommand):
    """Command to show server configuration."""

    help = "Show server configuration to help deploy"

    def handle(self, *args: Any, **options: Any) -> NoReturn:
        """Show server configuration."""
        cols, lines = shutil.get_terminal_size()
        wrapper = textwrap.TextWrapper(width=cols)

        def wprint(text: str) -> None:
            self.stdout.write("\n".join(wrapper.wrap(text)))

        self.stdout.write()
        self.stdout.write(f" * DEBUSINE_FQDN = {settings.DEBUSINE_FQDN!r}")
        self.stdout.write()
        wprint(
            "DEBUSINE_FQDN is the hostname used to generate URLs to debusine."
            " It can be different from the machine's hostname in case debusine"
            " is running, for example, on a server behind a proxy or load"
            " balancer."
        )
        self.stdout.write()
        socket_fqdn = socket.getfqdn()
        if socket_fqdn != settings.DEBUSINE_FQDN:
            wprint(
                f"The current value is changed from the default {socket_fqdn!r}"
            )
        else:
            wprint(
                "The current value matches the autodetected default"
                " from socket.getfqdn()."
            )

        self.stdout.write()
        self.stdout.write(f" * ALLOWED_HOSTS = {settings.ALLOWED_HOSTS!r}")
        self.stdout.write()
        wprint(
            "See https://docs.djangoproject.com/en/5.0/ref/settings/#allowed-hosts"  # noqa: E501
        )
        self.stdout.write()
        allowed_hosts_default = [
            settings.DEBUSINE_FQDN,
            "localhost",
            "127.0.0.1",
        ]
        if settings.ALLOWED_HOSTS != allowed_hosts_default:
            wprint(
                "The current value is changed from default"
                f" {allowed_hosts_default!r}"
            )
        else:
            wprint(
                "The current value matches the autodetected default from"
                " DEBUSINE_FQDN."
            )
        raise SystemExit(0)
