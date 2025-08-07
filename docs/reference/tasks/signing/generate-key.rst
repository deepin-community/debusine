.. _task-generate-key:

GenerateKey task
----------------

This is a :ref:`signing task <task-type-signing>` that generates a new key
on a signing worker and stores it for later use.

The ``task_data`` for this task may contain the following keys:

* ``purpose`` (required): the purpose of the key to generate: ``uefi``,
  or ``openpgp``.
* ``description`` (required): A text string with a human-readable
  description of the new key's intended purpose.

The output will be provided as a :ref:`debusine:signing-key
<asset-signing-key>` asset.
You can find the output asset with the debusine client:

.. code-block:: console

    $ debusine list-assets --work-request $work_request_id

.. todo::

   This will need additional parameters once we start supporting HSMs.
