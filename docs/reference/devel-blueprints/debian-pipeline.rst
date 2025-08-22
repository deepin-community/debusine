===============
Debian pipeline
===============

See the currently-implemented parts of :workflow:`debian_pipeline`.

.. task:: CheckInstallability

CheckInstallability task
========================

This is a server-side task that checks whether the uninstallability count in
a suite increases as a result of adding packages to it, along the lines of
the installability regression tests performed by `britney
<https://release.debian.org/doc/britney/>`_.

The ``task_data`` for this task may contain the following keys:

* ``suite`` (:ref:`lookup-single`, required): the :collection:`debian:suite`
  collection to check installability against
* ``binary_artifacts`` (:ref:`lookup-multiple`, required): a list of
  :artifact:`debian:binary-package`, :artifact:`debian:binary-packages`, or
  :artifact:`debian:upload` artifacts to check

.. todo::

    Check whether it's feasible to implement this in Debusine itself as a
    server-side task.  If not, we'll need to make it a worker task and
    consider what environment it should run in.

.. todo::

    Define the output.  It should probably be a new artifact category,
    produced only if the task fails, containing the list of
    newly-uninstallable packages.

.. task:: Confirm

Confirm task
============

This :ref:`wait task <task-type-wait>` blocks a workflow until a user confirms that the
workflow can continue. Users who can confirm are either the creator
of the work request/parent workflow, or owners of the workspace.
This task is equivalent to a confirmation prompt in a traditional user
interface.

Task data
---------

* ``auto_confirm_if_no_failure`` (boolean, defaults to False): when this
  parameter is set, the wait task is immediately marked as successful
  if all its dependencies are successful. It is useful to generate a
  confirmation prompt only when something has gone wrong and we want to
  offer the possibility to recover from it, instead of aborting the rest
  of the dependency chain.
* ``do_not_confirm_action`` (string, defaults to 'abort-workflow'): allowed
  values are 'failure', 'abort', 'abort-workflow'. For 'failure', the
  wait work request is completed with result set to FAILURE. For 'abort', the
  wait work request is "terminated" with status set to ABORTED. For
  'abort-workflow', the root workflow is aborted.
* ``confirm_label`` (string, defaults to 'Continue the workflow'): the label
  to show in the "Confirm" button
* ``do_not_confirm_label`` (string, defaults to "Abort the workflow"): the
  label to show in the "Do not confirm" button.

.. tip::

   Maybe the ``auto_confirm_if_no_failure`` behaviour can be implemented
   with an automatically generated ``on_unblock`` event reaction.

UI changes
----------

When showing a running :task:`Confirm` work request, the web UI shows
two buttons with the labels provided in the task data. It also shows
a "Comment" field where the user can input some text justifying the
choice made.

When one of the buttons is clicked, it is equivalent to calling
the ``confirm`` API view described below, with either ``confirm: True``
or ``confirm: False`` depending on the button clicked.

When showing a completed/aborted work request, the web UI displays:

* the user who took the decision
* the actual decision taken (show the label of the button "clicked"?)
* the timestamp of the confirmation
* the action performed as a result

API changes
-----------

A new ``/api/1.0/work-request/<int:work_request_id>/confirm/`` POST view
allows handling the new :task:`Confirm` wait task.  The view takes two
parameters:

* ``confirm`` (boolean, defaults to True): True indicates a confirmation,
  False a refusal to confirm
* ``comment`` (string, defaults to empty string): a textual message
  justifying the decision

The view works as follows:

* it checks that the work request is ``WAIT/Confirm`` and is running, and
  otherwise returns HTTP 400
* it checks that the requesting user has the permissions to confirm (creator
  or workspace owner), and otherwise returns HTTP 403
* when ``confirm`` is True, it terminates the work request with
  status=COMPLETED and result=SUCCESS
* when ``confirm`` is False, the behaviour is dictated by the
  ``do_not_confirm_action`` task data (see above)
* the ``output_data`` field of the work request is updated (so that
  the web UI can show information about who confirmed what & when, etc.)
* it returns HTTP 200
