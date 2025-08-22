.. _set-up-debusine-server:

========================
Set up a Debusine server
========================

Introduction
------------

The Debusine server waits for workers to connect and receives work requests
from the clients. When a work request is received by the server it tries
to match a worker for it and sends the work request to the worker.

.. _initial-setup-debusine-server:

Initial setup
-------------

Install the ``debusine-server`` package. All its dependencies are available
on Debian 12 'bookworm' (using bookworm-backports).

.. code-block:: console

   $ sudo apt install postgresql redis # if you need to install Postgresql/redis
   $ sudo apt install debusine-server

If needed configure APT with the appropriate :ref:`package repository
<debusine-package-repositories>`.

To know what the package does during
installation see the :ref:`What the package does on installation
<what-debusine-server-package-does-on-installation>` section.

Follow the steps below to finalize the set-up:

  #. By default, the package is configured to use a PostgreSQL database
     using the default local Unix socket and the authentication is done at
     the user level. The package creates a "debusine-server" Unix account.

  #. Debusine server has many configuration options. Review
     the documentation in ``/etc/debusine/server/*``. If you need
     to use your own local settings instead of the default ones create
     a ``local.py`` file:

     .. code-block:: console

       $ sudo cp -i /etc/debusine/server/local.py.sample \
          /etc/debusine/server/local.py

     And edit the file ``/etc/debusine/server/local.py``.

     Note that you can choose which type of settings are running. By default,
     ``selected.py`` is a symbolic link to ``production.py``, but this can be
     changed to ``development.py``.

  #. Commands can be issued to the server, for example:

     .. code-block:: console

       $ sudo -u debusine-server debusine-admin list_tokens

     To see the list of commands:

     .. code-block:: console

       $ sudo -u debusine-server debusine-admin help

     The Debusine specific commands are under the ``[server]`` section.

  #. Configure a webserver (see below for details on how to use
     Nginx with the default setup of debusine-server using daphne).

     You can run ``sudo -u debusine-server debusine-admin info`` to check that
     Debusine server configuration matches the expectations of your web server
     setup.


Debusine server commands
------------------------
``debusine-server`` package provides the command ``debusine-admin``. In the default
configuration it is needed to run the command using the ``debusine-server``
user. The main reason is the default authentication for the ``debusine``
PostgreSQL database. A secondary reason are the permissions of the log
files: only writable by ``debusine-server`` command.

To see the list of commands:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin

It will list all the ``debusine-admin`` commands and the Debusine specific ones. The
Debusine specific commands are under the ``[server]`` section::

  [server]
    create_notification_channel
    create_token
    create_user
    delete_expired
    delete_notification_channel
    delete_tokens
    list_notification_channels
    list_tokens
    list_users
    list_work_requests
    manage_notification_channel
    manage_user
    vacuum_storage
    worker

You can see the command specific help using ``--help``, for example:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin create_token --help

The section :ref:`debusine-admin command <debusine-admin-cli>` has documentation
for each command.

.. _testing-sending-emails:

Testing sending emails
----------------------

``debusine-server`` can send emails when certain events happen. For example,
if a work request fails, it can send an email to notify the user that there is
a problem.

By default, ``debusine-server`` uses the local MTA. To test if it can send,
execute the command:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin sendtestemail destination@example.com

If the email is not delivered: check ``/var/log/debusine/server/`` files
and read the next section for the email settings.

.. _smtp-configuration:

Configuration for sending emails
--------------------------------

Enable the ``local.py`` settings file if not done before:

.. code-block:: console

  $ # if you have not enabled using local.py before:
  $ cp -i /etc/debusine/server/local.py.sample /etc/debusine/server/local.py

By default, Django, sends emails using the local MTA. You can use any
SMTP server. For example, edit ``/etc/debusine/server/local.py`` and add:

.. code-block:: python3

  DEFAULT_FROM_EMAIL = "noreply@example.com"
  EMAIL_HOST = "smtp.example.com"
  EMAIL_PORT = 587
  EMAIL_HOST_USER = "user"
  EMAIL_HOST_PASSWORD = "the_password"
  EMAIL_USE_TLS = True

More settings are available in the `Django documentation email settings`_.

See the section :ref:`testing sending emails <testing-sending-emails>` to send
a test email.

When the test email works: restart debusine-server so the new settings
are applied:

.. code-block: console

  $ sudo systemctl restart debusine-server

.. _configure-nginx:

Configuration with Nginx
------------------------

The package provides a ``debusine-server.service`` unit for systemd
that will run Daphne (HTTP/HTTP2/WebSocket protocol server) and make
Debusine available on ``/var/lib/debusine/server/daphne.sock`` . There
is also a ready-to-use Nginx virtual host pointing to the ``daphne.sock``
file to make it available.

  #. Install (or create) the Nginx configuration. ``debusine-server`` package
     provides an example:
     
     .. code-block:: console

       $ sudo apt install nginx
       $ sudo cp /usr/share/doc/debusine-server/examples/nginx-vhost.conf \
            /etc/nginx/sites-available/debusine.example.net

     Change the variable "server_name" by the correct one. For testing, if the only
     "site-available" in Nginx is Debusine the default ``localhost`` can be left. It is
     possible to access Debusine via IP. Otherwise edit the file:

     .. code-block:: console

       $ sudo editor /etc/nginx/sites-available/debusine.example.net

     Search for "server_name" and change its value.

  #. Enable the Nginx configuration for the Debusine server:

     .. code-block:: console

       $ sudo ln -sf /etc/nginx/sites-available/debusine.example.net /etc/nginx/sites-enabled

     When setting up a new server, the default Nginx server configuration
     may need to be deleted:

     .. code-block:: console

       $ sudo rm /etc/nginx/sites-enabled/default

  #. Restart Nginx:

     .. code-block:: console

       $ sudo systemctl restart nginx

  #. If the server's hostname does not match the HTTP VirtualHost, set
     DEBUSINE_FQDN in Debusine settings:

     .. code-block:: console

       $ # if you have not enabled using local.py before:
       $ sudo cp -i /etc/debusine/server/local.py.sample /etc/debusine/server/local.py
       $ # Then edit the file
       $ sudo editor /etc/debusine/server/local.py

     Set the ``DEBUSINE_FQDN`` to your VirtualHost.

     (for testing, you could have a line such as: ``ALLOWED_HOSTS = ["*"]``, but
     do not use it in production)

  #. Restart to apply the new settings:

     .. code-block:: console

       $ sudo systemctl restart debusine-server

  #. Verify that Debusine's welcome page loads on ``http://your_server`` (by name or IP).

     If the welcome page cannot be loaded please check ``/var/log/debusine/server`` and
     ``/var/log/nginx``.


.. _configure-nginx-with-https:

Configure Nginx with an HTTPS certificate
-----------------------------------------

The example Nginx configuration used in the :ref:`previous step
<configure-nginx>` listens on HTTPS as well as HTTP, using a
locally-generated self-signed certificate.  If you are happy connecting over
HTTP, you can skip this section.

For a public Debusine server, you should change the ``ssl_certificate`` and
``ssl_certificate_key`` directives there to use a real certificate, for
example via `Let's Encrypt <https://letsencrypt.org/>`_.  You might also
wish to enforce the use of HTTPS by removing the ``listen`` directives for
port 80.

Configure Nginx with an HTTPS certificate for a local development setup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This only makes sense if you need to deploy Debusine locally on machines
that cannot obtain LetsEncrypt certificates.

For a local development setup, you may wish to export the self-signed
certificate to other machines to that they can connect to your server over
HTTPS, which you can do as follows:

  #. The self-signed certificate was generated by ``make-ssl-cert``, but its
     subject name must match the fully-qualified domain name (FQDN) that you
     will use from other machines to connect to the Debusine server.  If
     ``hostname -f`` does not match the FQDN you will use from other
     machines, then you must first correct it:

     #. Make the unqualified hostname be an alias for the FQDN in
        ``/etc/hosts``.  For example, you might change ``127.0.1.1
        debusine`` to read ``127.0.1.1 debusine.incus debusine``.

     #. Regenerate the self-signed certificate:

        .. code-block:: console

          $ sudo make-ssl-cert -f generate-default-snakeoil

     #. Restart Nginx using the new certificate:

        .. code-block:: console

          $ sudo systemctl restart nginx.service

  #. The self-signed certificate is in
     ``/etc/ssl/certs/ssl-cert-snakeoil.pem`` on your Debusine server.  On
     each machine from which you want to connect to the server:

     #. Place a copy of the self-signed certificate in
        ``/usr/local/share/ca-certificates/{fqdn}.crt`` (replacing
        ``{fqdn}`` with the fully-qualified domain name of your Debusine
        server).

     #. Update the system's certificate collection:

        .. code-block:: console

          $ sudo update-ca-certificates

  #. Verify that Debusine's welcome page loads, using ``curl
     https://{fqdn}/`` from a machine where you installed a copy of the
     self-signed certificate.


.. _what-debusine-server-package-does-on-installation:

What the package does on installation
-------------------------------------

* Creates the ``debusine-server`` user
* Collects static files in ``/var/lib/debusine/server/static/``
  (to do this manually: ``sudo -u debusine-server debusine-admin collectstatic``)
* Provides ready-to-customize configuration files for Nginx/daphne
  (in ``/etc/nginx/sites-available/debusine-server``)
* Installs a systemd service (``debusine-server.service``) that uses Daphne to make the
  Debusine server available on ``/var/lib/debusine/server/daphne.sock``
* Creates the directories ``/var/log/debusine/server`` and ``/var/lib/debusine-server``
* Install a systemd timer unit to run daily ``debusine-admin vacuum_storage``.
  (see it using ``systemctl list-timers``, disable it for the next boot via
  ``systemctl disable debusine-server-vacuum-storage.timer`` or stop it now
  ``systemctl stop debusine-server-vacuum-storage.timer``).

.. _Django documentation email settings: https://docs.djangoproject.com/en/3.2/ref/settings/#email-host
