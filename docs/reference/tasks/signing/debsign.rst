.. _task-debsign:

Debsign task
------------

This is a :ref:`signing task <task-type-signing>` that signs a
:ref:`debian:upload <artifact-upload>` artifact on a signing worker.  It is
separate from the :ref:`task-sign` because signing uploads is a customized
operation involving signing multiple files and possibly updating checksums
in the ``.changes`` file to match the signed versions of other files.

The ``task_data`` for this task may contain the following keys:

* ``unsigned`` (:ref:`lookup-single`, required): the ``debian:upload``
  artifact whose contents should be signed
* ``key`` (string, required): the fingerprint of the
  :ref:`debusine:signing-key <asset-signing-key>` asset to sign the
  upload with, which must have purpose ``openpgp``

The output will be provided as a :ref:`debian:upload
<artifact-upload>` artifact, with ``relates-to`` relations to the
unsigned artifact.

Used by the :ref:`package_upload workflow <workflow-package-upload>`.
