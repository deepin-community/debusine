# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Classes and functions supporting database tests."""

import concurrent.futures
import hashlib
import threading
from collections.abc import Callable
from typing import Any

from django.db import connections, transaction

from debusine.db.models import File


class RunInParallelTransaction:
    """
    Execute a callable in a separate transaction that gets started in a thread.

    The :py:meth:start_transaction() starts a new thread in which it starts
    the transaction and executes the callable. The transaction then stays
    open and the thread waits until we decide to call
    :py:meth:stop_transaction().
    """

    def __init__(self, to_run: Callable[[], Any]) -> None:
        """
        Create a RunInParallelTransaction object.

        :param to_run: the function/callable object that gets executed in
          a transaction started in a thread.
        """
        self._to_run = to_run
        self._transaction_ready = threading.Event()
        self._transaction_close = threading.Event()
        self._thread: threading.Thread | None = None

    def start_transaction(self) -> None:
        """
        Start a transaction and execute the registered callable.

        It does this in a new thread that then waits until
        :py:meth:stop_transaction() gets called.
        """

        def run_in_thread() -> None:
            with transaction.atomic():
                self._to_run()
                self._transaction_ready.set()
                self._transaction_close.wait()

                # Close the DB connection that was opened in this thread
                # (otherwise the connection is left opened and the test
                # runner might not be able to destroy the debusine-test DB)
                connections.close_all()

        self._thread = threading.Thread(target=run_in_thread)
        self._thread.start()
        self._transaction_ready.wait()

    def stop_transaction(self) -> None:
        """Close the open transaction and let the thread exit."""
        assert self._thread is not None
        self._transaction_close.set()
        self._thread.join()


class RunInThreadAndCloseDBConnections:
    r"""
    Execute a callable in a different thread.

    Close the database connections in the callable's thread after the callable
    has run.

    There are two ways to use this class:
    thread = RunInThreadAndCloseDBConnections(callable, \*args, \*\*kwargs)

    * First option: run and wait:
        thread.run_and_wait() # launch callable and waits for it to finish.
        Close the DB connection. Return the callable return value.
    * Second option: start(), some code, join()
      thread.start()  # start the callable in a new thread
      # code executed in the main thread
      # that could wait/set events in the just created thread.
      thread.join() # wait for the callable and close the DB connections
    """

    def __init__(
        self, to_run: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> None:
        """
        Create a RunInThreadAndCloseDBConnections object.

        :param to_run: callable to execute in a thread when run() is called.
        :param args: args to pass to to_run when it is executed.
        :param kwargs: kwargs to pass to to_run when it is executed.
        """
        self._to_run = to_run
        self._args = args
        self._kwargs = kwargs

        self._thread: threading.Thread | None = None

    def run_and_wait(self) -> Any:
        r"""
        Create a new thread and execute the registered callable.

        Wait for the callable to finish and return callable's return value.

        Close the database connections after running the callable, in its
        thread.
        """
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(self._run_and_close_db_connections)
            result = future.result()
            return result

    def _run_and_close_db_connections(self) -> None:
        self._to_run(*self._args, **self._kwargs)
        connections.close_all()

    def start_in_thread(self) -> None:
        """
        Create a thread and start the callable in the thread.

        Do not wait for the callable to finish.

        After the callable is executed: the database connections will be closed
        in the same thread as the callable was run.
        """
        self._thread = threading.Thread(
            target=self._run_and_close_db_connections
        )
        self._thread.start()

    def join(self) -> None:
        """Wait for the thread created in start_in_thread() to exit."""
        assert self._thread is not None
        self._thread.join()


def _calculate_hash_from_data(data: bytes) -> bytes:
    hasher = hashlib.new(File.current_hash_algorithm)
    hasher.update(data)
    return hasher.digest()
