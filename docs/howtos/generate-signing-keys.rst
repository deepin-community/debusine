=====================
Generate signing keys
=====================

After :ref:`setting up a signing worker <set-up-debusine-signing>`, you may
want to generate signing keys for use by tasks.  (See also
:ref:`configure-hsm` for high-value keys.)

This process currently requires administrative access to the server.  In
future it may be made available via a workflow or run automatically for new
workspaces.

.. code-block:: console

   $ sudo -u debusine-server \
       debusine-admin create_work_request signing generatekey --data - <<END
   purpose: uefi
   description: Some description of the new key
   END

This generates a new key and stores it as an asset. You can find the
fingerprint of the new key by looking up the work request in the web
interface.
