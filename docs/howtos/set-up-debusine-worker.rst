.. _set-up-debusine-worker:

========================
Set up a Debusine worker
========================

Introduction
------------

``debusine-worker`` is a daemon that connects (via HTTP API and WebSockets)
to a Debusine server, waits for tasks and executes tasks locally.  See
:ref:`explanation-workers`.

Initial setup
-------------

Install the ``debusine-worker`` package. The :ref:`Initial setup using Debusine server<initial-setup-debusine-server>`
has the instructions how to add the Debusine repository.

To know what the package does
during installation see the :ref:`What the package does on installation
<what-debusine-worker-package-does-on-installation>` section. Follow
the steps below to finalize the set-up.

  #. Create the file ``/etc/debusine/worker/config.ini`` . Use
     ``/usr/share/doc/debusine-worker/examples/config.ini`` as an example:

     .. code-block:: console

       $ sudo cp /usr/share/doc/debusine-worker/examples/config.ini /etc/debusine/worker/
       $ sudo editor /etc/debusine/worker/config.ini

     Change the ``api-url`` in the configuration file as needed.

  #. Add the user debusine-worker to the group, ``sbuild``:

     .. code-block:: console

       $ sudo sbuild-adduser debusine-worker

     (``sbuild-adduser`` adds the debusine-worker to the correct group,
     ignore the rest of ``sbuild-adduser`` messages regarding how to setup
     tasks for sudo users)

  #. Restart ``debusine-worker`` with the new configuration and group:

     .. code-block:: console

       $ sudo systemctl restart debusine-worker

     The logs can be seen in ``/var/log/debusine/worker/``.

  #. Following the restart, the token will appear in ``/etc/debusine/worker/token`` and
     in the log file ``/var/log/debusine/worker/worker.log``.
     The Debusine admin should :ref:`approve the tokens pending for approval
     <add-new-worker>`. Nothing needs to be done from the side of
     debusine-worker.

Managing ``debusine-worker``
----------------------------

Use the ``systemctl`` command to start (done automatically by the package), stop
or restart the debusine-worker:

.. code-block:: console

  $ sudo systemctl start debusine-worker
  $ sudo systemctl stop debusine-worker
  $ sudo systemctl status debusine-worker

The default configuration logs relevant information in
``/var/log/debusine/worker/worker.log``. The log file and log level can
be changed in ``/etc/debusine/worker/config.ini``.

If ``debusine-worker`` outputs the log to the stdout/stderr the logs are
available via:

.. code-block:: console

  $ journalctl --unit debusine-worker

Troubleshooting
---------------

Information about the state of debusine-worker can be found via:

  #. ``systemctl status debusine-worker`` to verify that the service is running
  #. ``/var/log/debusine/worker/worker.log`` to see any error messages
  #. ``journalctl -u debusine-worker`` for other information

To change the settings of ``debusine-worker``, for example to increase the logging level:

.. code-block:: console

  $ sudo editor /etc/debusine/worker/config.ini # change log-level
  $ sudo systemctl restart debusine-worker

``debusine-worker`` can be launched without systemd which might be useful for
troubleshooting. Run it with the same ``debusine-worker`` user. For example:

.. code-block:: console

  $ sudo systemctl stop debusine-worker
  $ sudo editor /etc/debusine/worker/config.ini # for example comment out log-file
                                                # so the log is to stderr
  $ sudo -u debusine-worker debusine-worker     # execute it with the correct user

``debusine-worker`` can also be launched using a different user for debugging purposes.
The configuration (``config.ini`` and ``token`` files) in the
directory ``$HOME/.config/debusine/worker`` have higher priority than the global
configuration, ``/etc/debusine/worker`` .

.. _what-debusine-worker-package-does-on-installation:

What the package does on installation
-------------------------------------

* Creates the ``debusine-worker`` user
* Installs a systemd service (named debusine-worker)
* Creates the directories ``/var/log/debusine/worker`` and ``/var/lib/debusine-worker``
