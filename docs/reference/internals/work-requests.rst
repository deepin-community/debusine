.. _work-requests:

=============
Work Requests
=============

See :ref:`explanation-work-requests` for a detailed overview.

Technically, scheduled :ref:`workflows <workflow-reference>` are Work
Requests with children work requests (see the ``parent`` field).

Work Requests have the following important properties:

* task_type: the type of the task (``Worker``, ``Server``, ``Internal``, or
  ``Workflow``; see :ref:`explanation-tasks`)
* task_name: the name of the task to execute (used to figure out the
  Python class implementing the logic)
* task_data: a JSON dict representing the input parameters for the task
* dynamic_task_data: a JSON dict representing derived information about the
  task, set by ``compute_dynamic_data`` based on its ``task_data``.
  Computing this field may involve resolving artifact lookups.  One special
  key is always supported here:

  * ``parameter_summary`` (optional): a string describing the most important
    parameters to this work request (for example, a workflow to build and test
    version ``1.0-1`` of the ``hello`` source package might have the parameter
    summary ``hello_1.0-1``).  This can be used in visual representations of
    the work request.

* status: the processing status of the work request. Allowed values are:

  * blocked: the task is not ready to be executed
  * pending: the task is ready to be executed and can be picked up by a
    worker
  * running: the task is currently being executed by a worker
  * aborted: the task has been cancelled/aborted
  * completed: the task has been completed

* result: the processing result. Allowed values are:

  * success: the task completed and succeeded
  * failure: the task completed and failed
  * error: an unexpected error happened during execution

* workspace: foreign key to the workspace where the task is executed
* worker: foreign key to the assigned worker (is NULL while
  work request is pending or blocked)
* unblock_strategy: specify how the work request can move from
  ``blocked`` to ``pending`` status. Supported values are:

  * ``deps``: the work request can be unblocked once all the dependent
    work requests have completed
  * ``manual``: the work request must be manually unblocked

* parent: workflow hierarchy tree (optional, NULL when scheduled
  outside of a workflow); foreign key to the containing
  WorkRequest. The parent hierarchy will eventually reach a top-level
  node of type ``WORKFLOW`` which is the node that manages this
  ``WorkRequest`` hierarchy, possibly including sub-workflows. See
  :ref:`Workflows <explanation-workflows>`.
* dependencies: order of execution within a workflow hierarchy
  (optional, hierarchy is run in parallel unless otherwise
  constrained); ManyToMany relation with other ``WorkRequest`` (from
  the same workflow) that need to complete before this one can be
  unblocked (if using the ``deps`` unblock_strategy)
* workflow_template: for workflows created from a workflow template, foreign
  key to that template; may be set to NULL if the template is deleted

* workflow_data: JSON dict controlling some workflow specific
  behaviour; it is expected to support the following keys:

  * ``allow_failure`` (optional, defaults to False): boolean
    indicating what to do with the parent workflow if the work request
    fails. If true, the workflow can continue, otherwise the workflow
    is interrupted.

  * ``display_name``: name of the step in the visual representation of
    the workflow

  * ``step``: internal identifier used to differentiate multiple
    workflow callbacks inside a single workflow.  It acts like a
    machine equivalent for ``display_name``, to allow the orchestrator
    to encode the plan about what it is supposed to do at this point
    in the workflow.

  * ``group`` (optional): name of the :ref:`group <workflow-group>`
    within this workflow containing this work request.

  * ``workflow_template_name`` (optional): for workflows created from a
    workflow template, the name of that template, cached here for
    convenience.

* event_reactions: JSON dict describing actions to perform in response to
  specific events.
* internal_collection: (only for workflow work requests): reference to a
  ``debusine:workflow-internal`` collection (see
  :ref:`collection-workflow-internal`) that holds artifacts produced during
  this workflow
* expiration_delay: retention time for this work request in the database
* supersedes: optional work request that has been superseded by this one: this
  is used to track previous attempts when retrying tasks.

Blocked work requests using the ``deps`` unblock strategy may have
dependencies on other work requests. Those are only used to control
the order of execution of work requests inside workflows: the
scheduler ignores ``blocked`` work requests and only considers
``pending`` work requests. The ``deps`` unblock strategy will change
the status of the work request to ``pending`` when all its dependent
work requests have completed.
