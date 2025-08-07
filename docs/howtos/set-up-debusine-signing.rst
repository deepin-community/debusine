.. _set-up-debusine-signing:

================================
Set up a Debusine signing worker
================================

Introduction
------------

``debusine-signing`` is a signing service for Debusine, holding private keys
and using them to sign data on behalf of Debusine.  It is a daemon that
connects (via HTTP API and WebSockets) to a Debusine server, waits for tasks
and executes tasks locally.

Notes for production deployments
--------------------------------

``debusine-signing`` manages sensitive data: an attacker with the ability to
extract private keys in the clear would be able to construct intermediary
attacks that could go undetected for long periods of time.  None of
``debusine-signing``'s interfaces allow extracting private keys, but
deployments that involve high-value keys should take some extra care.

* The signing worker uses its own database, which should not be accessible
  by the Debusine server.  In a small deployment it can be local to the
  signing worker, but if it is on a separate system then that should be
  distinct from the system hosting the Debusine server's database.

* Limit direct user access as much as possible.

* Apply strict firewall rules.  ``debusine-signing`` does not require any
  inbound connections.

* You may need to back up ``/etc/debusine/signing/*.key``; these keys allow
  decrypting private keys stored in the signing database, so avoid storing
  backups of the signing database and these keys in the same place.

* The signing database includes audit logs.  In the event of an attack that
  is not suspected of having been able to extract private key material
  directly, these audit logs may be useful in determining which keys need to
  be rotated.

Initial setup
-------------

Install the ``debusine-signing`` package.
The :ref:`Initial setup using Debusine server <initial-setup-debusine-server>`
has the instructions how to add the Debusine repository.

     .. code-block:: console

       $ sudo apt install postgresql  # if you need to install PostgreSQL
       $ sudo apt install debusine-signing

To know what the package does
during installation see the :ref:`What the package does on installation
<what-debusine-signing-package-does-on-installation>` section. Follow
the steps below to finalize the set-up.

  #. By default, the package is configured to use a PostgreSQL database
     using the default local Unix socket and the authentication is done at
     the user level. The package creates a "debusine-signing" Unix account.

     The database configuration can be edited in
     ``/etc/debusine/signing/db_postgresql.py`` .

  #. Create the file ``/etc/debusine/signing/config.ini`` . Use
     ``/usr/share/doc/debusine-signing/examples/config.ini`` as an example:

     .. code-block:: console

       $ sudo cp /usr/share/doc/debusine-signing/examples/config.ini /etc/debusine/signing/
       $ sudo editor /etc/debusine/signing/config.ini

     Change the ``api-url`` in the configuration file as needed. Note that
     it must be HTTPS: see :ref:`configure-nginx-with-https` for how to set
     this up, especially if you are using a local self-signed certificate.

  #. Generate a private key to be used to encrypt other private keys:

     .. code-block:: console

       $ sudo -u debusine-signing debusine-signing generate_service_key /etc/debusine/signing/0.key

  #. Restart ``debusine-signing`` with the new configuration and group:

     .. code-block:: console

       $ sudo systemctl restart debusine-signing

     The logs can be seen in ``/var/log/debusine/signing/``.

  #. Following the restart, the token will appear in
     ``/etc/debusine/signing/token`` and in the log file
     ``/var/log/debusine/signing/signing.log``.
     The Debusine admin should :ref:`approve the tokens pending for approval
     <enable-signing-worker>`.  Nothing needs to be done from the side of
     debusine-signing.

Managing ``debusine-signing``
-----------------------------

Use the ``systemctl`` command to start (done automatically by the package), stop
or restart debusine-signing:

.. code-block:: console

  $ sudo systemctl start debusine-signing
  $ sudo systemctl stop debusine-signing
  $ sudo systemctl status debusine-signing

The default configuration logs relevant information in
``/var/log/debusine/signing/signing.log``. The log file and log level can
be changed in ``/etc/debusine/signing/config.ini``.

If ``debusine-signing`` outputs the log to the stdout/stderr the logs are
available via:

.. code-block:: console

  $ journalctl --unit debusine-signing

Troubleshooting
---------------

Information about the state of debusine-signing can be found via:

  #. ``systemctl status debusine-signing`` to verify that the service is running
  #. ``/var/log/debusine/signing/signing.log`` to see any error messages
  #. ``journalctl -u debusine-signing`` for other information

To change the settings of ``debusine-signing``, for example to increase the logging level:

.. code-block:: console

  $ sudo editor /etc/debusine/signing/config.ini # change log-level
  $ sudo systemctl restart debusine-signing

``debusine-signing`` can be launched without systemd which might be useful for
troubleshooting. Run it with the same ``debusine-signing`` user. For example:

.. code-block:: console

  $ sudo systemctl stop debusine-signing

  # for example, comment out log-file so the log is to stderr
  $ sudo editor /etc/debusine/signing/config.ini

  # execute it with the correct user
  $ sudo -u debusine-signing debusine-signing signing_worker

``debusine-signing`` can also be launched using a different user for debugging purposes.
The configuration (``config.ini`` and ``token`` files) in the
directory ``$HOME/.config/debusine/signing`` have higher priority than the global
configuration, ``/etc/debusine/signing`` .

.. _what-debusine-signing-package-does-on-installation:

What the package does on installation
-------------------------------------

* Creates the ``debusine-signing`` user
* Installs a systemd service (named debusine-signing)
* Creates the directories ``/etc/debusine/signing``,
  ``/var/log/debusine/signing``, and ``/var/lib/debusine-signing``
