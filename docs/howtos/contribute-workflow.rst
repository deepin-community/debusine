.. _contribute-workflow:

Contribute a new workflow
=========================

:ref:`explanation-workflows` are Debusine's most powerful feature, and we
expect other people to have ideas of interesting workflows to try out.
Eventually we want to provide some kind of domain-specific language to
create custom workflows directly in the system, but in the meantime you are
welcome to :ref:`contribute <contribute-to-debusine>` your own workflows and
we will help you as needed.

Design
------

All new workflows need a :ref:`blueprint <design-with-blueprints>`.  This
should at minimum detail what task data the workflow accepts and what child
work requests it creates; see :ref:`existing workflows
<available-workflows>` for examples of how to lay this out.  If your
workflow involves any new tasks, then the blueprint should also include
detailed descriptions of those in the same way.

It will save you time to :ref:`discuss <communication>` your plans with the
development team so that we can advise on what approaches are likely to work
most smoothly.  You can do this either before or after writing a draft
blueprint.

Task breakdown
~~~~~~~~~~~~~~

Start by thinking about how your workflow breaks down into :ref:`tasks
<explanation-tasks>`.  Some good questions to ask yourself are:

* Can they be offloaded to external workers (most tasks should be in this
  category), or do they need direct access to the Debusine database (perhaps
  because they involve analysing a :ref:`collection
  <explanation-collections>`)?
* How finely does the work need to be divided into individual tasks?
* Are there any :ref:`existing tasks <available-tasks>` that can help you
  solve parts of your problem?
* What artifacts will need to be passed between tasks, and what dependencies
  will you need to have between them?

Input to the workflow
~~~~~~~~~~~~~~~~~~~~~

You should define the task data that your workflow will accept.  This is
usually related to the task data accepted by the individual tasks that it
will create, but it may be higher-level and more opinionated.  A good rule
of thumb is that tasks express mechanism, while workflows express policy.

Workflow task data is constructed by combining workflow templates defined in
a workspace with user input, so it should be laid out in a way that will
allow workspace owners to use workflow templates to set useful rules for how
contributors may start workflows.  In particular, this means that while
workflow task data can be arbitrary data structures, you probably want to
lean towards a flat layout; top-level items not set in a workflow template
may be provided by the user who starts the workflow.

Implementation
--------------

Each workflow has a Pydantic model in
``debusine/server/workflows/models.py`` for its task data, and a
corresponding orchestrator class under ``debusine/server/workflows/``.  At
minimum, that class should look like this:

.. code-block:: python

    class FooWorkflow(Workflow[FooWorkflowData, BaseDynamicTaskData]):
        """Do stuff."""

        TASK_NAME = "foo"

        def build_dynamic_data(
            self, task_database: TaskDatabaseInterface
        ) -> BaseDynamicTaskData:
            """Compute dynamic data for this workflow."""
            return BaseDynamicTaskData(subject=...)

        def populate(self) -> None:
            """Create work requests."""

        def get_label(self) -> str:
            """Return the task label."""
            return "..."

The ``build_dynamic_data`` method is typically less complex for workflows
than for tasks.  In the workflow case, it usually just needs to compute a
reasonable ``subject`` for a workflow instance based on ``self.data`` (see
:ref:`task-configuration`) and perhaps also ``parameter_summary`` describing
the most important parameters to the workflow for display in the web UI.

The ``populate`` method does the bulk of the orchestrator's work.  Usually,
it looks up any artifacts or collections needed from ``self.data``, decides
which child work requests it needs to create, and calls
:py:meth:`self.work_request_ensure_child
<debusine.server.workflows.base.Workflow.work_request_ensure_child>` to
create them.  It may also call :py:meth:`self.provides_artifact
<debusine.server.workflows.base.Workflow.provides_artifact>` and/or
:py:meth:`self.requires_artifact
<debusine.server.workflows.base.Workflow.requires_artifact>` to indicate
dependencies between work requests in the workflow; take care that the
``name`` passed to ``self.provides_artifact`` is unique across all internal
collection items under the same root workflow.

If your workflow creates sub-workflows, then it is your workflow's
responsibility to run their orchestrators in turn.  That normally looks like
this:

.. code-block:: python

    def populate(self) -> None:
        wr = self.work_request_ensure_child(...)
        wr.mark_running()
        orchestrate_workflow(wr)

In some exceptional cases the sub-workflow's orchestrator may not be able to
work out which child work requests to create until a dependency has
completed.  In such cases, the ``populate`` method should instead have this
sort of structure:

.. code-block:: python

    def populate(self) -> None:
        wr = self.work_request_ensure_child(task_type=TaskTypes.WORKFLOW, ...)
        self.requires_artifact(wr, ...)
        if wr.status == WorkRequest.Statuses.PENDING:
            wr.mark_running()
            orchestrate_workflow(wr)

The ``get_label`` method returns a string used as a label for a workflow
instance in the web UI.

You may find it helpful to consult some existing implementations for
inspiration.  The :workflow:`lintian` workflow
(``debusine/server/workflows/lintian.py``) is a relatively simple example
that demonstrates some of the points here.

Documentation
-------------

Once you have implemented your workflow, make sure to move the corresponding
documentation from the blueprint into the main documentation, usually under
``docs/reference/workflows/specs/``.
