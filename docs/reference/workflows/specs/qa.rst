.. workflow:: qa

Workflow ``qa``
===============

* ``task_data``:

  * ``prefix`` (string, optional): prefix this string to the item names
    provided in the internal collection

  * ``source_artifact`` (:ref:`lookup-single`, required): the
    :artifact:`debian:source-package` or :artifact:`debian:upload` artifact
    representing the source package to test
  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): the
    :artifact:`debian:binary-package`, :artifact:`debian:binary-packages`,
    or :artifact:`debian:upload` artifacts representing the binary packages
    to test

  * ``package_build_logs``(:ref:`lookup-multiple``, optional): the
    :artifact:`debian:package-build-log` artifacts representing the build logs.
    Required if `enable_blhc` is True

  * ``qa_suite`` (:ref:`lookup-single`, optional unless
    ``update_qa_results`` or ``enable_debdiff`` is True): the
    :collection:`debian:suite` collection that reference tests are being run
    against to detect regressions or the collection to search for debdiff
    original packages
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
  * ``extra_repositories`` (optional): see :task:`PackageBuild`
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

  * ``qa_suite`` (:ref:`lookup-single`, required if
    ``enable_reverse_dependencies_autopkgtest`` is True): the
    :collection:`debian:suite` collection to search for
    reverse-dependencies; once we have a good way to look up the primary
    suite for a vendor and codename, this could default to doing so

  * ``enable_check_installability`` (boolean, defaults to True): whether to
    include installability-checking tasks
  * ``check_installability_suite`` (:ref:`lookup-single`, required if
    ``enable_check_installability`` is True): the
    :collection:`debian:suite` collection to check installability against;
    once we have a good way to look up the primary suite for a vendor and
    codename, this could default to doing so

  * ``enable_autopkgtest`` (boolean, defaults to True): whether to include
    autopkgtest tasks
  * ``autopkgtest_backend`` (string, optional): see :task:`Autopkgtest`

  * ``enable_reverse_dependencies_autopkgtest`` (boolean, defaults to
    False): whether to include autopkgtest tasks for reverse-dependencies

  * ``enable_lintian`` (boolean, defaults to True): whether to include
    lintian tasks
  * ``lintian_backend`` (string, optional): see :task:`Lintian`
  * ``lintian_fail_on_severity`` (string, optional): see :task:`Lintian`

  * ``enable_piuparts`` (boolean, defaults to True): whether to include
    piuparts tasks
  * ``piuparts_backend`` (string, optional): see :task:`Piuparts`
  * ``piuparts_environment`` (string, optional): the environment to run
    piuparts in

  * ``enable_debdiff`` (boolean, defaults to False): whether to include
    debdiff tasks for source and binary packages. Compares the supplied source
    package and the binary packages against the packages available in the
    distribution identified by ``qa_suite``.

  * ``enable_blhc`` (boolean, defaults to False): whether to include ``blhc``
    tasks for the build logs

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.qa::QAWorkflow.build_dynamic_data

Any of the lookups in ``source_artifact`` or ``binary_artifacts`` may result
in :bare-data:`promises <debusine:promise>`, and in that case the workflow
adds corresponding dependencies.  Binary promises must include an
``architecture`` field in their data.

The effective set of architectures is ``{architectures}`` (defaulting to all
architectures supported by this Debusine instance and the
``{vendor}:{codename}`` suite, plus ``all``), intersecting
``{architectures_allowlist}`` if set, and subtracting
``{architectures_denylist}`` if set.

The workflow creates sub-workflows and tasks as follows, with substitutions
based on its own task data:

* if ``enable_check_installability`` is set, a single
  :task:`CheckInstallability`, with task data:

  * ``suite``: ``{check_installability_suite}``
  * ``binary_artifacts``: the subset of the lookup in this workflow's
    ``binary_artifacts`` for each available architecture

* if ``enable_autopkgtest`` is set, an :workflow:`autopkgtest` sub-workflow,
  with task data:

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
  :workflow:`reverse_dependencies_autopkgtest` sub-workflow, with task data:

  * ``source_artifact``: ``{source_artifact}``
  * ``binary_artifacts``: the subset of the lookup in this workflow's
    ``binary_artifacts`` for each of ``all`` and the concrete architecture
    in question that exist
  * ``qa_suite``: ``{qa_suite}``
  * ``vendor``: ``{vendor}``
  * ``codename``: ``{codename}``
  * ``backend``: ``{autopkgtest_backend}``
  * ``architectures``: the effective set of architectures
  * ``arch_all_host_architecture``: ``{arch_all_host_architecture}``

* if ``enable_lintian`` is set, a :workflow:`lintian` sub-workflow, with
  task data:

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

* if ``enable_piuparts`` is set, a :workflow:`piuparts` sub-workflow, with
  task data:

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
