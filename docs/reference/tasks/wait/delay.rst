.. _task-delay:

Delay task
----------

This :ref:`wait task <explanation-tasks>` completes after its
``delay_until`` timestamp.

The ``task_data`` for this task may contain the following keys:

* ``delay_until`` (datetime, required): a timestamp on or after which this
  task may be run

The ``workflow_data`` for this task contains ``needs_input: False``.

As with other wait tasks, the scheduler marks it running (without assigning
a worker) as soon as possible after it is pending, after which it is
considered to be waiting for its event to happen.  Separately, it handles
the completion of this particular task directly, by marking it as
successfully completed if the current time is greater than or equal to its
``delay_until`` timestamp.
