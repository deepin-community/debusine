.. _set-up-debusine-client:

======================
Set up debusine-client
======================

Introduction
------------

Install the ``debusine-client`` package which provides the ``debusine``
command.

If needed configure APT with the appropriate :ref:`package repository
<debusine-package-repositories>`.

The :ref:`debusine command <debusine-cli>` is used, among other things,
to submit work requests to the Debusine server and to look into the status
of the requests.

Initial setup
-------------

The Debusine client needs the Debusine server URL and an enabled token for
the server (see :ref:`create-api-token`).  With ``debusine-client`` version
0.10.0 or newer, ``debusine setup`` will create this for you.  Otherwise,
you can create it manually as follows:

  #. Create the directory for the ``config.ini`` file:

     .. code-block:: console

       $ mkdir --parents "$HOME/.config/debusine/client"

  #. Copy the example ``config.ini`` file to this directory:

     .. code-block:: console

       $ cp /usr/share/doc/debusine-client/examples/config.ini "$HOME/.config/debusine/client/"

  #. Edit the ``config.ini`` file:

     .. code-block:: console

       $ editor "$HOME/.config/debusine/client/config.ini"
  
  #. Rename ``[server:localhost]`` to ``[server:server_name]`` and set
     ``default-server = server_name``. The Debusine client supports multiple
     servers, one of which is a default server. It is possible to specify to which server
     the Debusine client connects using the argument ``--server NAME``.

  #. Set the ``api-url``, ``scope`` and ``token``.

Test the configuration
----------------------

Run the following command to ensure that the Debusine client can
successfully authenticate with the server:

.. code-block:: console

   $ echo "result: true" | debusine create-work-request noop
   result: success
   message: Work request registered on https://debusine.example.org/api with id 1.
   work_request_id: 1

If you don't see ``result: success``, then you likely have done something
wrong. If the output doesn't clearly tell you what went wrong, you can
rerun the command with ``--debug`` to have more details. You can also
check the Debusine server logs to see if an error has been reported.
