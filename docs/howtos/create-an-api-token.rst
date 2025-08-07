.. _create-api-token:

===================
Create an API token
===================

As a Debusine user
------------------

If you have regular user access to Debusine, you can generate your own token.

Automatic enrollment
~~~~~~~~~~~~~~~~~~~~

With ``debusine-client`` version 0.10.0 or newer, you can run ``debusine
setup``.  If the server is one of those listed, you can select its number;
otherwise, select "new" and enter its name or URL.  Then select "save" and
follow the prompts to confirm registration.

Manual generation
~~~~~~~~~~~~~~~~~

With older versions of ``debusine-client``, you need to connect to the web
interface of your Debusine instance (e.g. ``http://debusine.internal/`` or
https://debusine.debian.net/), authenticate yourself, click on "Tokens" on
your user menu at the top right of the page, then follow the instructions to
create a new token.

You can enter a comment to document the expected use of the token and
decide if you want to enable it immediately or not. After submission,
you will receive the generated token, which you should save immediately
as the token won't be shown again.

As a Debusine administrator
---------------------------

If you are a Debusine administrator, then you can rely on the
:ref:`debusine-admin <debusine-admin-cli>` command to create a new token
for any user:

.. code-block:: console

  $ sudo -u debusine-server debusine-admin create_token john-smith --comment "Testing Debusine"

The token will be printed to the standard output. The token can then be sent to
the user.

Next steps
----------

The token will generally be used to :ref:`configure the Debusine client
<set-up-debusine-client>`.
