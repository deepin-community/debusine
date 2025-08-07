.. _workflow-reverse-dependencies-autopkgtest:

Workflow ``reverse_dependencies_autopkgtest``
=============================================

This workflow schedules autopkgtests for all the reverse-dependencies of a
package in a suite.

* ``task_data``:

  * ``source_artifact`` (:ref:`lookup-single`, required): the source package
    whose reverse-dependencies should be tested
  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): a list of
    ``debian:binary-packages`` or ``debian:upload`` artifacts representing
    the binary packages whose reverse-dependencies should be tested
  * ``context_artifacts`` (:ref:`lookup-multiple`, optional): a list of
    ``debian:binary-packages`` or ``debian:upload`` artifacts that should
    additionally be used to satisfy dependencies of tests
  * ``suite_collection`` (:ref:`lookup-single`, required): the
    ``debian:suite`` collection to search for reverse-dependencies

  * ``vendor`` (string, required): the distribution vendor on which to run
    tests
  * ``codename`` (string, required): the distribution codename on which to
    run tests
  * ``backend`` (string, optional): see :ref:`task-autopkgtest`
  * ``extra_repositories`` (optional): see :ref:`task-autopkgtest`
  * ``architectures`` (list of strings, optional): if set, only consider
    reverse-dependencies on any of these architecture names
  * ``arch_all_host_architecture`` (string, defaults to ``amd64``): concrete
    architecture on which to run tasks for ``Architecture: all`` packages
  * ``packages_allowlist`` (list of strings, optional): restrict tests to
    packages from this list
  * ``packages_denylist`` (list of strings, optional): skip tests of
    packages from this list

  * ``debug_level``: see :ref:`task-autopkgtest`

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.reverse_dependencies_autopkgtest::ReverseDependenciesAutopkgtestWorkflow.build_dynamic_data

The workflow searches the given suite collection for any source package
where all the following conditions hold:

* the name is not equal to the name of the source package being tested
* the name is not listed in ``packages_denylist``
* the name is listed in ``packages_allowlist`` (only tested if the field
  is present and set)
* it has binary packages built from it
* either any of its binary packages depend on any of the given binary
  package names, or any of the given binary package names are in its
  ``Testsuite-Triggers`` field (which can be found in its ``dsc_fields``
  data field)
* its ``Testsuite`` field has an item either equal to ``autopkgtest`` or
  starting with ``autopkgtest-pkg``

It then creates an :ref:`autopkgtest sub-workflow <workflow-autopkgtest>`
for each one, with task data as follows:

* ``prefix``: ``{source_artifact.name}_{source_artifact.version}|``
* ``source_artifact``: the source package to test
* ``binary_artifacts``: the binary packages built from that source package
* ``context_artifacts``: ``{binary_artifacts}`` (the binary packages whose
  reverse-dependencies are being tested) plus ``{context_artifacts}`` (any
  other binary packages used to satisfy dependencies of tests)
* ``vendor``, ``codename``, ``backend``, ``architectures``,
  ``arch_all_host_architecture``, ``debug_level``: copied from workflow task
  data parameters of the same names

Any of the lookups in ``binary_artifacts`` or ``context_artifacts`` may
result in :ref:`promises <bare-data-promise>`, and in that case the workflow
adds corresponding dependencies.  Binary promises must include a
``binary_names`` field in their data.

As usual, the workflow as a whole will succeed only if all the sub-workflows
succeed.  Individual results are available in the workflow's internal
collection: for example, a sub-workflow that tests Debusine 0.5.0 on amd64
and arm64 will provide the items ``debusine_0.5.0/autopkgtest-amd64`` and
``debusine_0.5.0/autopkgtest-arm64``.

.. todo::

    We will probably need the ability to control parameters such as
    ``fail_on`` in sub-workflows, but these are often quite
    package-specific.

.. todo::

    This workflow does not currently handle checking for regressions against
    a base reference.

.. todo::

    Reverse-dependencies are not currently restricted by version (e.g. due
    to versioned dependencies) or architecture (e.g. due to
    architecture-restricted dependencies, or the ``architectures`` field in
    task data).
