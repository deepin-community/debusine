.. _reference-task-types:

==========
Task types
==========

There are six types of tasks, each with its own peculiarities. But
they are all scheduled through work requests. That's why work requests
identify the precise task to execute with a combination of a ``task_type``
and a ``task_name`` value.

Worker tasks
============

``Worker`` tasks is the most common type of tasks. They run on external
workers, often within some controlled execution environments.  They may
execute untrusted code, such as building a source package uploaded by a
user.

Worker tasks can only interact with Debusine through the public API. Each
worker has a dedicated token that has the proper permissions to retrieve
the required artifacts and to upload the generated artifacts.

Worker tasks can require specific features from the workers on which they
will run. This can be used to ensure that the assigned worker:

* supports some specific architecture (when managing builders with
  different architectures)
* has enough memory
* has enough disk space
* has some specific executables
* etc.

.. _reference-execution-environment:

Execution environment
---------------------

Debusine supports multiple different virtualization backends to execute
``Worker`` tasks, from lightweight containers (e.g. ``unshare``) to VMs
(e.g. ``incus-vm``). Some tasks let you control the virtualization
backend to use through the ``backend`` parameter (in ``task_data``).

The currently supported backends are:

* ``unshare``: lightweight container built with /usr/bin/unshare
* ``incus-lxc``: LXC container managed by Incus
* ``qemu``: QEMU virtual machine
* ``incus-vm``: QEMU virtual machine managed by Incus

When tasks are executed in an executor backend, one of the task inputs
is an environment, an artifact containing a system image that the task
is executed in. These image artifacts are downloaded by the worker and
cached locally. For some backends (e.g. Incus) they'll be converted
and/or imported into an image store.

The worker maintains an LRU cache of up to 10 images. When cleaning up
images, they'll also be removed from any relevant image stores.

Server tasks
============

``Server`` tasks perform operations that require direct database access
and that may take some time to run. They run on Celery workers, and must
not execute any user-controlled code.

Since server tasks have database access, they can thus analyze their
parent workflow, including all the completed work requests and the
generated artifacts, they can also consume and generate runtime data
that will be available for other steps in the workflow (through the
internal collection associated with the workflow's root
``WorkRequest``).

Internal tasks
==============

``Internal`` tasks are used to structure workflows and represent
operations that are typically handled by the Debusine scheduler
itself. There are only two internal tasks currently:

.. _synchronization-point:

Synchronization points
----------------------

Internal tasks with ``task_name`` set to ``synchronization_point`` are
tasks that do nothing. Hence they also don't need any input data
and their associated ``task_data`` in a work request is an empty
dictionary.

Their main use is to provide synchronization points in a graph of blocked
work requests. In particular they can be used to represent the entry
or exit points of sub-workflows or of groups of related work requests.

When such a work request becomes pending, it is immediately marked as
completed by the scheduler, thus unblocking work requests that depend on
it.

This work request typically has a non-empty ``workflow_data`` explaining
its purpose and influencing the rendering of the workflow's visual
representation.

.. _workflow-callback:

Workflow callbacks
------------------

Internal tasks with ``task_name`` set to ``workflow`` are integrated
in strategic points of a workflow's graph of work requests to ask the
scheduler to re-run the workflow orchestrator when that work request
becomes executable.

.. note::

    In a work request, its associated ``task_data`` is an empty dictionary
    but the ``workflow_data`` dictionary must have a ``step`` key to
    identify the callback being executed.

    The workflow orchestrator to run is identified by following
    the ``parent`` relationship and looking up the ``task_name``
    (since the parent work request should be a workflow).

This gives the workflow orchestrator an opportunity to review the progress
of the workflow and to add additional work requests (or alter the
structure of the workflow) based on results of already-completed work
requests.

The orchestrator is run in a celery task and the associated internal
work request is marked as completed when the celery task has completed.

Workflow tasks
==============

``Workflow`` tasks represent a collection of other tasks; see
:ref:`explanation-workflows`.

.. _task-type-signing:

Signing tasks
=============

``Signing`` tasks are like ``Worker`` tasks, but run on restricted signing
workers. They typically interact with secret keys that required to perform
the requested operation.

.. _task-type-wait:

Wait tasks
==========

``Wait`` tasks represent steps in workflows where Debusine needs to wait
until something else happens (user interaction or some other part of
Debusine). The task name determines what Debusine is waiting for.

Completing such tasks can either involve an API call performed by some
user, or some sort of regular job that monitors something and marks the
task as completed when some criteria are met.

The ``needs_input`` field in a ``Wait`` task's workflow data is True if it
requires user input, otherwise False.
