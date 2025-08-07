.. _bare-data-historical-task-run:

Category ``debusine:historical-task-run``
=========================================

This bare data item is added to a :ref:`debusine:task-history
<collection-task-history>` collection to store statistics about a task run.

* Data:

  * the mandatory classification fields defined by the
    :ref:`debusine-task-history <collection-task-history>` collection
  * ``timestamp`` (required): the date and time (as a Unix timestamp — cf.
    ``date +%s``) when the task started
  * ``runtime_statistics`` (required): :ref:`runtime-statistics`, copied
    from the output data of the associated work request

Example data:

.. code-block:: yaml

    timestamp: 1722692645
    work_request_id: 12
    result: success
    runtime_statistics:
        duration: 6230
        cpu_time: 4300
        disk_space: 14780131
        memory: 344891034
        available_disk_space: 12208271360
        available_memory: 32839598080
        cpu_count: 4
