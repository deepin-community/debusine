.. collection:: debusine:task-history

Category ``debusine:task-history``
----------------------------------

This :ref:`singleton collection <collection-singleton>` helps to find
previous runs of a given task that used similar input parameters and is
expected to have a similar behaviour.

To correctly represent the history of a large number of task runs,
the bare data item always has the following fields:

* ``task_type`` (required): the ``task_type`` of the work request for
  which we want to keep statistics
* ``task_name`` (required): the ``task_name`` of the work request for
  which we want to keep statistics
* ``subject`` (optional): an abstract string value representing the
  *subject* of the task. It is meant to group possible inputs into
  groups that we expect to behave similarly.
* ``context`` (optional): an abstract string value representing the
  *runtime context* in which the task is executed. It is meant to represent
  some of the task parameters that can significantly alter the runtime
  behaviour of the task.
* ``work_request_id`` (required): the ID of the WorkRequest corresponding to
  the monitored task
* ``result`` (required): duplicates the string value of the result field of
  the associated WorkRequest

For example, for the ``sbuild`` task, ``subject`` would typically be
the source package name while ``context`` would be the name of the target
suite and the target architecture.

The subject and runtime context are computed dynamically by the task's
``compute_dynamic_data()`` method and thus stored in the corresponding
field.

The name of each item is
``TASK_TYPE:TASK_NAME:SUBJECT:CONTEXT:WORK_REQUEST_ID``.

Other collection-specific characteristics:

* Data:

  * ``old_items_to_keep``: number of old items to keep. Defaults to 5.
    For each subject/context combination, the collection always keeps the
    last success, the last failure, and a given number of most recent
    entries. The cleanup is automatically done when adding new items.

    .. note::

        At some point, we may need more advanced logic than this, for
        instance to clean up statistics about packages that are gone
        from the corresponding suite.

* Valid items:

  * :bare-data:`debusine:historical-task-run` bare data

* Lookup names:

  * ``last-entry:TASK_TYPE:TASK_NAME:SUBJECT:CONTEXT`` returns the most
    recently added entry for the specific combination of
    task/subject/context.
  * ``last-success:TASK_TYPE:TASK_NAME:SUBJECT:CONTEXT`` returns the most
    recently added entry where ``result`` is ``success`` for the specific
    combination of task/subject/context.
  * ``last-failure:TASK_TYPE:TASK_NAME:SUBJECT:CONTEXT`` returns the most
    recently added entry where ``result`` is ``failure`` or ``error`` for
    the specific combination of task/subject/context.

* Multiple lookup filters:

  * ``same_work_request``: given a :ref:`lookup-multiple`, return conditions
    matching task runs for the same work request as any of the resulting
    artifacts
  * ``same_workflow``: given a :ref:`lookup-multiple`, return conditions
    matching task runs for work requests from the same workflow as any of
    the resulting artifacts

* Constraints:

  * None.
