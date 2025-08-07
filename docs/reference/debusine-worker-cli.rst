.. _debusine-worker-cli:

===========================
The debusine-worker command
===========================

The ``debusine-worker`` command starts a :ref:`Worker
<explanation-workers>`.  It is provided by the ``debusine-worker``
package.

``debusine-worker`` has few options, described in ``--help``:

.. code-block:: console

    $ debusine-worker --help
    usage: debusine-worker [-h] [--log-file LOG_FILE] [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
    
    Start Debusine worker process: ...

``debusine-worker`` is normally run automatically through a systemd
unit.

``debusine-worker`` expects ``sbin`` dirs in its ``PATH``, which is
normally the case when ran from systemd.

See also:

  * :ref:`set-up-debusine-worker`
  * :ref:`configure-manage-worker`


Command output
--------------

``debusine-worker`` is meant to be run as a daemon and normally
doesn't directly produce any output, but appends status information to
its log files.


Return values
-------------

Return values of the ``debusine-worker`` command:

===============  ==================================================================================
  Return value    Meaning
===============  ==================================================================================
 0                Success
 1                Error: unhandled exception. Please report the error
 2                Error: wrong arguments and options
 3                Error: any other type of error such as non-writable log file,

                  invalid configuration file, etc.
===============  ==================================================================================
