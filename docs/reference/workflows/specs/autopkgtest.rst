.. workflow:: autopkgtest

Workflow ``autopkgtest``
========================

This workflow schedules autopkgtests for a single source package on a set of
architectures.

* ``task_data``:

  * ``prefix`` (string, optional): prefix this string to the item names
    provided in the internal collection

  * ``source_artifact`` (:ref:`lookup-single`, required): see
    :task:`Autopkgtest`
  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): see
    :task:`Autopkgtest`
  * ``context_artifacts`` (:ref:`lookup-multiple`, optional): see
    :task:`Autopkgtest`

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
  * ``backend`` (string, optional): see :task:`Autopkgtest`
  * ``extra_repositories`` (optional): see :task:`Autopkgtest`
  * ``architectures`` (list of strings, optional): if set, only run on any
    of these architecture names
  * ``arch_all_host_architecture`` (string, defaults to ``amd64``): concrete
    architecture on which to run tasks for ``Architecture: all`` packages

  * ``include_tests``, ``exclude_tests``, ``debug_level``,
    ``extra_environment``, ``needs_internet``, ``fail_on``, ``timeout``: see
    :task:`Autopkgtest`

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.autopkgtest::AutopkgtestWorkflow.build_dynamic_data

Tests will be run on the intersection of the provided list of architectures
(if any) and the architectures provided in ``binary_artifacts``.  If only
``Architecture: all`` binary packages are provided in ``binary_artifacts``,
then tests are run on ``{arch_all_host_architecture}``.

The workflow creates an :task:`Autopkgtest` for each concrete architecture,
with task data:

* ``input.source_artifact``: ``{source_artifact}``
* ``input.binary_artifacts``: the subset of ``{binary_artifacts}`` that are
  for the concrete architecture or ``all``
* ``input.context_artifacts``: the subset of ``{context_artifacts}`` that
  are for the concrete architecture or ``all``
* ``host_architecture``: the concrete architecture
* ``environment``: ``{vendor}/match:codename={codename}``
* ``backend``: ``{backend}``
* ``extra_repositories`` copied from the workflow task, and extended
  with overlay repositories (e.g. ``experimental``) if ``codename`` is a
  known overlay.
* ``include_tests``, ``exclude_tests``, ``debug_level``,
  ``extra_environment``, ``needs_internet``, ``fail_on``, ``timeout``:
  copied from workflow task data parameters of the same names

Any of the lookups in ``input.source_artifact``, ``input.binary_artifacts``,
or ``input.context_artifacts`` may result in :bare-data:`promises
<debusine:promise>`, and in that case the workflow adds corresponding
dependencies.  Binary promises must include an ``architecture`` field in
their data.

Each work request provides its :artifact:`debian:autopkgtest` artifact as
output in the internal collection, using the item name
``{prefix}autopkgtest-{architecture}``.

.. todo::

    It would be useful to have a mechanism to control multiarch tests, such
    as testing i386 packages on an amd64 testbed.
