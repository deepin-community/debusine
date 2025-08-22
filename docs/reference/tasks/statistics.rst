===============
Task statistics
===============

We store meta-data about previous runs of various tasks.  Runtime data of
former runs can be used to:

* select a powerful worker when the task is resource hungry (or the
  opposite)
* decide whether a failure is fatal based on the existence of previous
  successful runs
* etc.

Output data
===========

The ``WorkRequest`` model has a JSON field named ``output_data``, set upon
completion of the work request.  The values are provided by the worker.

Its structure is as follows:

* ``runtime_statistics``: see :ref:`runtime-statistics` below.
* ``errors``: a list of errors.  Each error is a dictionary with the
  following keys:

  * ``message``: user-friendly error message
  * ``code``: computer-friendly error code

  .. note::

    Typically used to return validation/configuration errors to the user
    that resulted in the task not being run at all.  Other additional keys
    might be set depending on the error code.

    This ``errors`` key is not required for the design that we are doing
    here, but it explains why I opted to create an ``output_data`` field
    instead of a ``runtime_statistics`` field.  See :issue:`432` for a
    related issue that we could fix with this.

* ``skip_reason``: may be set to a human-readable explanation of why this
  work request was skipped rather than being run normally.

.. _runtime-statistics:

``RuntimeStatistics`` model
---------------------------

The model combines runtime data about the task itself:

* ``duration`` (optional, integer): the runtime duration of the task in
  seconds
* ``cpu_time`` (optional, integer): the amount of CPU time used in seconds
  (combining user and system CPU time)
* ``disk_space`` (optional, integer): the maximum disk space used during the
  task's execution (in bytes)
* ``memory`` (optional, integer): the maximum amount of RAM used during the
  task's execution (in bytes)

But also some data about the worker to help analyze the values and/or to
provide reference data in the case of missing runtime data:

* ``available_disk_space`` (optional, integer): the available disk space
  when the task started (in bytes, may be rounded)
* ``available_memory`` (optional, integer): the amount of RAM that was
  available when the task started (in bytes, may be rounded)
* ``cpu_count`` (optional, integer): the number of CPU cores on the worker
  that ran the task

Open question: how and where to use the statistics
==================================================

In theory, the statistics might only be available when the task becomes
pending when we have the final result for ``compute_dynamic_data()`` and
the guarantee to have values for subject/context.

If we want to use those statistics to tweak the configuration of the work
request (i.e. adding new worker requirements), then it needs some careful
coordination between the scheduler and the workflow.

In practice, many workflows will know the subject/context values by
advance and can possibly configure the work request at creation time.
