.. _workflow-orchestration:

Workflow orchestration
======================

Each :ref:`workflow <explanation-workflows>` is controlled by a subclass of
``Workflow``, which runs on the server with full database access.  It is in
charge of "orchestrating" the workflow by doing the following:

* laying out an initial execution plan, as a directed acyclic graph of new
  ``WorkRequest`` instances (via its :py:meth:`populate
  <debusine.server.workflows.base.Workflow.populate>` method)
* optionally, being called back later to modify its graph after analysing
  the current state of its work requests (via its :py:meth:`callback
  <debusine.server.workflows.base.Workflow.callback>` method)

Workflows are themselves special kinds of work requests (with ``task_type``
set to ``"workflow"``), and may contain sub-workflows which are in charge of
parts of the graph.  The initial workflow is referred to as the "root
workflow".

The root workflow has a special :ref:`collection <explanation-collections>`
associated with it, referred to as the "internal collection", and shared
among all its sub-workflows.  This is used to coordinate sub-workflows,
allowing work requests to declare that they will provide certain kinds of
artifacts which may then be required by work requests in other
sub-workflows.

Dependencies
------------

Under a root workflow, work requests may depend on each other, including
work requests from different sub-workflows; but they may not depend on work
requests outside their root workflow.

Child work requests are normally created using the
:py:meth:`work_request_ensure_child
<debusine.server.workflows.base.Workflow.work_request_ensure_child>` method,
which helps to ensure that workflow population is idempotent.  It creates
work requests in the ``blocked`` status using the ``deps`` unblock strategy,
meaning that they will become ``pending`` once all their dependencies have
completed.

Sub-workflows
-------------

Advanced workflows can be created by combining multiple limited-purpose
workflows: for example, the :workflow:`reverse_dependencies_autopkgtest`
workflow figures out which source packages need to have tests run for them,
but creates instances of the :workflow:`autopkgtest` workflow to schedule
tests for each individual source package.

Sub-workflows are integrated in the general graph of their parent workflow
as work requests of type ``workflow``.  From a user interface perspective,
they are typically hidden as a single step in the visual representation of
the parent's workflow.

Cooperation between workflows is defined at the level of workflows.
Individual work requests should not concern themselves with this; they are
designed to take inputs using lookups and produce output artifacts that are
linked to the work request.

On the providing side, workflows use the
:ref:`action-update-collection-with-artifacts` event reaction to add
relevant output artifacts from work requests to the internal collection, and
create :bare-data:`promises <debusine:promise>` to indicate to other
workflows that they have done so.  Providing workflows choose item names in
the internal collection; it is the responsibility of workflow designers to
ensure that they do not clash, and workflows that provide output artifacts
have a optional ``prefix`` field in their task data to allow multiple
instances of the same workflow to cooperate under the same root workflow.
The :py:meth:`provides_artifact
<debusine.server.workflows.base.Workflow.provides_artifact>` method helps
with this.  Note that ``prefix`` may not contain ``/``, since that separates
lookup segments; the convention is to use ``|`` as a separator.

On the requiring side, workflows look up the names of artifacts they require
in the internal collection; each of those lookups may return nothing, or a
promise including a work request ID, or an artifact that already exists, and
they may use that to determine which child work requests they create.  They
use :ref:`lookups <lookup-syntax>` in their child work requests to refer to
items in the internal collection (e.g.
``internal@collections/name:build-amd64``), and add corresponding
dependencies on work requests that promise to provide those items.  The
:py:meth:`requires_artifact
<debusine.server.workflows.base.Workflow.requires_artifact>` method helps
with this.

Sub-workflows may depend on other steps within the root workflow while still
being fully populated in advance of being able to run.  A workflow that
needs more information before being able to populate child work requests
should normally depend on the work requests that will provide the
information it needs; failing that, it should use :ref:`workflow callbacks
<workflow-callback>` to run the workflow orchestrator again when it is
ready.  (For example, a workflow that creates a source package and then
builds it may not know which work requests it needs to create until it has
created the source package and can look at its ``Architecture`` field.)

Workflows themselves should not normally have dependencies, since that means
that their orchestrators cannot run and populate the work request graph in
advance.  The exception is where the workflow orchestrator itself needs some
information from some of its input artifacts in order to work out which
child work requests to create; in such cases the workflow itself should have
dependencies that mean the orchestrator does not run until that information
is available.  Otherwise, it is better for workflows to create child work
requests that have whatever dependencies are needed.
