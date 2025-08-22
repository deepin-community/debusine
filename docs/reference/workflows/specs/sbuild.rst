.. workflow:: sbuild

Workflow ``sbuild``
===================

This workflow takes a source package and creates :task:`Sbuild` work
requests to build it for a set of architectures.

* ``task_data``:

  * ``prefix`` (optional): prefix this string to the item names provided in
    the internal collection

  * ``input`` (required): see :task:`PackageBuild`.
    ``source_artifact`` may be a :artifact:`debian:source-package` or a
    :artifact:`debian:upload`.  This workflow cannot be populated until this
    is a real artifact, not merely a promise.
  * ``target_distribution`` (required string): ``vendor:codename`` to specify
    the environment to use for building. It will be used to determine
    ``distribution`` or ``environment``, depending on ``backend``.
  * ``backend`` (optional string): see :task:`PackageBuild`
  * ``architectures`` (required list of strings): list of architectures to
    build. It can include ``all`` to build a binary for ``Architecture: all``
  * ``arch_all_host_architecture`` (string, defaults to ``amd64``): concrete
    architecture on which to build ``Architecture: all`` packages
  * ``extra_repositories`` (optional, default unset): enable extra APT
    repositories, see :task:`PackageBuild`.
  * ``environment_variant`` (optional string): variant of the
    environment we want to build on, e.g. ``buildd``; appended during
    environment :ref:`lookup <lookup-syntax>` for
    ``target_distribution`` above.
  * ``build_profiles`` (optional, default unset): select a build profile, see
    :task:`PackageBuild`.
  * ``binnmu`` (optional, default unset): build a binNMU, see
    :task:`PackageBuild`.
  * ``retry_delays`` (optional list): a list of delays to apply to each
    successive retry; each item is an integer suffixed with ``m`` for
    minutes, ``h`` for hours, ``d`` for days, or ``w`` for weeks.
  * ``signing_template_names`` (dictionary, optional): mapping from
    architecture to list of names of binary packages that should be used as
    templates by the :task:`ExtractForSigning` task

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.sbuild::SbuildWorkflow.build_dynamic_data

The source package will be built on the intersection of the provided list of
architectures and the architectures supported in the ``Architecture:`` field
of the source package. Architecture ``all`` packages are built on
``arch_all_host_architecture``.

The ``extra_repositories`` are copied from the workflow task, and
extended with overlay repositories (e.g. ``experimental``) if
``target_distribution`` is a known overlay.

The workflow may also apply a denylist of architectures if it finds a
:collection:`debian:suite` collection corresponding to the build
distribution/environment, and that suite provides one.

The workflow adds event reactions that cause the :artifact:`debian:upload`
artifact in the output for each architecture to be provided as
``{prefix}build-{architecture}`` in the workflow's internal collection.

If the workspace has a :collection:`debian:package-build-logs` collection,
then the workflow adds :ref:`action-update-collection-with-data` and
:ref:`action-update-collection-with-artifacts` event reactions to each
sbuild work request to record their build logs there.

If ``retry_delays`` is set, then the workflow adds a corresponding
``on_failure`` :ref:`action-retry-with-delays` action to each of the sbuild
work requests it creates.  This provides a simplistic way to retry
dependency-wait failures.  Note that this currently retries any failure, not
just dependency-waits; this may change in future.

If ``signing_template_names`` exists, then the workflow adds event reactions
that cause the corresponding :artifact:`debian:binary-package` artifacts in
the output for each architecture to be provided as
``{prefix}signing-template-{architecture}-{binary_package_name}`` in the
workflow's internal collection.
