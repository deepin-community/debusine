.. task:: SignRepositoryIndex

SignRepositoryIndex task
------------------------

This is a :ref:`signing task <task-type-signing>` that signs a
:artifact:`debian:repository-index` artifact on a signing worker.  It is
separate from the :task:`Sign` task to make it easier to add the signed
indexes to the suite without needing additional helper tasks.

The ``task_data`` for this task may contain the following keys:

* ``suite_collection`` (:ref:`lookup-single`, required): the
  :collection:`suite <debian:suite>` whose indexes should be signed
* ``unsigned`` (:ref:`lookup-single`, required): the
  :artifact:`debian:repository-index` artifact whose contents should be
  signed
* ``mode`` (required): ``detached`` or ``clear``
* ``signed_name`` (required): the file name to use in the output artifact

The task looks up the suite's ``signing_keys`` field; if that is not set, it
falls back to the ``signing_keys`` field of its containing archive, if any.
It signs the unsigned artifact with all the signing keys corresponding to
fingerprints in that list, creating a single signed output file.

The output will be provided as a :artifact:`debian:repository-index`
artifact, with ``relates-to`` relations to the unsigned artifact.
