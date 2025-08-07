==============
Add a new user
==============

With debusine-admin
-------------------

The Debusine administrator can use the :ref:`debusine-admin
<debusine-admin-cli>` command to create a new user:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin create_user john-smith john@example.com

The password for this user will be printed on the stdout.

The email is required and used to send notifications (e.g. when a job
fails) if the Debusine server is configured to send emails, a channel is
created on the server and the work request include the notification
settings.

The command ``manage_user`` can be used to change the email. The Django
native command ``changepassword`` can be used to change the password of
the user.

Automatic creation with Single Sign On support
----------------------------------------------

If you have configured :ref:`Single Sign On <configure-gitlab-sso>`, users
that can authenticate with the configured OIDC provider will have an
account automatically created upon their first login (this can be somewhat
controlled with the ``restrict`` parameter of the OIDC provider
configuration).
