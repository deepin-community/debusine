.. _task-debdiff:

DebDiff task
------------

A task to compute the differences between two source or binary Debian packages using the ``debdiff`` utility.

The ``task_data`` associated to this task can contain the following keys:

* ``input`` (required): a dictionary describing the input data

  * ``source_artifacts`` (optional): a list with two elements (original, new).
    Each element is a :ref:`lookup-single` pointing to an artifact of
    category ``debian:source-package``.
  * ``binary_artifacts:`` (optional): a list with two elements (original, new).
    Each element is a :ref:`lookup-multiple` pointing to artifacts of
    category ``debian:upload``.

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.debdiff::DebDiff.build_dynamic_data


.. note::

  Note that exactly one of ``source_artifacts`` or ``binary_artifacts`` is required.

* ``extra_flags`` (optional): a list of flags to be passed to the debdiff command, such as ``--nocontrol`` or ``--diffstat``.
  If an unsupported flag is passed then the request will fail.

* ``environment`` (:ref:`lookup-single` with default category
  ``debian:environments``, required): artifact that will be used to run
  ``debdiff`` (it will be installed if necessary).

* ``host_architecture`` (required): the architecture that we want to run
  ``debdiff``.

The ``debdiff`` output will be provided as a new artifact of category ``debian:debdiff``, described in the :ref:`artifacts reference <artifact-debdiff>`.

The task returns success if ``debdiff`` returns an exit code of 0 or 1, and failure otherwise.
