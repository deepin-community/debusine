===============
Debian pipeline
===============

See the currently-implemented parts of :ref:`debian-pipeline <workflow-debian-pipeline>`.

.. _task-check-installability:

CheckInstallability task
========================

This is a server-side task that checks whether the uninstallability count in
a suite increases as a result of adding packages to it, along the lines of
the installability regression tests performed by `britney
<https://release.debian.org/doc/britney/>`_.

The ``task_data`` for this task may contain the following keys:

* ``suite`` (:ref:`lookup-single`, required): the ``debian:suite``
  collection to check installability against
* ``binary_artifacts`` (:ref:`lookup-multiple`, required): a list of
  ``debian:binary-package``, ``debian:binary-packages``, or
  ``debian:upload`` artifacts to check

.. todo::

    Check whether it's feasible to implement this in Debusine itself as a
    server-side task.  If not, we'll need to make it a worker task and
    consider what environment it should run in.

.. todo::

    Define the output.  It should probably be a new artifact category,
    produced only if the task fails, containing the list of
    newly-uninstallable packages.

.. _task-confirm:

Confirm task
============

This wait task blocks until the user who created it says that it may
proceed.  It is equivalent to a confirmation prompt in a traditional user
interface.  It has no task data.

API changes
===========

A new ``/api/1.0/work-request/<int:work_request_id>/confirm/`` view allows
handling the new ``Confirm`` wait task.  The view works as follows:

* check that the work request is ``WAIT/Confirm`` and is running, and
  otherwise return HTTP 400
* check that the request user is the same as the user who created the work
  request, and otherwise return HTTP 403
* mark the work request completed
* return HTTP 200

UI changes
==========

When showing a blocked ``Confirm`` work request, the web UI shows a "Confirm"
button which does the equivalent of the ``confirm`` API view above.
