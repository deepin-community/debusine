.. task:: MergeUploads

MergeUploads task
-----------------

This worker task combines multiple :artifact:`debian:upload` artifacts into
a single one, in preparation for uploading them together.  This involves
running ``mergechanges`` (from devscripts) on them, or equivalent.

The ``task_data`` for this task may contain the following keys:

* ``input`` (required): a dictionary describing the input data:

  * ``uploads`` (:ref:`lookup-multiple`, required): a list of
    :artifact:`debian:upload` artifacts

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.mergeuploads::MergeUploads.build_dynamic_data

The output is a :artifact:`debian:upload` artifact with ``extends``
relationships to each of the input upload artifacts.

Used by the :workflow:`package_upload` workflow.
