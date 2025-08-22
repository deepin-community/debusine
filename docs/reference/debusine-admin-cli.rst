.. _debusine-admin-cli:

==========================
The debusine-admin command
==========================

The ``debusine-admin`` command is the usual `django-admin
<https://docs.djangoproject.com/en/4.2/ref/django-admin/>`_ command
of a Django project, but Debusine adds many `custom management commands
<https://docs.djangoproject.com/en/4.2/howto/custom-management-commands/>`_
that are documented on this page.

This command must be executed on the server while connected as the
``debusine-server`` user. For example, to run the command ``check``:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin check

For commands that are currently undocumented, you can still explore their
features by using the ``--help`` command line option:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin worker edit_metadata --help

Command output
--------------

If a command is successful: nothing is printed and the return code is 0.

Managing workers
----------------

``worker``
~~~~~~~~~~

This command has several sub-commands.

Workers are **disabled** by default: they do not receive any tasks to run.

``worker enable``
.................

To enable a worker find its name or its token using the ``debusine-admin
worker list`` command and then enable the worker using ``debusine-admin
worker enable``. To enable the worker ``worker-01``:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin worker enable worker-01

``worker disable``
..................

To disable a worker: use ``disable`` instead of ``enable``. When disabling
a worker, any work requests assigned to the worker in RUNNING or PENDING status
will be de-assigned and assigned to another worker.

Disabling a worker only makes the worker's token disabled: the worker will not
be able to submit any results (the server will reject any communication
with HTTP 403) or interact with the server. No attempt is made to stop the
worker's current task.

To stop the running work requests on the worker run in the worker:

.. code-block:: console

  $ sudo systemctl stop debusine-worker

.. _debusine-admin-worker-edit-metadata:

``worker edit_metadata``
........................

A Debusine sysadmin can attach static metadata to a worker. To edit the
worker metadata for ``worker-01`` you would run:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin worker edit_metadata worker-01

This launches ``sensible-editor`` on a file with the current metadata.
The worker metadata is presented (and expected to be formatted) in YAML.

.. note::

    The initial metadata is presented as an empty dictionary (``{}``).
    That line should be deleted when you start inputting your own
    metadata.

When exiting the editor, the worker metadata will be updated with whatever
got saved in the edited file (except if the YAML turns out to be invalid).

For non-interactive use, you can store the metadata in a file, and pass
that file with ``--set``:

.. code-block:: console

  $ cat > /tmp/metadata <<END
  system:architectures:
  - amd64
  - i386
  END
  $ sudo -u debusine-server debusine-admin worker edit_metadata worker-01 --set /tmp/metadata
  debusine: metadata set for debusine-internal

``worker list``
...............

List workers with information:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin worker list
             ╷          ╷                                  ╷                                  ╷                                                                  ╷
   Name      │ Type     │ Registered                       │ Connected                        │ Token hash (do not copy)                                         │ Enabled
  ═══════════╪══════════╪══════════════════════════════════╪══════════════════════════════════╪══════════════════════════════════════════════════════════════════╪═════════
   saturn    │ external │ 2024-11-15T13:23:30.200968+00:00 │ 2024-11-15T14:02:30.105791+00:00 │ f299978eb7687291a6149df2b47e91e21891e5a04f2d41363617b2582a81e4ce │ True
   mars      │ external │ 2024-11-16T14:24:31.390558+00:00 │ -                                │ 7cff1b6b968bc2db06aec9cf4557ecc9e6c63356e2ade73c2c47c1d7015214a0 │ True
   mercury   │ external │ 2024-11-17T15:25:32.473994+00:00 │ -                                │ 6eff3aee879ad9c953f8f12bf0fe8544126ec80eab0867aa205413db6fcbeed2 │ False
             ╵          ╵                                  ╵                                  ╵                                                                  ╵

If a worker is not connected at this time: the ``Connected`` column has ``-``.
If it's connected: it has the time that connected.

Workers can be enabled or disabled using the command ``debusine-admin worker
enable`` or ``debusine-admin worker disable`` respectively.

You can get a machine-readable version of this output with the ``--yaml`` option.

Managing users
--------------

``create_user``
~~~~~~~~~~~~~~~

Users can be created using the ``create_user`` command. The password for the
new user will be printed.

.. code-block:: console

  $ sudo -u debusine-server debusine-admin create_user john.doe john@example.org
  m;Ag[2BcyItI..=M

A user can login on the website and then create tokens to be used by Debusine
client.

Tokens for a Debusine user (to be used by Debusine client) can also be created
using the command ``create_token``.

``manage_user``
~~~~~~~~~~~~~~~

Users can be managed using ``manage_user``. To change the email of the
``john.doe`` user to ``johnd@example.org``:

.. code-block:: console

  sudo -u debusine-server debusine-admin manage_user change-email john.doe johnd@example.org

``changepassword``
~~~~~~~~~~~~~~~~~~

Change the password for a user. The password is asked interactively:

.. code-block:: console

  sudo -u debusine-server debusine-admin changepassword john.doe

``create_super_user``
~~~~~~~~~~~~~~~~~~~~~

Create a super user. Super users have access to the `admin` section of
debusine-server (currently not used).

.. code-block:: console

  $ sudo -u debusine-server debusine-admin createsuperuser

The username is asked interactively.

``list_users``
~~~~~~~~~~~~~~

List the users with their `email` and `date_joined`.

.. code-block:: console

  $ sudo -u debusine-server debusine-admin list_users

              ╷                   ╷
   User       │ Email             │ Joined
  ════════════╪═══════════════════╪══════════════════════════════════
   john.doe   │ john@example.org  │ 2024-11-12T17:06:55.058340+00:00
              ╵                   ╵

You can get a machine-readable version of this output with the ``--yaml`` option.


Manage workspaces
-----------------

:ref:`explanation-artifacts` belong to :ref:`explanation-workspaces`.

.. _debusine-admin-cli-workspace:

``workspace``
~~~~~~~~~~~~~

This command has several sub-commands.

Note: to make commands easier to be invoked from Ansible, we take care
to make them idempotent.

The ``define`` (alias: ``create``) sub-command has the options:

  * ``--private``: Make the workspace private (only authenticated users can see the resources in it) (default)
  * ``--public``: Public permissions (non-logged users can see the resources of the workspace)
  * ``--default-expiration-delay``: Minimal time (in days) that a new artifact is kept in the workspace before being expired (default: 30)
  * ``--no-singleton-collections``: Don't create the usual singleton collections for this workspace (default: create singleton collections)
  * ``--with-owners-group name``: Name of the owners groups for the workspace (required when creating a workspace)

To create a workspace:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin workspace \
      define debian/Debian Owners-debian --default-expiration-delay 10

Workspaces are created ``Private`` by default (only registered users
can access its resources) and with a 10-days expiration delay.

Also use ``workspace define`` to change the permissions or expiry delay.

To change the permissions of the workspace `Debian` to `public`, while
preserving other parameters:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin workspace define debian/Debian --public


To rename a workspace:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin workspace rename debian/Debian Ubuntu


List the workspaces with information:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin workspaces list
                   ╷        ╷                                 ╷                    ╷
   Name            │ Public │ Default Expiration Delay (days) │ Default File Store │ # Other File Stores
  ═════════════════╪════════╪═════════════════════════════════╪════════════════════╪═════════════════════
   debusine/System │ True   │ Never                           │ Default (Local)    │ 0
   debusine/Debian │ False  │ Never                           │ Default (Local)    │ 0
                   ╵        ╵                                 ╵                    ╵

You can get a machine-readable version of this output with the ``--yaml`` option.

To delete a workspace (and associated resources):

.. warning::

    This will delete the entire contents of the workspace.  Data not
    stored elsewhere will be lost.  This operation cannot be undone.

.. code-block:: console

  $ sudo -u debusine-server debusine-admin workspace delete debian/Debian
  Would you like to delete workspace debian/Debian? [yN] y

You can skip the interactive prompt with ``--yes``.


To grant a new role to one or more groups on a workspace:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin workspace grant_role debian/Debian contributor QA-Team Security-Team


To revoke such roles:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin workspace revoke_role debian/Debian contributor Security-Team


To list roles:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin workspace list_roles debian/Debian
                  ╷        
    Group         │ Role   
   ═══════════════╪═══════ 
   Owners-debian  │ owner  
   QA-Team        │ contributor
                  ╵        

You can get a machine-readable version of this output with the ``--yaml`` option.


Manage work requests
--------------------

``list_work_requests``
~~~~~~~~~~~~~~~~~~~~~~

List the work requests and its status. Similar information can be displayed
using the web interface of Debusine.

.. code-block:: console

  $ sudo -u debusine-server debusine-admin list_work_requests
      ╷                 ╷                                  ╷                                  ╷                                  ╷           ╷
   ID │ Worker          │ Created                          │ Started                          │ Completed                        │ Status    │ Result
  ════╪═════════════════╪══════════════════════════════════╪══════════════════════════════════╪══════════════════════════════════╪═══════════╪═════════
   49 │ computer-lan-17 │ 2024-11-15T13:37:56.251136+00:00 │ 2024-11-15T13:37:56.289533+00:00 │ 2024-11-15T13:37:56.351906+00:00 │ completed │ success
   50 │ computer-lan-18 │ 2024-11-15T13:37:56.415081+00:00 │ 2024-11-15T13:37:56.446717+00:00 │ 2024-11-15T13:37:56.479520+00:00 │ completed │ success
   51 │ computer-lan-19 │ 2024-11-15T13:37:56.499152+00:00 │ 2024-11-15T13:37:56.526519+00:00 │ 2024-11-15T13:37:56.582989+00:00 │ completed │ success
   52 │ -               │ 2024-11-15T13:37:56.959527+00:00 │ -                                │ -                                │ completed │ success

You can get a machine-readable version of this output with the ``--yaml`` option.

.. _debusine-admin-notification-channels:

Manage notification channels
----------------------------

``create_notification_channel``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a notification channel:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin create_notification_channel Debian-LTS email << EOF
  {
    "from": "admin@example.org",
    "to": ["lts@example.org"]
  }
  EOF

notification channels can be used when creating a work request.

Currently only the type ``email`` is implemented.

See :ref:`configure-notifications` for more information.

``manage_notification_channel``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To change the name of a notification channel from ``Debian`` to ``Debian-LTS``:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin manage_notification_channel change-name Debian Debian-LTS

To change the associated data to the notification channel:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin manage_notification_channel change-data Debian-LTS << EOF
  {
    "from": "admin@example.org",
    "to": ["new-to@example.org"]
  }
  EOF

``list_notification_channels``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

List the notification channels with their information.

.. code-block:: console

  $ sudo -u debusine-server debusine-admin list_notification_channels

               ╷        ╷
   Name        │ Method │ Data
  ═════════════╪════════╪═════════════════════════════════════════════════════════════
   Debian-LTS  │ email  │ {'to': ['lts222@example.com'], 'from': 'admin@example.com'}
               ╵        ╵

You can get a machine-readable version of this output with the ``--yaml`` option.

``delete_notification_channel``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Deletes a notification channel.

.. code-block:: console

  $ sudo -u debusine-server debusine-admin delete_notification_channel Debian-LTS

Managing tokens
---------------

``create_token``
~~~~~~~~~~~~~~~~

Create a token. Must be associated to a user. The token is printed to the
stdout. Users can also create tokens using the web interface.

.. code-block:: console

  $ sudo -u debusine-server debusine-admin create_token john.doe
  ed73404d2edd232bc20955a2316a16c41e9b0bf2c240d6aceb7bf0706cb6d78f
  debian@debusine:~$

The tokens created by ``create_token`` are enabled by default.

In Debusine, there can be tokens that are not associated to users. They are
created when a debusine-worker registers to debusine-server.

``list_tokens``
~~~~~~~~~~~~~~~

The command ``list_tokens`` lists all tokens by default. It is possible
to filter tokens by the owner or the token itself, using the options ``--owner``
or ``--token``, for example:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin list_tokens --owner OWNER
  $ sudo -u debusine-server debusine-admin list_tokens --token TOKEN

You can get a machine-readable version of this output with the ``--yaml`` option.

``delete_tokens``
~~~~~~~~~~~~~~~~~

Tokens can be removed using the ``delete_tokens`` command. By default, it asks
for interactive confirmation unless ``--yes`` is used. See the options using:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin delete_tokens --help

Administrative commands
-----------------------

.. _command-delete-expired:

``delete_expired``
~~~~~~~~~~~~~~~~~~

Delete expired resources

.. code-block:: console

  $ sudo -u debusine-server debusine-admin delete_expired

The ``debusine-server`` package installs a systemd timer to run this
command daily.

.. _command-vacuum-storage:

``vacuum_storage``
~~~~~~~~~~~~~~~~~~

Perform regular maintenance on Debusine's storage.

.. code-block::

  $ sudo -u debusine-server debusine-admin vacuum_storage

The ``debusine-server`` package installs a systemd timer to run this
command daily.
