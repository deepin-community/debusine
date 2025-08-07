.. _work-request-scheduling:

=======================
Work request scheduling
=======================

Lifecycle of a user-submitted work request
==========================================

When a work request gets submitted by a user, Debusine records it with a
``pending`` status and with no worker assigned. The list of pending work
requests constitutes the queue of work requests that are waiting to be
processed.

When the Debusine scheduler finds a suitable worker (it must be idle and
must match the requirements defined by the planned task), the work
request is assigned to the worker and the worker is notified of the
availability of a new work request to process. When the worker starts
to process the work request, the status is updated to ``running``.

.. note::

   At any point in time, there is at most one (pending or running) work
   request assigned to a given worker.

When the worker has finished to process the work request, and after
having sent back the results and uploaded generated artifacts, the status
is updated to ``completed``.

The ``aborted`` status is a special case, it can only be set by the
submitter, by an administrator or by the scheduler when the pre-requisites
are not met. It is the official way to cancel a work request.

A failed work request can be retried, in which case a new work request is
created superseding the old one, its dynamic task data is recomputed with new
lookups to update references to artifacts like build environments, and previous
work request dependencies are updated to point to the new one, effectively
replacing it. The superseded work request will be kept for inspection.

Lifecycle of a work request inside a workflow
=============================================

When a workflow creates work requests, it will typically create
dependencies between them. When a work request has a dependency
against a work request that is not yet completed, it is put in the
``blocked`` status.

The scheduler will move the work request to the ``pending`` status
only when all the dependent work requests have successfully completed
their work. When a dependent work request has failed (and when it was not
allowed to fail), the work request will be marked as ``aborted``.

Priorities
==========

Work requests have a base priority and a priority adjustment.  The former is
set automatically, and the latter by administrators.  The effective priority
of a work request is the sum of its base priority and its priority
adjustment.  The scheduler considers eligible tasks in descending order of
effective priority.

The base priority of a work request is normally set by a workflow template
or a workflow orchestrator.  Failing that, it defaults to the effective
priority of the parent work request (computed at creation time).  If there
is no parent work request, it defaults to 0.

Workflow templates have a priority, which is used as the initial base
priority for work requests created from that template.  This can be set by
administrators, and it is expected that workflows used by automated QA tasks
would be given a negative priority.

When workflow orchestrators lay out an execution plan, they may adjust the
base priority of each resulting work request relative to the parent work
request's effective priority.  For example, an orchestrator planning several
different kinds of tasks might choose to give quicker static analysis tasks
a slightly higher base priority than slower dynamic analysis tasks.

Separating the priority adjustment from the base priority allows us to tell
more easily when effective priorities have been adjusted manually.

Users with the ``db.change_workrequest`` permission (including superusers)
can use ``debusine manage-work-request --set-priority-adjustment ADJUSTMENT
WORK_REQUEST_ID`` to adjust a work request's priority.

Ordering of work request
========================

The scheduler handles the queue of pending work requests in descending order
of effective priority, breaking ties by chronological order of creation
(first in, first out).

.. note::

   This doesn't mean that all work requests will be processed in that
   priority order because they can have different requirements for workers.
   If work request N has no suitable worker available, but N+1 has one
   worker available, then N+1 will start before N.

Matching of workers and work requests
=====================================

Every time that a worker completes a work request, the scheduler kicks in
and tries to find a suitable *next work request* for that worker.

The scheduler builds a dictionary-based description of that worker by
combining static metadata (set by the administrators) and dynamic metadata
(returned by the worker themselves). The key/values from the static metadata
take precedence over those provided by the dynamic metadata.

A first filter is made by:

* excluding work requests whose ``task_name`` are listed in the ``tasks_denylist`` metadata
* selecting work requests whose ``task_name`` are listed in the ``tasks_allowlist`` metadata

Then a second — work-request specific — filter is made by the scheduler.
For each work request, the scheduler builds the ``Task`` object out of the
work request and runs ``task.can_run_on(worker_metadata)`` to verify if
the work request can run on that worker.

If there are remaining work requests, then they are deemed to be
suitable for that worker and the oldest of those work requests is assigned
to that worker.

Management of architecture-specific tasks
=========================================

Many work requests have to run on worker of a specific architecture (or on
a worker that is compatible with that architecture). This selection is done
as part of the ``task.can_run_on`` method and relies on the
``system:architectures`` metadata key.

That key is set by default to a list with a single item containing the host
architecture (as returned by ``dpkg --print-architecture``).

See :ref:`howto-configure-worker-architectures` for detailed instructions on how to
configure the appropriate metadata.

Dynamic worker provisioning
===========================

When :ref:`dynamic-worker-pools` are available, workers can be spun up
in response to demand.
An estimated execution latency is calculated for pending tasks, and if
it exceeds the configured ``target_latency_seconds`` limit, then
additional dynamic workers will be provisioned.

Once the queue is exhausted and dynamic workers have been sitting idle
for ``max_idle_seconds``, they will be terminated.

See :ref:`howto-add-cloud-worker-pool` for detailed instructions on how
to configure dynamic workers.
