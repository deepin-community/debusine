.. _task-package-upload:

PackageUpload task
------------------

This server task uploads Debian packages to an upload queue.

It is the equivalent of running ``dput``, but since other parts of
Debusine ensure that the upload is well-formed, there's no need for
most of the complexity of ``dput`` and we can avoid needing an
environment for it.

The ``task_data`` for this task may contain the following keys:

* ``input`` (required): a dictionary describing the input data:

  * ``upload`` (:ref:`lookup-single`, required): a ``debian:upload``
    artifact

* ``target`` (required): the upload queue, as an ``ftp://`` or
  ``sftp://`` URL

* ``delayed_days`` (integer, optional): the number of days to delay this
  upload; this assumes that the upload queue implements Debian's convention
  of uploading delayed uploads to a ``DELAYED/{n}-day`` queue

The implementation should take care to use a suitable connection
timeout.  An SSH private key should be provided in the ``~/.ssh/``
directory of the user running Debusine.

Used by the :ref:`package_upload workflow <workflow-package-upload>`.
