.. task:: MakeSourcePackageUpload

MakeSourcePackageUpload task
----------------------------

This worker task makes a :artifact:`debian:upload` artifact from a
:artifact:`debian:source-package` artifact.  This involves unpacking the
source package and running ``dpkg-genchanges`` on it.

The ``task_data`` for this task may contain the following keys:

* ``input`` (required): a dictionary describing the input data:

  * ``source_artifact`` (:ref:`lookup-single`, required): a
    :artifact:`debian:source-package` artifact

* ``since_version`` (string, optional): include changelog information
  from all versions strictly later than this version in the
  ``.changes`` file; the default is to include only the topmost
  changelog entry

* ``target_distribution`` (string, optional): override the target
  ``Distribution`` field in the ``.changes`` file to this value; the
  default is to use the distribution from the topmost changelog entry

* ``environment`` (:ref:`lookup-single` with default category
  :collection:`debian:environments`, required):
  :artifact:`debian:system-tarball` artifact that will be used to run
  ``dpkg-source`` and ``dpkg-genchanges`` using the ``unshare`` backend.

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.makesourcepackageupload::MakeSourcePackageUpload.build_dynamic_data

The output is a :artifact:`debian:upload` artifact, with ``extends`` and
``relates-to`` relationships to the input source package artifact (to
match the behaviour of ``debusine import-debian-artifact``).

Used by the :workflow:`package_upload` workflow.
