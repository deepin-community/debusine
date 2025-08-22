.. task:: Sign

Sign task
---------

This is a :ref:`signing task <task-type-signing>` that signs the contents of
:artifact:`debusine:signing-input` artifacts on a signing worker.

The ``task_data`` for this task may contain the following keys:

* ``purpose`` (required): the purpose of the key to sign with: ``uefi``
  or ``openpgp`` (needed separately from ``key`` so that the
  scheduler can check whether the worker has the necessary tools available)
* ``unsigned`` (:ref:`lookup-multiple`, required): the
  :artifact:`debusine:signing-input` artifacts whose contents should be
  signed
* ``key`` (string, required): the fingerprint of a
  :asset:`debusine:signing-key` asset to sign with; must match ``purpose``

The output will be provided as :artifact:`debusine:signing-output`
artifacts, each of which is related to the corresponding
:artifact:`debusine:signing-input` artifact.
