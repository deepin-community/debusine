.. _workflow-lintian:

Workflow ``lintian``
====================

This workflow schedules Lintian checks for a single source package and its
binaries on a set of architectures.

* ``task_data``:

  * ``source_artifact`` (:ref:`lookup-single`, required): see
    :ref:`task-lintian`
  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): see
    :ref:`task-lintian`

  * ``vendor`` (string, required): the distribution vendor on which to run
    tests
  * ``codename`` (string, required): the distribution codename on which to
    run tests
  * ``backend`` (string, optional): see :ref:`task-lintian`
  * ``architectures`` (list of strings, optional): if set, only run on any
    of these architecture names

  * ``output``, ``include_tags``, ``exclude_tags``, ``fail_on_severity``:
    see :ref:`task-lintian`

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.lintian::LintianWorkflow.build_dynamic_data

Lintian will be run on the intersection of the provided list of
architectures (if any) and the architectures provided in
``binary_artifacts``, in each case grouping source + arch-all + arch-any
together for the best test coverage.  If only ``Architecture: all`` binary
packages are provided in ``binary_artifacts``, then Lintian will be run once
for source + arch-all.

The workflow creates a :ref:`task-lintian` for each concrete architecture,
with task data:

* ``input.source_artifact``: ``{source_artifact}``
* ``input.binary_artifacts``: the subset of ``{binary_artifacts}`` that are
  for the concrete architecture or ``all``
* ``environment``: ``{vendor}/match:codename={codename}``
* ``backend``: ``{backend}``
* ``output``, ``include_tags``, ``exclude_tags``, ``fail_on_severity``:
  copied from workflow task data parameters of the same names

Any of the lookups in ``input.source_artifact`` or
``input.binary_artifacts`` may result in :ref:`promises
<bare-data-promise>`, and in that case the workflow adds corresponding
dependencies.  Binary promises must include an ``architecture`` field in
their data.
