.. _workflow-qa:

Workflow ``qa``
===============

* ``task_data``:

  * ``source_artifact`` (:ref:`lookup-single`, required): the
    ``debian:source-package`` or ``debian:upload`` artifact representing the
    source package to test
  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): the
    ``debian:binary-packages`` or ``debian:upload`` artifacts representing
    the binary packages to test

  * ``vendor`` (string, required): the distribution vendor on which to run
    tests
  * ``codename`` (string, required): the distribution codename on which to
    run tests
  * ``extra_repositories`` (optional): see the :ref:`package-build-task`
  * ``architectures`` (list of strings, optional): if set, only run on any
    of these architecture names

  * ``architectures_allowlist`` (list of strings, optional, either concrete
    architecture names or ``all``): if set, only run on any of these
    architecture names; while ``architectures`` is intended to be supplied
    by users or passed down from a higher-level workflow, this field is
    intended to be provided via :ref:`task-configuration`
  * ``architectures_denylist`` (list of strings, optional, either concrete
    architecture names or ``all``): if set, do not run on any of these
    architecture names; this field is intended to be provided via
    :ref:`task-configuration`
  * ``arch_all_host_architecture`` (string, defaults to ``amd64``): concrete
    architecture on which to run tasks for ``Architecture: all`` packages

  * ``enable_check_installability`` (boolean, defaults to True): whether to
    include installability-checking tasks
  * ``check_installability_suite`` (:ref:`lookup-single`, required if
    ``enable_check_installability`` is True): the ``debian:suite``
    collection to check installability against; once we have a good way to
    look up the primary suite for a vendor and codename, this could default
    to doing so

  * ``enable_autopkgtest`` (boolean, defaults to True): whether to include
    autopkgtest tasks
  * ``autopkgtest_backend`` (string, optional): see :ref:`task-autopkgtest`

  * ``enable_reverse_dependencies_autopkgtest`` (boolean, defaults to
    False): whether to include autopkgtest tasks for reverse-dependencies
  * ``reverse_dependencies_autopkgtest_suite`` (:ref:`lookup-single`,
    required if ``enable_reverse_dependencies_autopkgtest`` is True): the
    ``debian:suite`` collection to search for reverse-dependencies; once we
    have a good way to look up the primary suite for a vendor and codename,
    this could default to doing so

  * ``enable_lintian`` (boolean, defaults to True): whether to include
    lintian tasks
  * ``lintian_backend`` (string, optional): see :ref:`task-lintian`
  * ``lintian_fail_on_severity`` (string, optional): see :ref:`task-lintian`

  * ``enable_piuparts`` (boolean, defaults to True): whether to include
    piuparts tasks
  * ``piuparts_backend`` (string, optional): see :ref:`task-piuparts`
  * ``piuparts_environment`` (string, optional): the environment to run
    piuparts in

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.qa::QAWorkflow.build_dynamic_data

Any of the lookups in ``source_artifact`` or ``binary_artifacts`` may result
in :ref:`promises <bare-data-promise>`, and in that case the workflow adds
corresponding dependencies.  Binary promises must include an
``architecture`` field in their data.

The effective set of architectures is ``{architectures}`` (defaulting to all
architectures supported by this Debusine instance and the
``{vendor}:{codename}`` suite, plus ``all``), intersecting
``{architectures_allowlist}`` if set, and subtracting
``{architectures_denylist}`` if set.

The workflow creates sub-workflows and tasks as follows, with substitutions
based on its own task data:

* if ``enable_check_installability`` is set, a single
  :ref:`task-check-installability`, with task data:

  * ``suite``: ``{check_installability_suite}``
  * ``binary_artifacts``: the subset of the lookup in this workflow's
    ``binary_artifacts`` for each available architecture

* if ``enable_autopkgtest`` is set, an :ref:`autopkgtest sub-workflow
  <workflow-autopkgtest>`, with task data:

  * ``source_artifact``: ``{source_artifact}``
  * ``binary_artifacts``: the subset of the lookup in this workflow's
    ``binary_artifacts`` for each of ``all`` and the concrete architecture
    in question that exist
  * ``vendor``: ``{vendor}``
  * ``codename``: ``{codename}``
  * ``backend``: ``{autopkgtest_backend}``
  * ``architectures``: the effective set of architectures
  * ``arch_all_host_architecture``: ``{arch_all_host_architecture}``

* if ``enable_reverse_dependencies_autopkgtest`` is set, a
  :ref:`reverse_dependencies_autopkgtest sub-workflow
  <workflow-reverse-dependencies-autopkgtest>`, with task data:

  * ``source_artifact``: ``{source_artifact}``
  * ``binary_artifacts``: the subset of the lookup in this workflow's
    ``binary_artifacts`` for each of ``all`` and the concrete architecture
    in question that exist
  * ``suite_collection``: ``{reverse_dependencies_autopkgtest_suite}``
  * ``vendor``: ``{vendor}``
  * ``codename``: ``{codename}``
  * ``backend``: ``{autopkgtest_backend}``
  * ``architectures``: the effective set of architectures
  * ``arch_all_host_architecture``: ``{arch_all_host_architecture}``

* if ``enable_lintian`` is set, a :ref:`lintian sub-workflow
  <workflow-lintian>`, with task data:

  * ``source_artifact``: ``{source_artifact}``
  * ``binary_artifacts``: the subset of the lookup in this workflow's
    ``binary_artifacts`` for each of ``all`` and the concrete architecture
    in question that exist
  * ``vendor``: ``{vendor}``
  * ``codename``: ``{codename}``
  * ``backend``: ``{lintian_backend}``
  * ``architectures``: the effective set of architectures
  * ``arch_all_host_architecture``: ``{arch_all_host_architecture}``
  * ``fail_on_severity``: ``{lintian_fail_on_severity}``

* if ``enable_piuparts`` is set, a :ref:`piuparts sub-workflow
  <workflow-piuparts>`, with task data:

  * ``binary_artifacts``: the subset of the lookup in this workflow's
    ``binary_artifacts`` for each of ``all`` and the concrete architecture
    in question that exist
  * ``vendor``: ``{vendor}``
  * ``codename``: ``{codename}``
  * ``backend``: ``{piuparts_backend}``
  * ``architectures``: the effective set of architectures
  * ``arch_all_host_architecture``: ``{arch_all_host_architecture}``

.. todo::

    Not implemented: ``enable_check_installability`` and
    ``check_installability_suite``.
