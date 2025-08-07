.. _task-sign:

Sign task
---------

This is a :ref:`signing task <task-type-signing>` that signs the contents of
:ref:`debusine:signing-input <artifact-signing-input>` artifacts on a
signing worker.

The ``task_data`` for this task may contain the following keys:

* ``purpose`` (required): the purpose of the key to sign with: ``uefi``
  or ``openpgp`` (needed separately from ``key`` so that the
  scheduler can check whether the worker has the necessary tools available)
* ``unsigned`` (:ref:`lookup-multiple`, required): the
  ``debusine:signing-input`` artifacts whose contents should be signed
* ``key`` (string, required): the fingerprint of a
  :ref:`debusine:signing-key <asset-signing-key>` asset to sign with;
  must match ``purpose``

The output will be provided as :ref:`debusine:signing-output
<artifact-signing-output>` artifacts, each of which is related to the
corresponding ``debusine:signing-input`` artifact.
