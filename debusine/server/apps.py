# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Django Application Configuration for the server application."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from django.apps import AppConfig


class ServerConfig(AppConfig):
    """Django's AppConfig for the server application."""

    name = 'debusine.server'

    def ready(self) -> None:
        """Mark workers as disconnected. Runs on Django startup."""
        # Import the scheduler so that its signal receiver is registered.
        import debusine.server.scheduler  # noqa: F401

        # Import various tasks so that they are registered.
        import debusine.server.tasks  # noqa: F401
        import debusine.server.tasks.wait  # noqa: F401
        import debusine.signing.tasks  # noqa: F401
        from debusine.db.models import Worker

        # The workers are disconnected (because the server is just loading).
        # But, depending on how debusine-server exited, the workers might
        # be registered as connected in the DB. Let's mark them as disconnected.
        #
        # The environment variable DEBUSINE_WORKER_MANAGER is set externally
        # (e.g. in debusine-server package via debusine-server systemd unit).
        # It is tempting to use Django's RUN_MAIN environment variable, but it's
        # part of a private API
        # (see https://code.djangoproject.com/ticket/34115)
        #
        # If using "runserver" without "--noreload": the code runs
        # twice. Given that "runserver" is for development it should not
        # cause issues.
        #
        # The reason of setting DEBUSINE_WORKER_MANAGER is to avoid
        # running the disconnect when a debusine sysadmin runs
        # commands such as "debusine-admin worker list"

        if os.environ.get("DEBUSINE_WORKER_MANAGER", "0") != "0":
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                Worker.objects.mark_all_disconnected()
            else:
                # Run this in a separate thread so that it can use the
                # synchronous ORM, and wait for it to finish.  We can't use
                # a more conventional async style because AppConfig.ready
                # has no asynchronous version, even if it's being called as
                # part of an ASGI server.
                ThreadPoolExecutor(max_workers=1).submit(
                    Worker.objects.mark_all_disconnected
                ).result()
