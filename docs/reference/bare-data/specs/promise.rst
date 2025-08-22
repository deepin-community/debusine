.. bare-data:: debusine:promise

Category ``debusine:promise``
=============================

This bare data item represents an artifact that will eventually be
provided as the output of some existing work request.  The bare data
item may contain additional properties of said artifact to help
differentiate them.  These items are created by :ref:`workflows
<explanation-workflows>` when they populate their work request graphs.

Workflows add promises to their internal collection using the same name as
they use for the expected artifact.

* Data:

  * ``promise_work_request_id`` (integer, required): the ID of the work
    request that is expected to fulfil this promise
  * ``promise_workflow_id`` (integer, required): the ID of the workflow work
    request that created this promise
  * ``promise_category`` (string, required): the category of the artifact
    that is expected to be provided

Other data items should match the per-item data structure used when adding
the promised artifact to the workflow's internal collection, which is
determined by the parent workflow based on its protocol for communicating
with other workflows.  This allows constructing lookups that match either a
promise or the promised artifact.  Names starting with ``promise_`` are
reserved.

When a work request is retried, it must update the work request ID in any
associated promises.
