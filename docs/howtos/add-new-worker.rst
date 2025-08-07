.. _add-new-worker:

================
Add a new worker
================

Adding a new worker is a two-step process: first you need to :ref:`setup
the Debusine worker <set-up-debusine-worker>` on some server, and then you
need to enable the worker on the Debusine server.

Once configured, the worker automatically tries to connect to the Debusine
server, which initially denies him access because it is unknown. There are
two ways to enable the worker on the Debusine server:

Enable a new worker on the server based on the worker's name
------------------------------------------------------------

The Debusine admin should find the worker, for example:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin list_workers

An output example::

             ╷          ╷                                  ╷                                  ╷                                                                  ╷
   Name      │ Type     │ Registered                       │ Connected                        │ Token hash (do not copy)                                         │ Enabled
  ═══════════╪══════════╪══════════════════════════════════╪══════════════════════════════════╪══════════════════════════════════════════════════════════════════╪═════════
   saturn    │ external │ 2024-11-15T13:23:30.200968+00:00 │ 2024-11-15T14:02:30.105791+00:00 │ f299978eb7687291a6149df2b47e91e21891e5a04f2d41363617b2582a81e4ce │ True
   mars      │ external │ 2024-11-16T14:24:31.390558+00:00 │ -                                │ 7cff1b6b968bc2db06aec9cf4557ecc9e6c63356e2ade73c2c47c1d7015214a0 │ True
   mercury   │ external │ 2024-11-17T15:25:32.473994+00:00 │ -                                │ 6eff3aee879ad9c953f8f12bf0fe8544126ec80eab0867aa205413db6fcbeed2 │ False

In this case, the worker ``mercury`` is the newest addition according to the available
information: it has the latest registered time and is the only one that is not enabled.

The default name of the worker is the FQDN of the worker.

To enable the new worker on the server:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin manage_worker enable mercury

The worker then will be able to connect and process tasks.

Enable a new worker on the server based on the token
----------------------------------------------------

It is also possible for the debusine-worker admin to send the token associated
to the worker, to the debusine-server admin. This would allow the
debusine-server admin to enable the worker.

The token can be found in the worker's installation system in the file
``/etc/debusine/worker/token`` or in the logs
(by default, it is in ``/var/log/debusine/worker/worker.log``).

The debusine-server admin could then enable the token using the command:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin manage_worker enable 93f1b0e9ad2e0ad1fd550f5fdf4ab809594fe1b38ffd37c5b3aa3858062ce0ab

.. _enable-signing-worker:

Enable a signing worker
-----------------------

:ref:`Signing workers <explanation-workers>` have access to private key
material.  To avoid accidents, enabling them requires a special option:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin list_workers
  Name        Type      Registered                        Connected                         Token hash                                                        Enabled
  ----------  --------  --------------------------------  --------------------------------  ----------------------------------------------------------------  ---------
  apollo      external  2024-10-21T16:39:57.351233+00:00  -                                 568cd68b5834da9bf223c7760226ff739caf2a461dbff2b524c35da26db2f280  False

  Number of workers: 1
  $ sudo -u debusine-server \
      debusine-admin manage_worker --worker-type signing enable apollo
