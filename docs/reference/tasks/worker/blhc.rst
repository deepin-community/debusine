.. _task-blhc:

Blhc task
---------

A task to represent a build log check using the ``blhc`` utility.

The ``task_data`` associated to this task can contain the following keys:

* ``input`` (required): a dictionary describing the input data

  * ``artifact`` (:ref:`lookup-single`, required): a
    ``debian:package-build-log`` artifact corresponding to the build log to
    be checked. The file should have a ``.build`` suffix.

* ``extra_flags`` (optional): a list of flags to be passed to the blhc
  command, such as ``--bindnow`` or ``--pie``. If an unsupported flag is
  passed then the request will fail.

The ``blhc`` output will be provided as a new artifact of category
``debian:blhc``, described :ref:`in the artifacts reference <artifact-blhc>`.

The task returns success if ```blhc``` returns an exit code of 0 or 1, and
failure otherwise.

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.blhc::Blhc.build_dynamic_data
