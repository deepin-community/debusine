.. _workflow-debian-pipeline:

Workflow ``debian_pipeline``
============================
We want to provide a workflow coordinating all the steps that are typically
run to build and test an upload to Debian, similar to the `Salsa CI pipeline
<https://salsa.debian.org/salsa-ci-team/pipeline>`_ but (eventually) with
more distribution-wide testing and the ability to handle the task of
performing the upload.

This builds on the existing :ref:`sbuild workflow <workflow-sbuild>`.

* ``task_data``:

  * ``source_artifact`` (:ref:`lookup-single`, required): the
    ``debian:source-package`` or ``debian:upload`` artifact representing the
    source package to test

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
    by users, this field is intended to be provided via
    :ref:`task-configuration`
  * ``architectures_denylist`` (list of strings, optional, either concrete
    architecture names or ``all``): if set, do not run on any of these
    architecture names; this field is intended to be provided via
    :ref:`task-configuration`
  * ``arch_all_host_architecture`` (string, defaults to ``amd64``): concrete
    architecture on which to run tasks for ``Architecture: all`` packages

  * ``signing_template_names`` (dictionary, optional): mapping from
    architecture to list of names of binary packages that should be used as
    signing templates by the :ref:`make_signed_source sub-workflow
    <workflow-make-signed-source>`

  * ``sbuild_backend`` (string, optional): see :ref:`package-build-task`
  * ``sbuild_environment_variant`` (string, optional): variant of the
    environment to build on, e.g. ``buildd``

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

  * ``enable_debdiff`` (boolean, defaults to False): whether to generate
    debdiff for source and binary packages, comparing the supplied source
    package and the built binary packages against the packages available in the
    distribution identified by ``vendor`` and ``codename``.

  * ``enable_make_signed_source`` (boolean, defaults to False): whether to
    sign the contents of builds and make a signed source package
  * ``make_signed_source_purpose`` (string, required only if
    ``enable_make_signed_source`` is True): the purpose of the key to sign
    with; see :ref:`task-sign`
  * ``make_signed_source_key`` (string, required only if
    ``enable_make_signed_source`` is True): the fingerprint to sign
    with; must match ``purpose``

  * ``enable_confirmation`` (boolean, defaults to False): whether the
    generated workflow includes a confirmation step asking the user to
    double check what was built before the upload

  * ``enable_upload`` (boolean, defaults to False): whether to upload to an
    upload queue
  * ``upload_key`` (:ref:`lookup-single`, optional): key used to sign the
    uploads. If not set and if ``upload_require_signature`` is True, then
    the user will have to remotely sign the files.
  * ``upload_require_signature`` (boolean, defaults to True): whether the
    uploads must be signed
  * ``upload_include_source`` (boolean, defaults to True): include
    source with the upload
  * ``upload_include_binaries`` (boolean, defaults to True): include
    binaries with the upload
  * ``upload_merge_uploads`` (boolean, defaults to True): if True, merge the
    uploads for each source package and its binaries that are being
    uploaded, and create one PackageUpload task per source package to upload
    them all together; if False, create a separate PackageUpload task for
    each source and binary upload
  * ``upload_since_version`` (string, optional): if ``source_artifact`` is a
    ``debian:source-package``, include changelog information from all
    versions strictly later than this version in the ``.changes`` file; the
    default is to include only the topmost changelog entry
  * ``upload_target_distribution`` (string, optional): if
    ``source_artifact`` is a ``debian:source-package``, override the target
    ``Distribution`` field in the ``.changes`` file to this value; the
    default is to use the distribution from the topmost changelog entry
  * ``upload_target`` (string, defaults to
    ``ftp://anonymous@ftp.upload.debian.org/pub/UploadQueue/``): the upload
    queue, as an ``ftp://`` or ``sftp://`` URL
  * ``upload_delayed_days`` (integer, optional): the number of days to delay
    the upload; this assumes that the upload queue implements Debian's
    convention of uploading delayed uploads to a ``DELAYED/{n}-day`` queue

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.debian_pipeline::DebianPipelineWorkflow.build_dynamic_data

The effective set of architectures is ``{architectures}`` (defaulting to all
architectures supported by this Debusine instance and the
``{vendor}:{codename}`` suite, plus ``all``), intersecting
``{architectures_allowlist}`` if set, and subtracting
``{architectures_denylist}`` if set.

The workflow creates sub-workflows and tasks as follows, with substitutions
based on its own task data:

* an :ref:`sbuild sub-workflow <workflow-sbuild>`, with task data:

  * ``input.source_artifact``: ``{source_artifact}``
  * ``target_distribution``: ``{vendor}:{codename}``
  * ``backend``: ``{sbuild_backend}``
  * ``architectures``: the effective set of architectures
  * ``arch_all_host_architecture``: ``{arch_all_host_architecture}``, if set
  * ``environment_variant``: ``{sbuild_environment_variant}``, if set
  * ``signing_template_names``: ``{signing_template_names}``, if set

* if any of ``enable_check_installability``, ``enable_autopkgtest``,
  ``enable_lintian``, and ``enable_piuparts`` are True, a :ref:`qa
  sub-workflow <workflow-qa>`, with task data copied from the items of the
  same name in this workflow's task data, plus:

  * ``binary_artifacts``:
    ``internal@collections/name:build-{architecture}``, for each available
    architecture
  * ``architectures``: the effective set of architectures

* if ``enable_confirmation`` is set, a :ref:`task-confirm`

* if ``enable_make_signed_source`` and ``signing_template_names`` are set, a
  :ref:`make_signed_source sub-workflow <workflow-make-signed-source>`, with
  task data:

  * ``binary_artifacts``:
    ``internal@collections/name:build-{architecture}``, for each available
    architecture
  * ``signing_template_artifacts``:
    ``internal@collections/name:signing-template-{architecture}-{binary_package_name}``,
    for each architecture and binary package name from
    ``signing_template_names``
  * ``vendor``: ``{vendor}``
  * ``codename``: ``{codename}``
  * ``architectures``: the effective set of architectures
  * ``purpose``: ``{make_signed_source_purpose}``
  * ``key``: ``{make_signed_source_key}``
  * ``sbuild_backend``: ``{sbuild_backend}``

* if ``enable_upload`` is set, a :ref:`package_upload sub-workflow
  <workflow-package-upload>` for each source package being uploaded (at
  least the top-level ``source_artifact``, but also each assembled signed
  source package from the :ref:`make_signed_source sub-workflow
  <workflow-make-signed-source>` if one exists), configured to require a
  signature from the developer, with task data:

  * ``source_artifact``: the source artifact to upload (or unset if this
    upload is for the top-level source artifact and
    ``upload_include_source`` is False)
  * ``binary_artifacts``:
    ``internal@collections/name:{prefix}build-{architecture}``, for each
    available architecture (or empty if ``upload_include_binaries`` is
    False), where ``prefix`` is empty if this upload is for the top-level
    source artifact or
    ``signed-source-{architecture}-{binary_package_name}|`` if this upload
    is for an assembled signed source package
  * ``merge_uploads``: ``{upload_merge_uploads}``
  * ``since_version``: ``{upload_since_version}``
  * ``target_distribution``: ``{upload_target_distribution}``
  * ``key``: ``{upload_key}``
  * ``require_signature``: ``{upload_require_signature}``
  * ``target``: ``{upload_target}``
  * ``vendor``: ``{vendor}``
  * ``codename``: ``{codename}``
  * ``delayed_days``: ``{upload_delayed_days}``

The first work request for each architecture in the :ref:`make_signed_source
sub-workflow <workflow-make-signed-source>` and the first work request in
the ``package_upload`` sub-workflow depend on the :ref:`task-confirm` above.

.. todo::
    Not implemented: `enable_debdiff`, ``enable_check_installability``,
    ``check_installability_suite`` and ``enable_confirmation``.
    See the relevant blueprints for :ref:`task installability <task-check-installability>`,
    :ref:`reverse dependencies autopkgtest <workflow-reverse-dependencies-autopkgtest>` or
    :ref:`enable confirmation <task-confirm>`.

.. todo::

    There should also be an option to add the results to a debian:suite
    collection rather than uploading it to an external queue.  However, this
    isn't very useful until Debusine has its own repository hosting, and
    once it does, we'll need to be able to apply consistency checks to
    uploads rather than just adding them to suites in an unconstrained way.
    This will probably involve a new workflow yet to be designed.

.. todo::

    The pipeline should also include the ability to schedule a `debdiff
    against a baseline suite
    <https://salsa.debian.org/freexian-team/debusine/-/issues/398>`_ (either
    directly or in a sub-workflow).
