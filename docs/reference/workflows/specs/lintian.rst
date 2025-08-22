.. workflow:: lintian

Workflow ``lintian``
====================

This workflow schedules Lintian checks for a single source package and its
binaries on a set of architectures.

* ``task_data``:

  * ``source_artifact`` (:ref:`lookup-single`, required): see
    :task:`Lintian`
  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): see
    :task:`Lintian`

  * ``qa_suite`` (:ref:`lookup-single`, optional unless
    ``update_qa_results`` is True): the :collection:`debian:suite`
    collection that reference tests are being run against to detect
    regressions
  * ``reference_qa_results`` (:ref:`lookup-single`, optional unless
    ``update_qa_results`` is True): the :collection:`debian:qa-results`
    collection that contains the reference results of QA tasks to use to
    detect regressions
  * ``update_qa_results`` (boolean, defaults to False): when set to True,
    the workflow runs QA tasks and updates the collection passed in
    ``reference_qa_results`` with the results.

  * ``vendor`` (string, required): the distribution vendor on which to run
    tests
  * ``codename`` (string, required): the distribution codename on which to
    run tests
  * ``backend`` (string, optional): see :task:`Lintian`
  * ``architectures`` (list of strings, optional): if set, only run on any
    of these architecture names
  * ``arch_all_host_architecture`` (string, defaults to ``amd64``): concrete
    architecture on which to run tasks for ``Architecture: all`` packages

  * ``output``, ``include_tags``, ``exclude_tags``, ``fail_on_severity``:
    see :task:`Lintian`

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.lintian::LintianWorkflow.build_dynamic_data

Lintian will be run on the intersection of the provided list of
architectures (if any) and the architectures provided in
``binary_artifacts``, in each case grouping source + arch-all + arch-any
together for the best test coverage.  If only ``Architecture: all`` binary
packages are provided in ``binary_artifacts``, then Lintian will be run once
for source + arch-all.

The workflow creates a :task:`Lintian` for each concrete architecture, with
task data:

* ``input.source_artifact``: ``{source_artifact}``
* ``input.binary_artifacts``: the subset of ``{binary_artifacts}`` that are
  for the concrete architecture or ``all``
* ``host_architecture``: the concrete architecture, or
  ``{arch_all_host_architecture}`` if only ``Architecture: all`` binary
  packages were provided
* ``environment``: ``{vendor}/match:codename={codename}``
* ``backend``: ``{backend}``
* ``output``, ``include_tags``, ``exclude_tags``, ``fail_on_severity``:
  copied from workflow task data parameters of the same names

Any of the lookups in ``input.source_artifact`` or
``input.binary_artifacts`` may result in :bare-data:`promises
<debusine:promise>`, and in that case the workflow adds corresponding
dependencies.  Binary promises must include an ``architecture`` field in
their data.
