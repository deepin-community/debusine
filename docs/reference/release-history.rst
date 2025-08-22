.. _release-history:

===============
Release history
===============

.. towncrier release notes start

.. _release-0.12.1:

0.12.1 (2025-08-21)
-------------------

Server
~~~~~~

Bug Fixes
^^^^^^^^^

- Don't allow multiple instances of the same root workflow's orchestrator to
  run concurrently for different sub-workflows. (:issue:`1000`)
- Ignore SSO identities without an attached user in open-metrics endpoint,
  fixing a 500. (:issue:`1022`)


Web UI
~~~~~~

Features
^^^^^^^^

- :workflow:`debian_pipeline` workflow: display input artifacts (source
  package) and display source package in title. (:issue:`1005`)


Bug Fixes
^^^^^^^^^

- Display dynamic task data in the "Internals" tab of work requests if
  available. (:issue:`1013`)


Client
~~~~~~

Features
^^^^^^^^

- Add ``debusine workspace-inheritance`` to configure a workspace's
  inheritance. (:issue:`978`)
- Add shell completion via ``argcomplete``. (:issue:`1020`)


Bug Fixes
^^^^^^^^^

- Return more user-friendly errors if an incorrect file is specified to
  ``debusine provide-signature --local-file``. (:issue:`986`)


Workflows
~~~~~~~~~

Features
^^^^^^^^

- Add :workflow:`blhc` workflow and use it from :workflow:`debian_pipeline` and
  :workflow:`qa` workflows. (:issue:`802`)


Tasks
~~~~~

Bug Fixes
^^^^^^^^^

- ``task_configuration`` in task data is now inherited from the parent
  workflow. (:issue:`1012`)
- Default task_configuration to ``default@debusine:task-configuration``.
  (:issue:`1012`)


Worker
~~~~~~

Bug Fixes
^^^^^^^^^

- Go back to exiting the worker if it fails to send a task result to the
  server, so that systemd can restart it and allow it to reconnect.
  (:issue:`937`)


General
~~~~~~~

Bug Fixes
^^^^^^^^^

- Weaken tests for invalid HTML, since lxml (via libxml2) no longer provides as
  much HTML error checking. (:issue:`953`)


.. _release-0.12.0:

0.12.0 (2025-08-15)
-------------------

Server
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Remove ``debusine-admin create_workspace``, ``delete_workspace``,
  ``list_workspaces``, and ``manage_workspace`` commands, deprecated in favor
  of ``debusine-admin workspace <subcommand>`` in 0.8.0.

  Remove ``debusine-admin create_file_store``, deprecated in favor of
  ``debusine-admin file_store create`` in 0.9.0. (:issue:`886`)
- Moved OIDC validation to code. ``Provider.restrict`` is still supported, but
  deprecated: use ``settings.SIGNON_CLASS`` instead, see the ``DebusineSignon``
  class.

  The ``add_to_group`` option of ``Provider`` now requires a dict mapping
  GitLab
  groups to Debusine groups, instead of a string, and a string value is
  ignored.

  Site-specific code is provided to replicate existing setups for
  ``debusine.debian.net`` and ``debusine.freexian.com``, and can be removed
  once
  both sites are migrated to using dict values for ``add_to_group``.
  (:issue:`898`)
- Refactor server/signon to remove compatibility code.

  This drops the previous ``debusine.DebusineSignon`` class for
  ``SIGNON_CLASS``
  in favour of ``sites.DebianSignon``, only needed for ``debusine.debian.net``.

  The default ``signon.Signon`` class is now sufficient for basic deployments,
  including ``debusine.freexian.com``.

  ``restrict`` has been un-deprecated and is now honored, so that deployments
  like ``debusine.freexian.com`` can restrict logins to given GitLab groups
  without a ``SIGNON_CLASS``.

  No migration strategy is provided: this requires a flag day for
  ``debusine.debian.net`` and ``debusine.freexian.com``, as they used the
  ``DebusineSignon`` class. There should be no breaking changes for other
  deployments introduced with this change. (:issue:`898`)
- Changed the location for local templates in the packaged defaults.

  Additional local templates were loaded from
  ``/var/lib/debusine/server/templates``. This is now changed to
  ``/etc/debusine/server/templates``, which is the correct place for local
  customizations. (:issue:`947`)
- Squashed database migrations from before 0.11.0.  People with older Debusine
  server installations must upgrade to 0.11.* before upgrading to this version.
  (:issue:`975`)
- Rename ``debusine-admin manage_worker`` command to ``debusine-admin worker``.
  The ``--worker-type`` option, if given, must now come after the subcommand
  name (``enable`` or ``disable``).  The old name is still present, but is
  deprecated.

  Rename ``debusine-admin edit_worker_metadata`` command to ``debusine-admin
  worker edit_metadata``.  The old name is still present, but is deprecated.

  Rename ``debusine-admin list_workers`` command to ``debusine-admin worker
  list``.  The old name is still present, but is deprecated.


Features
^^^^^^^^

- Store content-type of files as sent by the client. (:issue:`324`)
- Implement :collection:`debian:archive` collection. (:issue:`329`)
- Add an API endpoint to abort a work request or a workflow. (:issue:`384`)
- Add :artifact:`debian:repository-index` artifacts, and allow adding them to
  :collection:`debian:suite` collections.

  Add a :task:`GenerateSuiteIndexes` task to generate ``Packages``,
  ``Sources``, and ``Release`` files for a suite. (:issue:`755`)
- Add ``url`` and ``scope`` fields to responses from several API views.
  (:issue:`766`)
- Add ``/api/1.0/open-metrics/`` that provides instance usage statistics.
  (:issue:`888`)
- Allow ``Provider.options['add_to_group']`` to match ``nm.debian.org`` user
  statuses when using an ``nm:`` prefix. (:issue:`898`)
- Add ``on_assignment`` :ref:`event <workflow-event-reactions>`.

  Add :ref:`action-skip-if-lookup-result-changed` action, and a new "skipped"
  work request result. (:issue:`907`)
- Ensure that only one workflow callback can run at once for a given workflow.
  (:issue:`908`)
- :task:`APTMirror`: Mirror repository indexes as well as packages.
  (:issue:`945`)
- Add ``debusine-admin worker create`` to create a worker with an activation
  token, as an alternative to letting it register itself and then enabling the
  token separately.
- Extend ``binary`` and ``binary-version`` lookups on
  :collection:`debian:archive` and :collection:`debian:suite` collections to
  include ``Architecture: all`` packages for concrete architecture names.
- Improve reconstruction of lookups by preserving the original spelling of the
  parent collection.


Bug Fixes
^^^^^^^^^

- ``debusine-admin delete_expired``: Optimize calculation of which artifacts
  must be kept. (:issue:`473`)
- Record errors from assigning work requests to workers in
  ``WorkRequest.output_data``. (:issue:`589`)
- Optimize ``Workspace.get_collection``, used by most lookups. (:issue:`786`)
- Fix crashes in ``debusine-admin delete_expired`` and ``debusine-admin
  vacuum_storage`` when trying to clean up expired or old incomplete artifacts
  respectively.

  Make ``debusine-admin delete_expired`` delete files from stores that aren't
  present in any artifact, even if the artifacts they used to be in weren't
  deleted in this ``delete_expired`` run. (:issue:`891`)
- Fix a race when telling the client which of a new artifact's files it needs
  to upload; previously this sometimes resulted in incomplete artifacts when
  two artifacts with overlapping files were created at nearly the same time.

  Forbid creating relations with incomplete artifacts. (:issue:`930`)
- Fix crash in ``debusine-admin delete_expired`` when trying to clean up
  expired workspaces. (:issue:`936`)
- When creating worker, server, or sub-workflow work requests in workflows,
  make them inherit the effective priority of their parent as their base
  priority. (:issue:`973`)
- Work around S3 incompatibility between Hetzner and boto3 >= 1.36.0.


Web UI
~~~~~~

Features
^^^^^^^^

- Display files based on the content-type sent by the client, restricted to a
  set of safe content-types. (:issue:`324`)
- Add web UI to abort a work request or a workflow. (:issue:`384`)
- Add a separate virtual host with archive access views. (:issue:`757`)
- Make it easier for local admins to customize the homepage and footer.
  (:issue:`850`)
- Allow Debian Maintainers to log in via Salsa OIDC authentication.
  (:issue:`898`)
- Add view to test and debug how task configuration is applied. (:issue:`989`)
- Add links to workflow documentation from the web UI.


Bug Fixes
^^^^^^^^^

- Exclude Celery worker from list of workers. (:issue:`559`)
- Add ``--server FQDN/SCOPE`` option to suggested ``debusine
  provide-signature`` command (requires the client to be at least version
  0.11.3). (:issue:`749`)
- Make "workspace not found" errors slightly more generic, since they can also
  cover authorization failures. (:issue:`778`)
- Optimize detail view for large workflows. (:issue:`786`)
- Return 404 when trying to view a nonexistent workflow template, rather than
  logging a noisy traceback. (:issue:`875`)
- Fix display of collection retention periods. (:issue:`890`)
- Support byte-range requests that specify only one of the first and last byte
  positions in the range. (:issue:`956`)


Client
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Print web URLs to objects where possible.  This requires a server with at
  least commit `30dd738393e46f2f2bc0d09aacdfd53297dbba95
  <https://salsa.debian.org/freexian-team/debusine/-/commit/30dd738393e46f2f2bc0d09aacdfd53297dbba95>`__.
  (:issue:`766`)


Features
^^^^^^^^

- Guess content-type of files when uploading them to the server. (:issue:`324`)
- Add ``debusine abort-work-request`` command. (:issue:`384`)
- Allow selecting a server using ``--server FQDN/SCOPE``, as an alternative to
  needing to know the ``[server:...]`` section name in the configuration file.
  (:issue:`749`)
- Added ``debusine task-config-pull`` and ``task-config-push``, to manage
  :collection:`debusine:task-configuration` collections. (:issue:`789`)
- A local copy of the ``.changes`` file can be passed to ``provide-signature``
  for signing and uploading. (:issue:`816`)
- Accept extra command-line arguments to ``debusine
  on-work-request-completed``. (:issue:`966`)


Workflows
~~~~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :workflow:`lintian`, :workflow:`qa`, :workflow:`debian_pipeline`: Change
  default value of ``fail_on_severity``/``lintian_fail_on_severity`` to
  ``error``. (:issue:`804`)
- Rename the ``suite_collection`` key of the
  :workflow:`reverse_dependencies_autopkgtest` workflow and the
  ``reverse_dependencies_autopkgtest_suite`` key of the :workflow:`qa` and
  :workflow:`debian_pipeline` workflows to ``qa_suite``.

  :workflow:`piuparts`: Add ``source_artifact`` as a required task data key.
  (:issue:`907`)


Features
^^^^^^^^

- Add :workflow:`debdiff` workflow and integrate it into
  :workflow:`debian_pipeline`. (:issue:`607`)
- Add :workflow:`update_suites` workflow. (:issue:`755`)
- In the :workflow:`sbuild` workflow, configure the same ASPCUD criteria as
  Debian's buildd would use, when targeting ``experimental``. (:issue:`829`)
- :workflow:`update_environments`: Accept ``null`` as an element in a
  ``targets.variants`` list; this may be useful to indicate that an environment
  may be used as a generic environment for any task while also being the most
  suitable environment for particular variants. (:issue:`899`)
- :workflow:`reverse_dependencies_autopkgtest`: Document support for passing
  :artifact:`debian:binary-package` artifacts in ``binary_artifacts`` and
  ``context_artifacts``.

  :workflow:`qa`: Document support for passing
  :artifact:`debian:binary-package` artifacts in ``binary_artifacts``.
  (:issue:`906`)
- :workflow:`autopkgtest`, :workflow:`lintian`, :workflow:`piuparts`,
  :workflow:`qa`, :workflow:`reverse_dependencies_autopkgtest`: Support
  updating a :collection:`debian:qa-results` collection with reference QA
  results. (:issue:`907`)
- :workflow:`autopkgtest`: Implement ``enable_regression_tracking`` parameter
  to perform regression analysis against reference results. (:issue:`908`)
- Add split source/binary upload signing, where the developer signs the source
  package and Debusine signs the binaries. (:issue:`944`)


Bug Fixes
^^^^^^^^^

- Make the :workflow:`package_upload` workflow idempotent. (:issue:`800`)
- :workflow:`lintian`: Constrain child work requests to run on an architecture
  matching the binaries. (:issue:`866`)
- :workflow:`lintian`: Only produce source and binary-all analysis artifacts
  once.

  :workflow:`lintian`: If ``binary_artifacts`` is empty, create a single work
  request to run ``lintian`` on the source package. (:issue:`908`)
- :workflow:`package_upload`: Avoid confusion between the output of different
  :task:`MergeUploads` tasks. (:issue:`914`)
- :workflow:`reverse_dependencies_autopkgtest`: Give child workflows a
  hardcoded priority of -5 relative to their parent workflow, for now.
  (:issue:`973`)
- :workflow:`package_upload`: Constrain :task:`MakeSourcePackageUpload` to run
  on a particular architecture. (:issue:`990`)


Tasks
~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :task:`Lintian`: Change default value of ``fail_on_severity`` to ``error``.
  (:issue:`804`)
- If tasks are given an ``environment`` without a ``variant`` filter,
  automatically try ``variant={task_name}`` followed by ``variant=``.  This may
  require changes to your ``update_environments`` workflows to ensure that a
  generic environment with no variant is always available. (:issue:`899`)


Features
^^^^^^^^

- Display input artifacts for tasks :task:`AssembleSignedSource`,
  :task:`Autopkgtest`, :task:`Blhc`, :task:`CopyCollectionItems`,
  :task:`ExtractForSigning`, :task:`Lintian`, :task:`MakeSourcePackageUpload`,
  :task:`MergeUploads`, Noop, :task:`PackageUpload`, :task:`Piuparts`,
  :task:`SystemBootstrap`, and :task:`SystemImageBuild`. (:issue:`549`)
- :task:`Sbuild`: Add ``build_dep_resolver`` and ``aspcud_criteria`` options.
  (:issue:`829`)
- Require a compatible piuparts version to be available in the environment for
  the :task:`Piuparts` task, when running in a container. (:issue:`867`)
- :task:`Autopkgtest`: Document support for passing
  :artifact:`debian:binary-package` artifacts in ``input.binary_artifacts`` and
  ``input.context_artifacts``.

  :task:`Piuparts`: Support passing :artifact:`debian:binary-package` artifacts
  in ``input.binary_artifacts``. (:issue:`906`)
- :task:`Lintian`: Add ``architecture`` field to :artifact:`debian:lintian`
  artifact. (:issue:`908`)
- :task:`DebDiff`: Speed up this task significantly by avoiding installing most
  of the ``Recommends`` of the ``devscripts`` package.


Bug Fixes
^^^^^^^^^

- Fix DNS resolution during ``customization_script`` execution in
  :task:`SimpleSystemImageBuild` image builds. (:issue:`664`)
- :task:`Piuparts`: Process base tarball in Python rather than using
  ``mmtarfilter``, which wasn't available until Debian 10 (buster).
  (:issue:`867`)
- Ensure that a ``/var/lib/dpkg/available`` file exists when running the
  :task:`Piuparts` task. (:issue:`874`)
- :task:`Lintian`: Fix incorrect parsing of tag explanations for Debian
  bullseye. (:issue:`921`)
- :task:`MergeUploads`: Fix ineffective overlapping-files check when multiple
  input uploads share the same ``.changes`` file name. (:issue:`954`)
- Fix an ``AssertionError`` in the :task:`Piuparts` task, when using
  :artifact:`debian:binary-packages` as input.
- Install ``passwd`` if we need ``useradd`` to create a non-root user inside
  task executors.


Signing
~~~~~~~

Features
^^^^^^^^

- Add :task:`SignRepositoryIndex` task. (:issue:`756`)


General
~~~~~~~

Features
^^^^^^^^

- Add ``components`` attribute to :artifact:`debian:system-tarball` and
  :artifact:`debian:system-image` artifacts. (:issue:`829`)
- Run test suite under ``pytest``.


.. _release-0.11.3:

0.11.3 (2025-07-08)
-------------------

Client
~~~~~~

Features
^^^^^^^^

- A local copy of the ``.changes`` file can be passed to ``provide-signature``
  for signing and uploading. (:issue:`816`)


.. _release-0.11.2:

0.11.2 (2025-07-03)
-------------------

Client
~~~~~~

Features
^^^^^^^^

- Allow selecting a server using ``--server FQDN/SCOPE``, as an alternative to
  needing to know the ``[server:...]`` section name in the configuration file.
  (:issue:`749`)


.. _release-0.11.1:

0.11.1 (2025-05-04)
-------------------

Server
~~~~~~

Bug Fixes
^^^^^^^^^

- Set ``Vary: Cookie, Token`` on all HTTP responses. (:issue:`761`)
- Return multiple lookup results in a predictable order, to make it easier for
  workflows to be idempotent. (:issue:`796`)
- Fix regression in ``update_workflows`` Celery task.


Web UI
~~~~~~

Features
^^^^^^^^

- Add a :artifact:`debdiff <debian:debdiff>` artifact view. (:issue:`714`)
- Added list and detail views for WorkerPool. (:issue:`733`)
- Add number of files in the "Files" tab of the artifact view.
- Redesigned table sorting and header rendering.


Bug Fixes
^^^^^^^^^

- Redesigned table filtering. (:issue:`475`)
- Search collection page: fix "str failed to render" error in table headers.
  (:issue:`799`)
- :task:`Autopkgtest`: Render extra repositories as deb822 sources.
  (:issue:`828`)
- Change the default tab in the artifact view to "Files". (:issue:`848`)
- :task:`Autopkgtest`: Fix the "Distribution" field.


Miscellaneous
^^^^^^^^^^^^^

- :issue:`420`, :issue:`814`


Client
~~~~~~

Features
^^^^^^^^

- ``debusine setup``: Manage the default server setting. (:issue:`780`)


Bug Fixes
^^^^^^^^^

- Wrap Debusine errors so that they're shown cleanly by ``dput-ng``.
  (:issue:`827`)
- Improve logging while uploading individual files to artifacts.
  (:issue:`839`)
- Fix handling of responses without ``Content-Type``.


Workflows
~~~~~~~~~

Features
^^^^^^^^

- Allow overriding the ``environment`` in the :workflow:`piuparts` workflow.
  Allow overriding the ``piuparts_environment`` in the :workflow:`qa` and
  :workflow:`debian_pipeline` workflows. (:issue:`638`)


Bug Fixes
^^^^^^^^^

- In the :workflow:`autopkgtest`, :workflow:`piuparts` and
  :workflow:`sbuild` workflows, extend children's ``extra_repositories``
  with overlay repositories (e.g. ``experimental``) if ``codename`` is a
  known overlay. (:issue:`780`)
- :workflow:`make_signed_source`: Disambiguate handling of multiple signing
  templates for a single architecture.

  :workflow:`make_signed_source`: Provide :artifact:`debian:upload`
  artifacts as ``signed-source-*`` outputs, not
  :artifact:`debian:source-package`.

  :workflow:`debian_pipeline`: Upload signed source packages and their
  binaries if necessary. (:issue:`796`)
- :workflow:`sbuild`: Improve workflow orchestration error when no
  environments were found.  (:issue:`830`)


Tasks
~~~~~

Bug Fixes
^^^^^^^^^

- :task:`Lintian`: Use ``lintian --print-version`` to extract the version.
  (:issue:`609`)
- Fix a variety of bugs in :task:`SimpleSystemImageBuild` image builds, that
  broke use with the ``incus-vm`` and ``qemu`` executors.
  Only require the ``python3-minimal`` package to be installed for the ``qemu``
  executor. (:issue:`664`)
- :task:`DebDiff`: Install ``diffstat`` package, to make the ``--diffstat``
  flag work. (:issue:`748`)
- :task:`DebDiff`: Create ``relates-to`` relations to binary artifacts.


Worker
~~~~~~

Bug Fixes
^^^^^^^^^

- Incus LXC instances now wait for ``systemd-networkd`` to declare the network
  online, before running autopkgtests. (:issue:`812`)


General
~~~~~~~

Documentation
^^^^^^^^^^^^^

- Add new project management practices page. (:issue:`784`)
- Update playground setup advice. (:issue:`797`)
- Update the introduction with more recent content.


.. _release-0.11.0:

0.11.0 (2025-04-15)
-------------------

Server
~~~~~~

Features
^^^^^^^^

- Delete artifacts that were created more than a day ago and are still
  incomplete. (:issue:`667`)


Bug Fixes
^^^^^^^^^

- Don't create a workflow if its input validation fails. (:issue:`432`)
- Only retry work requests up to three times in a row due to worker failures.
  (:issue:`477`)
- Rename ``debusine-server-artifacts-cleanup.{service,timer}`` to
  ``debusine-server-delete-expired.{service,timer}``, to better reflect the
  function of those units. (:issue:`636`)
- :task:`APTMirror`: Ensure that only one mirroring task for a given
  collection runs at once. (:issue:`694`)
- Don't set the Celery worker's concurrency to 1 in the database when starting
  the scheduler or provisioner service. (:issue:`751`)
- Record errors from server tasks in ``WorkRequest.output_data``.
  (:issue:`785`)
- Optimize computing the runtime status of large workflows.
  Batch expensive workflow updates and defer them to a Celery task.
  (:issue:`786`)


Documentation
^^^^^^^^^^^^^

- Update :ref:`configure-gitlab-sso` to account for a renamed module.


Web UI
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Remove FileInArtifact IDs from file links.

``<scope>/<workspace>/artifact/<artifact_id>/raw/<file_in_artifact_id>/<path>``
  becomes ``…/<artifact_id>/raw/<path>``.

``<scope>/<workspace>/artifact/<artifact_id>/file/<file_in_artifact_id>/<path>``
  becomes ``…/<artifact_id>/file/<path>``. (:issue:`621`)


Features
^^^^^^^^

- Better usability for the token generation UI: copy token to clipboard, show a
  config snippet with the token. (:issue:`421`)
- Downloading an artifact without the archive= query parameter autodetects the
  file type.

  This means that a download will by default produce a tarball only if the
  artifact contains more than one file. One can explicitly add
  ``?archive=tar.gz`` to force always returning a tarball. (:issue:`621`)
- Add view raw and download buttons to all file display widgets.
  (:issue:`621`)
- Add an indication to ``/-/status/workers/`` showing each worker's pool.
  Exclude inactive pool workers from ``/-/status/workers/``.
  Add worker details page. (:issue:`733`)


Bug Fixes
^^^^^^^^^

- Work requests now show validation/configuration errors. (:issue:`651`)


Client
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Move Debusine-specific entries in ``dput-ng`` profiles from keys in a nested
  ``debusine`` object to top-level ``debusine_*`` keys, to make them easier to
  override in local profiles.


Features
^^^^^^^^

- Add ``bullseye-security``, ``bookworm``, and ``bookworm-security`` entries to
  the ``dput-ng`` profile for ``debusine.debian.net``.
- Show more useful information for 404 responses.


Bug Fixes
^^^^^^^^^

- Fix file uploads if ``api-url`` is configured with a trailing slash.
  (:issue:`793`)


Workflows
~~~~~~~~~

Features
^^^^^^^^

- Restrict starting workflows to workspace contributors. (:issue:`625`)


Bug Fixes
^^^^^^^^^

- Record errors from ``Workflow.ensure_dynamic_data``. (:issue:`589`)
- Record orchestrator errors in ``WorkRequest.output_data``.
  :workflow:`reverse_dependencies_autopkgtest`: Validate
  ``suite_collection`` parameter. (:issue:`651`)
- Use ``|`` instead of ``/`` as a collection item prefix separator in
  workflows, since ``/`` is used to separate lookup string segments.
  :workflow:`reverse_dependencies_autopkgtest`: Fix orchestration failure
  for source package versions containing a colon.


Tasks
~~~~~

Features
^^^^^^^^

- :task:`MergeUploads`: Reimplement ``mergechanges`` in Python, for
  efficiency and to avoid problems with buggy versions of ``mawk`` in some
  old Debian releases. (:issue:`512`)


Bug Fixes
^^^^^^^^^

- :task:`ExtractForSigning`: Tolerate overlap between template and binary
  artifacts. (:issue:`763`)


Signing
~~~~~~~

Documentation
^^^^^^^^^^^^^

- Document how to find generated signing keys. (:issue:`771`)


General
~~~~~~~

Documentation
^^^^^^^^^^^^^

- Rework :ref:`tutorial-getting-started` to create a workflow. (:issue:`764`)


Miscellaneous
^^^^^^^^^^^^^

- :issue:`743`


.. _release-0.10.0:

0.10.0 (2025-04-02)
-------------------

Server
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :task:`CreateExperimentWorkspace`: Redefine ``expiration_delay`` as a
  number of days rather than a duration.
- Use Debusine permissions for managing workflow templates.  If you previously
  granted yourself the ``add_workflowtemplate`` permission, see the
  :ref:`updated tutorial <tutorial-getting-started>` for how to grant yourself
  owner access to a workspace.


Features
^^^^^^^^

- Store worker pool statistics on task completion and worker shutdown.
  Implement provisioning of pool workers. (:issue:`721`)


Bug Fixes
^^^^^^^^^

- Retry any running work requests when terminating pool workers.
  (:issue:`731`)
- Limit status views of running external tasks (``/api/1.0/service-status/``
  and ``/-/status/queue/``) to worker tasks. (:issue:`750`)


Documentation
^^^^^^^^^^^^^

- Document cloud worker pools and storage. (:issue:`735`)


Web UI
~~~~~~

Features
^^^^^^^^

- Add an audit log for group-related changes. (:issue:`734`)


Bug Fixes
^^^^^^^^^

- Fix link to workflows that need input.


Client
~~~~~~

Features
^^^^^^^^

- Add ``debusine setup`` for editing server configuration interactively.
  (:issue:`711`)
- Add ``dput-ng`` integration. (:issue:`713`)


Bug Fixes
^^^^^^^^^

- ``debusine provide-signature``: Always pass ``--re-sign`` to ``debsign``.
  (:issue:`713`)


Workflows
~~~~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :workflow:`create_experiment_workspace`: Redefine ``expiration_delay`` as
  a number of days rather than a duration.


Bug Fixes
^^^^^^^^^

- :workflow:`make_signed_source`: Pass unsigned binary artifacts to
  :workflow:`sbuild` sub-workflow via ``input.extra_binary_artifacts``.
  (:issue:`727`)
- :workflow:`autopkgtest`, :workflow:`lintian`: Handle
  :artifact:`debian:upload` source artifacts without original upstream
  source. (:issue:`744`)


Documentation
^^^^^^^^^^^^^

- :workflow:`make_signed_source`: No longer document
  :artifact:`debian:binary-packages` artifacts as being accepted in
  ``binary_artifacts``; they never worked. (:issue:`747`)


Tasks
~~~~~

Features
^^^^^^^^

- :task:`Sbuild`: Accept :artifact:`debian:upload` artifacts in
  ``input.extra_binary_artifacts``. (:issue:`727`)


Bug Fixes
^^^^^^^^^

- :task:`ExtractForSigning`: If given :artifact:`debian:upload` artifacts in
  ``binary_artifacts``, follow ``extends`` relationships to find the
  underlying :artifact:`debian:binary-package` artifacts. (:issue:`747`)
- Handle errors while fetching task input more gracefully. (:issue:`763`)


.. _release-0.9.1:

0.9.1 (2025-03-24)
------------------

Server
~~~~~~

Features
^^^^^^^^

- Automatically add task runs to the appropriate
  :collection:`debusine:task-history` collection. (:issue:`510`)
- Support Hetzner Object Storage.
  Support worker pools on Hetzner Cloud. (:issue:`543`)
- Accept scope prefixes in ``debusine-admin create_collection --workspace`` and
  ``debusine-admin create_work_request --workspace``. (:issue:`608`)
- Implement ``populate`` and ``drain`` storage policies in ``debusine-admin
  vacuum_storage``.
  Implement store-level ``soft_max_size`` and ``max_size`` limits in
  ``debusine-admin vacuum_storage``. (:issue:`684`)
- :asset:`debusine:cloud-provider-account` asset: Add optional
  ``configuration.s3_endpoint_url`` for the ``aws`` provider type.
  (:issue:`685`)
- Add roles to group memberships. (:issue:`697`)
- Add ``debusine-admin worker_pool`` command.
  Add internal per-provider API for launching and terminating dynamic workers.
  (:issue:`720`)
- Support worker pools on AWS EC2. (:issue:`722`)


Bug Fixes
^^^^^^^^^

- Add a ``DEBUSINE_DEFAULT_WORKSPACE`` Django setting, for use if the default
  workspace has been renamed to something other than "System". (:issue:`571`)
- Only upload to write-only stores when applying the ``populate`` storage
  policy in ``debusine-admin vacuum_storage``, not elsewhere. (:issue:`684`)


Documentation
^^^^^^^^^^^^^

- Document file stores. (:issue:`541`)
- Document :task:`CreateExperimentWorkspace` task. (:issue:`542`)


Web UI
~~~~~~

Features
^^^^^^^^

- Add web UI for group management: list groups, add/remove users, change user
  roles. (:issue:`542`)


Bug Fixes
^^^^^^^^^

- Do not show "Plumbing" in the navigation bar if the view is not
  workspace-aware. (:issue:`675`)


Workflows
~~~~~~~~~

Features
^^^^^^^^

- :workflow:`package_publish`: Copy :collection:`debusine:task-history`
  items from the same workflow. (:issue:`510`)


Documentation
^^^^^^^^^^^^^

- Document :workflow:`create_experiment_workspace`. (:issue:`542`)
- Document how to implement a new workflow. (:issue:`693`)


Tasks
~~~~~

Features
^^^^^^^^

- :task:`MmDebstrap`, :task:`SimpleSystemImageBuild`: Support reading
  keyrings from ``/usr/local/share/keyrings/``. (:issue:`739`)


Worker
~~~~~~

Features
^^^^^^^^

- Add worker activation tokens, which can be used to auto-enable pool workers
  when they start without needing to expose worker tokens in ``cloud-init``
  user-data. (:issue:`732`)


General
~~~~~~~

Miscellaneous
^^^^^^^^^^^^^

- :issue:`729`


.. _release-0.9.0:

0.9.0 (2025-02-25)
------------------

Server
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- File stores are now linked to scopes rather than to workspaces.  They can be
  configured using ``debusine-admin scope``.
  ``debusine-admin workspace define`` and ``debusine-admin workspace list`` (as
  well as the deprecated ``debusine-admin create_workspace``, ``debusine-admin
  manage_workspace``, and ``debusine-admin list_workspaces`` commands) no
  longer handle file stores. (:issue:`682`)
- Rename ``debusine-admin create_file_store`` command to ``debusine-admin
  file_store create``.  (The old name is still present, but is deprecated.)
  (:issue:`683`)
- Rename ``debusine-admin monthly_cleanup`` to ``debusine-admin
  vacuum_storage``, and run it daily.  Rename the associated ``systemd`` units
  similarly. (:issue:`684`)


Features
^^^^^^^^

- Implement :ref:`task configuration mechanism <task-configuration>`.
  (:issue:`508`)
- Implement :collection:`debusine:task-history` collection. (:issue:`510`)
- Add API: ``1.0/asset/`` to create and list :ref:`assets`.
  Add API:
  ``1.0/asset/<str:asset_category>/<str:asset_slug>/<str:permission_name>`` to
  check permissions on :ref:`assets`.
  Add ``debusine-admin asset`` management command to manage asset permissions.
  (:issue:`576`)
- Add ``debusine-admin scope add_file_store``, ``debusine-admin scope
  edit_file_store``, and ``debusine-admin scope remove_file_store`` commands.
  Add an ``instance_wide`` field to file stores, defaulting to True, which can
  be configured using the ``--instance-wide``/``--no-instance-wide`` options to
  ``debusine-admin file_store create``.  Non-instance-wide file stores may only
  be used by a single scope.
  Add ``soft_max_size`` and ``max_size`` fields to file stores, which can be
  configured using the ``--soft-max-size`` and ``--max-size`` options to
  ``debusine-admin file_store create``. (:issue:`682`)
- Add ``debusine-admin scope show`` command.
  Add ``debusine-admin file_store delete`` command.
  Make ``debusine-admin file_store create`` idempotent. (:issue:`683`)
- Generalize sweeps by ``debusine-admin vacuum_storage`` over files in local
  storage to be able to handle other backends. (:issue:`684`)
- Add ``debusine-admin asset create`` command.
  Add an :file-backend:`S3` file backend.
  Add ``--provider-account`` option to ``debusine-admin file_store create``, to
  allow linking file stores to cloud provider accounts. (:issue:`685`)
- Add :asset:`debusine:cloud-provider-account` asset. (:issue:`696`)
- Implement ephemeral groups. (:issue:`697`)
- Add a plugin for the Munin monitoring server.
  If run on the server, it should be able to automatically configure itself.
  It provides three graphs.
  The workrequest queue length is graphed by type and by worker architecture.
  The third graph shows the number of registered, connected and busy workers.


Bug Fixes
^^^^^^^^^

- Deal with expired work requests without an internal collection that are
  referenced by build logs.
  Fix deleting expired work requests with child work requests referenced by
  build logs. (:issue:`635`)
- Explicitly depend on ``libjs-select2.js`` in the ``debusine-server`` package.
- Set current context when running server tasks.


Documentation
^^^^^^^^^^^^^

- Add blueprint for dynamic cloud compute scaling. (:issue:`538`)
- Add blueprint for dynamic cloud storage scaling. (:issue:`539`)
- Split artifacts documentation by category. (:issue:`541`)
- Add blueprint for cloning workspaces for experiments.
  Add blueprint for granting ``ADMIN`` roles on groups to users. (:issue:`542`)


Miscellaneous
^^^^^^^^^^^^^

- :issue:`666`, :issue:`704`


Web UI
~~~~~~

Features
^^^^^^^^

- Workspaces can now be set to expire. Owners can configure this and other
  attributes in the web UI. (:issue:`698`)
- Display configured task data (see :ref:`task-configuration`) in views that
  display work requests. (:issue:`707`)
- ``/{scope}/{workspace}/workflow/``: Add ``label`` tag to "With failed work
  requests", to allow enabling/disabling the checkbox by clicking on the text.


Bug Fixes
^^^^^^^^^

- Fix collection item detail URLs to allow slashes in names. (:issue:`676`)
- Handle empty Lintian artifacts. (:issue:`677`)
- Filter workflow template detail view to the current workspace. (:issue:`680`)
- Preserve redirect URL on login. (:issue:`717`)
- Fix title of homepage and scope pages.


Client
~~~~~~

Features
^^^^^^^^

- Add ``asset_create`` and ``asset_list`` methods to create and list
  :ref:`assets`.
  Add ``create-asset`` and ``list-assets`` commands to create and list assets.
  Add ``asset_permission_check`` method to check permissions on :ref:`assets`.
  (:issue:`576`)


Workflows
~~~~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :workflow:`debian_pipeline`, :workflow:`make_signed_source`,
  :workflow:`package_upload`: Signing keys are now specified by fingerprint,
  rather than a lookup for an asset.
  Remove the ``debian:suite-signing-keys`` collection. (:issue:`576`)


Features
^^^^^^^^

- Add ``subject`` to dynamic data for all workflows. (:issue:`679`)
- Add workflow to create an experiment workspace. (:issue:`699`)


Bug Fixes
^^^^^^^^^

- :workflow:`make_signed_source`: Fix passing of
  :artifact:`debusine:signing-input` artifacts between workflow steps.
  (:issue:`689`)
- Fix handling of dependencies between workflows.  In most cases workflows
  themselves shouldn't have dependencies, but the :workflow:`sbuild`
  sub-workflow created by :workflow:`make_signed_source` is an exception.
  (:issue:`690`)
- :workflow:`make_signed_source`: Pass all outputs from the :task:`Sign`
  task through to the :task:`AssembleSignedSource` task, not just one of
  them. (:issue:`692`)
- :workflow:`make_signed_source`: Fix orchestration of :workflow:`sbuild`
  sub-workflow. (:issue:`695`)


Tasks
~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :task:`Sbuild`: Remove ``schroot`` support. (:issue:`660`)


Features
^^^^^^^^

- Add ``subject``, ``configuration_context``, and ``runtime_context`` to
  dynamic data for all worker tasks. (:issue:`679`)


Bug Fixes
^^^^^^^^^

- Fix accidental leakage of keyring and customization script names between
  :task:`MmDebstrap` task instances on the same worker, leading to task
  failure. (:issue:`686`)


Worker
~~~~~~

Features
^^^^^^^^

- Record runtime statistics for tasks. (:issue:`510`)
- Log task stages to a work request debug log as well.


Bug Fixes
^^^^^^^^^

- Fix various worker asyncio issues.


Signing
~~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :task:`GenerateKey`: The result is now a :asset:`debusine:signing-key`
  :ref:`asset <assets>` rather than an :ref:`artifact <artifact-reference>`.
  :task:`Debsign`, :task:`Sign`: The ``key`` parameter is now the key's
  fingerprint, rather than an asset lookup.
  :task:`Sign`, :task:`Debsign`: The ``signer`` role is required on signing
  key assets, by the work request creator. (:issue:`576`)


Features
^^^^^^^^

- Allow recording username and resource data in the signing service audit log.
  Record the username and resource description in the audit log, in the
  :task:`Sign` and :task:`Debsign` tasks. (:issue:`576`)


General
~~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Add a new primitive, :ref:`assets`, to represent objects that need
  permissions, like :asset:`debusine:signing-key`.
  Existing work requests and workflows are migrated to refer to signing keys by
  fingerprint.
  Existing ``debusine:signing-key`` artifacts are migrated to assets.
  We recommend that Debusine admins audit their database for any remaining
  artifacts with category ``debusine:signing-key``, and remove them after
  confirming that they have been migrated to assets. This will require removing
  any related artifact relations first. Audit query: ``SELECT * FROM
  db_artifact WHERE category='debusine:signing-key';`` (:issue:`576`)


.. _release-0.8.1:

0.8.1 (2025-01-13)
------------------

Server
~~~~~~

Features
^^^^^^^^

- New view with list of workflows (``/<scope>/<workspace>/workflow/``). List
  workflow templates with stats in the workspace view
  (``/<scope></workspace>/``), new view with specific template information
  (``/<scope>/<workspace>/workflow-template/<workflow-template>/``).
  (:issue:`400`)


Bug Fixes
^^^^^^^^^

- Use an in-memory channel layer for tests, rather than Redis. (:issue:`617`)
- Fix cleanup of expired work requests referenced by internal collections.
  (:issue:`644`)
- Retry any work requests that a worker is currently running when it asks for a
  new work request. (:issue:`667`)
- Fix tests with python-debian >= 0.1.50. (:issue:`672`)


Documentation
^^^^^^^^^^^^^

- Split collections documentation by category. (:issue:`541`)


Web UI
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Reorganize ``/-/user/`` URLs to contain the user name, and move the logout
  view to ``/-/logout/``. (:issue:`649`)
- Remove ``/view/`` from workspace view path (``/<scope>/<workspace>/view/``).


Features
^^^^^^^^

- Add workflows split-button pulldown to base template. (:issue:`620`)
- For workflows that need input, link to the first work request that needs
  input. (:issue:`674`)
- Add a user detail view.
- Extend workspace detail view to show figures about workflows.
- Use `select2 <https://select2.org/>`__ for the multiple choice fields on the
  workflow list form.


Bug Fixes
^^^^^^^^^

- Hide collections with the category ``workflow-internal`` from the navbar
  collections dropdown. (:issue:`639`)
- Return 404 when trying to view incomplete files, rather than logging a noisy
  traceback.
  Don't link to incomplete files, and mark them as "(incomplete)".
  Mark artifacts as incomplete in artifact lists if any of their files are
  incomplete. (:issue:`667`)
- Fix ordering of workers list by "Last seen". (:issue:`669`)


Workflows
~~~~~~~~~

Features
^^^^^^^^

- :workflow:`debian_pipeline`, :workflow:`qa`,
  :workflow:`reverse_dependencies_autopkgtest`, :workflow:`sbuild`: Support
  :artifact:`debian:upload` artifacts as input. (:issue:`590`)
- :workflow:`autopkgtest`, :workflow:`piuparts`,
  :workflow:`reverse_dependencies_autopkgtest`, :workflow:`qa`,
  :workflow:`debian_pipeline`: Add support for ``extra_repositories``.
  (:issue:`622`)


Bug Fixes
^^^^^^^^^

- Fix looking up the architecture from a lookup that returns an artifact from a
  collection. (:issue:`661`)


Tasks
~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :task:`Autopkgtest`: Replace the ``extra_apt_sources`` property with
  ``extra_repositories``, following the same syntax as :task:`Sbuild`.
  (:issue:`622`)


Features
^^^^^^^^

- Gather runtime statistics from executors. (:issue:`510`)
- :task:`Piuparts`: Add support for ``extra_repositories``. (:issue:`622`)
- :task:`SimpleSystemImageBuild`: Switch from debos to debefivm-create for
  VM image creation. This also drops support for the Debian Jessie release.


Bug Fixes
^^^^^^^^^

- :task:`Piuparts`: Compress processed base tarball for pre-1.3
  compatibility. (:issue:`638`)


General
~~~~~~~

Miscellaneous
^^^^^^^^^^^^^

- :issue:`648`, :issue:`670`


.. _release-0.8.0:

0.8.0 (2024-12-26)
------------------

Server
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Refactor tabular output to also allow machine-readable YAML. (:issue:`247`)
- Add permission checks to all API views that accept user authentication.
  (:issue:`568`)
- Enforce permissions when creating artifacts. (:issue:`614`)
- Deprecate ``debusine-admin create_workspace``, ``delete_workspace``,
  ``list_workspace`` and ``manage_workspace`` in favor of
  ``debusine-admin workspace <subcommand>``.
  ``debusine-admin workspace create`` creates workspaces with a default
  30-days expiration delay (instead of no expiration by default for
  ``create_workspace``), and requires an existing owner group to be
  specified. (:issue:`640`)
- Enforce permissions when retrying work requests.


Features
^^^^^^^^

- ``debusine-admin create_workspace``: Assign an owners group, controlled by
  the ``--with-owners-group`` option. (:issue:`527`)
- Add infrastructure to help enforcing permissions in views. (:issue:`598`)
- Record information about any originating workflow template in work requests,
  and add a cached human-readable summary of their most important parameters.
  (:issue:`618`)
- Implement ``debusine-admin group list`` and ``debusine-admin group members``.
  (:issue:`623`)
- Add a contributor role for workspaces; contributors can display the workspace
  and create artifacts in it. (:issue:`625`)
- Introduce new ``debusine-admin workspace`` subcommand, regrouping and
  expanding the existing ``*_workspace``. See :ref:`debusine-admin
  workspace <debusine-admin-cli-workspace>`. (:issue:`640`)
- Allow bare artifact IDs in workflow input.


Bug Fixes
^^^^^^^^^

- Validate new scope, user, collection, and notification channel names.
  (:issue:`551`)
- Allow creating workflows using scoped workspace names. (:issue:`570`)
- Report workflow validation errors directly to the client on creation, rather
  than leaving unvalidated workflows lying around in error states.
  (:issue:`633`)
- Set up permissions context when running server tasks. (:issue:`642`)
- Port to Django 5.1. (:issue:`646`)
- Check work request status when running Celery tasks, to guard against
  mistakes elsewhere.
- Enable Django's ``ATOMIC_REQUESTS`` setting, avoiding a class of mistakes
  where views forget to wrap their changes in a transaction.
- Implement ``add_to_group`` option in signon providers.
- Link externally-signed artifacts to the :task:`ExternalDebsign` work
  request.


Miscellaneous
^^^^^^^^^^^^^

- :issue:`626`, :issue:`643`


Web UI
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Drop workspaces from homepage; they are now visible on scope pages instead.
  (:issue:`554`)
- Move ``/api-auth/`` views to ``/api/auth/``. (:issue:`581`)
- Move ``admin``, ``task-status``, ``user``, and ``workers`` views to unscoped
  URLs. (:issue:`582`)
- Move account-related views to unscoped URLs. (:issue:`583`)
- Move work request URLs under workspaces. (:issue:`584`)
- Move artifact URLs under workspaces. (:issue:`585`)


Features
^^^^^^^^

- Set the current workspace in views that use it. (:issue:`395`)
- Move "Workers" and "Task status" from the navigation bar to the footer.
  Add a per-scope landing page.
  Add a "Collections" menu in workspaces.
  Add view to list and filter workflows. (:issue:`557`)
- Show current and other workspaces in base template. (:issue:`624`)
- Merge workspace list into scope detail view. (:issue:`629`)
- Show the current scope as the "brand", with an optional label and icon.
  (:issue:`630`)
- Display git-based version information in footer. (:issue:`631`)
- Show results in workflow views.
- Show workflow details open by default.


Bug Fixes
^^^^^^^^^

- Silence unnecessary logging when viewing invalid work requests.
  (:issue:`588`)
- Log out via ``POST`` rather than ``GET``. (:issue:`646`)
- :task:`ExternalDebsign`: Fix "Waiting for signature" card.
- Consider task type when selecting work request view plugins.
- Fix "Last Seen" and "Status" for Celery workers.
- List workflow templates in workspace detail view.


Documentation
^^^^^^^^^^^^^

- Document scope as required in client configuration, and simplify example if
  there is only one. (:issue:`613`)


Miscellaneous
^^^^^^^^^^^^^

- :issue:`645`


Client
~~~~~~

Documentation
^^^^^^^^^^^^^

- Add documentation for the client configuration file. (:issue:`613`)


Workflows
~~~~~~~~~

Features
^^^^^^^^

- Add :workflow:`package_publish` workflow. (:issue:`396`)
- Add :workflow:`reverse_dependencies_autopkgtest` workflow. (:issue:`397`)
- :workflow:`autopkgtest`, :workflow:`sbuild`: Implement
  ``arch_all_host_architecture``. (:issue:`574`)
- :workflow:`sbuild`: Implement ``extra_repositories``. (:issue:`622`)
- :workflow:`package_upload`: Support uploading to delayed queues.


Bug Fixes
^^^^^^^^^

- :workflow:`debian_pipeline`: Handle some ``build-*`` promises being
  missing.
- :workflow:`make_signed_source`, :workflow:`package_upload`: Fix invalid
  creation of some child work requests. Add validation to catch such
  problems in future.
- :workflow:`package_upload`: Set correct task type for ``ExternalDebsign``.
- Fix work request statuses in several workflows.
- Mark empty workflows as completed.


Documentation
^^^^^^^^^^^^^

- Point to the workflow template list.


Tasks
~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :task:`Sbuild`: Stop running ``lintian``; it's now straightforward to run
  both ``sbuild`` and ``lintian`` in sequence using the
  :workflow:`debian_pipeline` workflow. (:issue:`260`)


Features
^^^^^^^^

- :task:`Sbuild`: Implement ``extra_repositories``. (:issue:`622`)
- :task:`Lintian`, :task:`Piuparts`: Capture ``apt-get`` output.


Bug Fixes
^^^^^^^^^

- :task:`Sbuild`: Don't count it as a success if the host architecture is
  not supported by the source package. (:issue:`592`)
- :task:`Sbuild`: Drop the redundant ``--no-clean`` argument. (:issue:`603`)
- :task:`Piuparts`: Handle ``piuparts`` being in either ``/usr/sbin`` or
  ``/usr/bin``.
- Wait for Incus instances to boot systemd.


Documentation
^^^^^^^^^^^^^

- Split task documentation by task types.


Miscellaneous
^^^^^^^^^^^^^

- :issue:`652`


Signing
~~~~~~~

Documentation
^^^^^^^^^^^^^

- Add blueprint for restricting use of signing keys. (:issue:`576`)


General
~~~~~~~

Features
^^^^^^^^

- Enforce ``mypy``'s strict mode across the whole codebase.


Bug Fixes
^^^^^^^^^

- Ensure consistent ``LANG`` settings in systemd services. (:issue:`494`)
- Reset failed ``*-migrate`` services in integration tests.


.. _release-0.7.2:

0.7.2 (2024-11-13)
------------------

Quality
~~~~~~~

* Use ``hello`` from bookworm in piuparts integration test.

.. _release-0.7.1:

0.7.1 (2024-11-12)
------------------

Quality
~~~~~~~

* Fetch packages from matching suites in integration tests.

.. _release-0.7.0:

0.7.0 (2024-11-12)
------------------

Server
~~~~~~

* Unblock reverse-dependencies when aborting a work request.
* Upgrade to Django 4.2.
* Implement an admin role for scopes.
* Validate group names.
* Add ``debusine-admin group`` management command.
* Add :workflow:`make_signed_source` workflow.
* Add API for monitoring worker status.
* Add roles for workspaces.
* Handle scopes in workspace management commands.
* Add an initial set of permission predicates.
* Add scope visibility permission check.
* Use workspace permissions in collection lookup.
* Force evaluation of lazy ``request.user`` in ``AuthorizationMiddleware``.
* Don't ignore failed elements of multiple lookups.
* Make the default workspace public.
* Improve command-line handling of constraint violations.
* Add :ref:`singleton collections <collection-singleton>`.
* Add permission for creating workspaces.
* Add :workflow:`lintian` workflow.
* Fix ``debusine-admin create_workspace --default-expiration-delay``
  command-line parsing.
* Support lookups that match items of multiple types.
* Add :workflow:`piuparts` workflow.
* Add :workflow:`qa` workflow.
* Implement ``signing_template_names`` in :workflow:`sbuild` workflow.
* Add ``same_work_request`` lookup filter to
  :collection:`debian:package-build-logs` collection.
* Add :workflow:`debian_pipeline` workflow.
* Add :task:`CopyCollectionItems` task.

Web UI
~~~~~~

* Disallow public access to work requests in private workspaces.
* Prototype implementation of scopes in URLs.
* Handle workspaces with the same name in different scopes.
* Remove ``workspace/`` segment from URLs.

Client
~~~~~~

* Implement scope support.
* Correctly download artifacts with directories in file paths.

Worker
~~~~~~

* :task:`SystemBootstrap`:

  * Allow keyring URLs starting with ``file:///usr/share/keyrings/``.
  * Write non-ASCII-armored keyrings to ``.gpg`` rather than ``.asc``.

* :task:`Sbuild`:

  * Relax ``binnmu_maintainer`` validation in dynamic data to avoid failures
    if ``DEBUSINE_FQDN`` is under a non-email-suitable domain.
  * Drop unnecessary ``sbuild:host_architecture`` from dynamic metadata.

* Add :task:`DebDiff` task.

Signing
~~~~~~~

* :task:`Sign`:

  * Fail if signing failed.
  * Use detached signatures when signing UEFI files.
  * Take multiple unsigned artifacts and sign them all with the same key.

* Register :task:`Debsign` task, which previously existed but was unusable.

Documentation
~~~~~~~~~~~~~

* Indicate that kmod keys aren't (yet?) supported.
* Split signing service documentation into :ref:`explanation
  <explanation-signing-service>` and :ref:`reference
  <reference-signing-service>`.
* Add an :ref:`explanation of lookups <explanation-lookups>`.
* Document the :ref:`debusine-worker CLI <debusine-worker-cli>`.
* Move :ref:`artifact relationships <artifact-relationships>` documentation
  to reference.
* Point to bookworm-backports instead of deb.freexian.com.
* Update :ref:`add-new-worker` to explain how to enable a signing worker.
* Add :ref:`how-to for configuring a YubiHSM <configure-hsm>`.
* Install a signing worker in the :ref:`installation tutorial
  <tutorial-install-debusine>`.
* Document the :ref:`debusine-signing CLI <debusine-signing-cli>`.
* Add blueprint for changing the UI to be more workflow-centered.
* Restructure the hierarchy of reference documentation pages.
* Document how to generate signing keys.
* Add blueprint for copying artifacts between workspaces.
* Add blueprint for a URL redesign.

Quality
~~~~~~~

* Add more type annotations for tasks.
* Fix test failures in non-English locales.
* Skip simplesystemimagebuild test with UML >= 6.11um1 for now.

.. _release-0.6.0:

0.6.0 (2024-10-10)
------------------

Server
~~~~~~

* Tighten up handling of creating artifacts with files that already exist.
* Add ``Wait`` task type.
* Add :task:`Delay` task.
* Add :task:`ExternalDebsign` task and a corresponding API view to allow a
  client to provide a signature to it.
* Add a system for coordinating multiple sub-workflows within a higher-level
  workflow.
* Introduce :ref:`scopes <explanation-scopes>`.
* Introduce a basic application context.
* Run workflow orchestrators via Celery.
* Add :workflow:`autopkgtest` workflow.
* Add ``debusine-admin scope`` command.
* Add :ref:`action-retry-with-delays` action for use in ``on_failure`` event
  reactions.
* :workflow:`sbuild` workflow:

  * Support build profiles.
  * Add ``retry_delays``, which can be used for simplistic retries of
    dependency-wait failures.

* Let ``nginx`` gzip-compress text responses.
* Add :task:`PackageUpload` task.
* Add :workflow:`package_upload` workflow.

Web UI
~~~~~~

* Improve label for :artifact:`debian:binary-package` artifacts.
* Show "Waiting for signature" card on blocked :task:`ExternalDebsign`
  requests.
* Show forward and reverse-extends artifact relations.

Client
~~~~~~

* Add ``debusine provide-signature`` command.
* Allow ``debusine import-debian-artifact`` to upload individual ``.deb``
  packages.
* Correct imported package relations.
* Don't download large artifacts as tarballs.

Worker
~~~~~~

* Add :task:`MakeSourcePackageUpload` task.
* Add :task:`MergeUploads` task.
* :task:`Sbuild`:

  * Support ``build_profiles``.
  * Don't permit architecture-independent binary-only NMUs.
  * Fix ``architecture`` field of created :artifact:`debian:binary-packages`
    artifacts.
  * Export ``DEB_BUILD_OPTIONS`` for ``nocheck`` and ``nodoc`` profiles.
  * Set a default maintainer for binary-only NMUs.

* Apply some environment constraints to the :task:`Piuparts` task's
  ``base_tgz`` lookup.
* Register :task:`ExtractForSigning` task, which previously existed but was
  unusable.
* Fix ``unshare`` executor compatibility with Debian environments from
  before the start of the ``/usr`` merge.
* Fall back to the worker's host architecture for the purpose of environment
  lookups if the task doesn't specify one.
* Log progress through the main steps of each task.

Signing
~~~~~~~

* Add :task:`Debsign` task.

Documentation
~~~~~~~~~~~~~

* Document signing workers and tasks.
* Add design for permission management.
* Add design for reverse-dependencies-autopkgtest workflow.
* Add design for task configuration, work request statistics, and other
  build-related features.
* Add short introduction to :ref:`debusine-concepts` tying everything
  together.
* Move explanation of expiration logic to a separate
  :ref:`expiration-of-data` page.
* Simplify :ref:`explanation of artifacts <explanation-artifacts>`.
* Move information about :ref:`reference-task-types` to a separate page.
* Move information about :ref:`collection data models
  <collection-data-models>` to a separate page.

Quality
~~~~~~~

* Use `vulture <https://github.com/jendrikseipp/vulture>`__ to find dead
  code.
* Sort imports automatically using `isort
  <https://github.com/PyCQA/isort>`__.
* Make coverage reports briefer.

.. _release-0.5.0:

0.5.0 (2024-09-03)
------------------

Server
~~~~~~

* Avoid N+1 queries when resolving :ref:`multiple lookups
  <lookup-multiple>`.
* Automatically drop privileges when running ``debusine-admin`` or
  ``debusine-signing`` as root.
* Mark retried work requests as blocked if necessary.
* Add an API endpoint to review manual unblocks.
* Unassign pending or running work requests when disabling a worker.
* Fix ineffective :collection:`debian:environments` uniqueness constraint.
* Adjust the :workflow:`sbuild` workflow to allow storing build logs in a
  new :collection:`debian:package-build-logs` collection.
* Default to a five-second timeout when sending email, to avoid hangs if the
  local mail transport agent is broken.
* Don't buffer output to log files.
* Validate new work requests when creating them.

Web UI
~~~~~~

* Link to work request and build log in artifact list.
* Add a framework of UI shortcuts and sidebar information, allowing a more
  attractive and consistent presentation of resources such as artifacts and
  work requests.
* Redirect user to original URL after login.
* If an artifact has only one file, download that file by default instead of
  a tarball.
* Show input artifacts in work request views.
* Add a user-friendly view of files in artifacts.
* Fix error when viewing an artifact with multiple related build logs.
* Use `pygments <https://pygments.org/>`__ to render text content.
* Redesign work request detail view.
* Use work request labels in the UI.
* Add UI to review work requests blocked on manual approval.
* Add a view of registered workers and their running work requests.
* Fix collection search paging.
* Add a view of the task queue.

Client
~~~~~~

* Only accept valid artifact categories in ``debusine create-artifact``.
* Don't process downloads one byte at a time.
* Retry some HTTP requests.

Worker
~~~~~~

* Make ``arch-test`` a dependency rather than an optional feature.
* Add :task:`ExtractForSigning` task.
* Add :task:`AssembleSignedSource` task.
* :task:`Sbuild`:

  * Create a :artifact:`debusine:signing-input` artifact.
  * Ignore ``dose-debcheck`` decoding errors.
  * Support building binary-only NMUs.
  * Skip ``dose-debcheck`` extraction on success.

Signing
~~~~~~~

* Add support for static (not extracted under wrap) PKCS#11 keys.
* Add OpenPGP key generation and signing support.

Documentation
~~~~~~~~~~~~~

* Document that workers need ``sbin`` directories in their ``PATH``.
* Clarify data model details for the workflow hierarchy.
* Improve documentation for ``debusine-admin manage_worker disable``.
* Fix documentation of creating a collection in :ref:`set-up-apt-mirroring`.
* Add design for coordinating sub-workflows.
* Add design for package upload task and workflow.

Quality
~~~~~~~

* Support building Debusine itself with ``nocheck`` and ``nodoc`` build
  profiles.
* Add `pre-commit <https://pre-commit.com/>`__ configuration.
* Fix various :py:exc:`ResourceWarning`\ s.
* Convert Python packaging to `hatchling
  <https://pypi.org/project/hatchling/>`__.
* Add many more type annotations.
* Use `dbconfig-pgsql
  <https://www.debian.org/doc/manuals/dbconfig-common/>`__ for database
  configuration, avoiding services restarting indefinitely after initial
  installation.
* Ensure that Debusine starts after and stops before a PostgreSQL service
  running on the same machine.
* Make task-killing tests more reliable.

.. _release-0.4.1:

0.4.1 (2024-06-28)
------------------

Server
~~~~~~

* Make ``debusine:test`` artifact instantiable.

Web UI
~~~~~~

* Introduce a common base layout with a right sidebar.
* Implement labels for artifacts.
* Add specialized view for showing build log artifacts.

Worker
~~~~~~

* Run ``sbuild`` with ``--bd-uninstallable-explainer=dose3`` and parse its
  output.

Quality
~~~~~~~

* Fix license classifier in ``setup.cfg``.

.. _release-0.4.0:

0.4.0 (2024-06-24)
------------------

Server
~~~~~~

* Add API endpoint to retry work requests.
* Implement retrying workflows.
* Give the scheduler Celery worker a different node name.
* Switch to ``RedisPubSubChannelLayer``.

Web UI
~~~~~~

* Add UI to retry work requests.

Worker
~~~~~~

* Add binary-only NMU support to ``sbuild`` task.
* Use ``arch-test`` to provide better defaults for ``system:architectures``.

Signing
~~~~~~~

* Add a new signing service.  This currently supports generating keys
  (though currently only in software, as opposed to an HSM) and signing UEFI
  Secure Boot images with them.  A few more pieces still need to be
  assembled before this is useful.

Documentation
~~~~~~~~~~~~~

* Document HTTPS setup.
* Document signing worker.

Quality
~~~~~~~

* Remove now-unnecessary autopkgtest schroot creation from integration
  tests.
* Add a "playground" system to manage test object creation and to allow
  discussion of UI prototypes.
* Use HTTPS in integration tests.
* Bump timeout for ``mmdebstrap`` integration tests.
* Reorganize test cases for improved type-safety.
* Fix cleanup order in an integration test which caused failures on slow
  architectures.

.. _release-0.3.2:

0.3.2 (2024-06-03)
------------------

Server
~~~~~~

* Rename some leftovers of "internal" naming for server tasks.
* Added method to check if a work request can be retried.
* Fix ``Architecture: all`` matching in ``sbuild`` workflow.

Web UI
~~~~~~

* Second iteration on collection UI design.
* Add base template support for ``django.contrib.messages``.

Quality
~~~~~~~

* Fix several race conditions and timeouts that caused autopkgtest failures
  on slow architectures.

.. _release-0.3.1:

0.3.1 (2024-05-28)
------------------

Server
~~~~~~

* Namespace collections under workspaces.
* Refresh worker from database before marking it disconnected, so that we
  don't lose changes made using ``debusine-admin edit_worker_metadata``.
* Add backend capability to retry aborted or failed work requests.
* ``sbuild`` workflow:

  * Fix task data for ``Architecture: all`` work requests.
  * Specify the backend in environment lookups.
  * Defer environment resolution.

Web UI
~~~~~~

* Fix typo resulting in HTTP 500 error in collection detail view.

Worker
~~~~~~

* Handle systemd 256 in ``incus-lxc`` executor.
* Handle dangling ``/etc/resolv.conf`` symlinks in environments in the
  ``unshare`` executor.
* Fix ``mmdebstrap`` task to specify the architecture of the chroot.

Documentation
~~~~~~~~~~~~~

* Fix several errors in the "Getting started with Debusine" tutorial.
* Adjust "The debusine command" reference to refer to self-documenting
  ``--help`` output.

Quality
~~~~~~~

* Skip some integration tests for architectures that weren't in bookworm.
* Add enums for artifact and collection categories, to guard against typos.

.. _release-0.3.0:

0.3.0 (2024-05-23)
------------------

Highlights:

* The focus of this milestone is on automatic orchestration of building
  blocks, to allow tasks to be scheduled for all items of a collection.  For
  example, Debusine can now automatically schedule Lintian tasks for all
  packages in a suite.
* Added collections and workflows.
* Added a new lookup syntax, taking advantage of collections.

Server
~~~~~~

* Add infrastructure for collections.
* Implement :collection:`debian:environments` collection.
* Implement :collection:`debian:suite-lintian` collection.
* Add ``debusine-admin create_collection`` command.
* Store tokens only in a hashed form.
* Implement :collection:`debian:suite` collection.
* Move the scheduler to a dedicated Celery worker.
* Generalize work request notifications into event reactions.
* Implement basic building blocks of workflows.
* Implement synchronization points.
* Implement workflow orchestrators.
* Implement workflow callbacks.
* Add ``--default-file-store`` options to ``debusine-admin
  create_workspace`` and ``debusine-admin manage_workspace``.
* Restrict creation of non-worker tasks via the API.
* Add ``debusine-admin create_file_store`` command.
* Implement scheduling priorities.
* Implement ``update-collection-with-artifacts`` event reaction.
* Implement collection item lookup syntax and semantics.
* Implement ``aptmirror`` server task.
* Implement ``updatesuitelintiancollection`` task to update a
  :collection:`debian:suite-lintian` collection from
  :collection:`debian:suite`.
* Implement :collection:`debusine:workflow-internal` collection.
* Add ``debusine-admin create_work_request`` command.
* Implement ``sbuild`` and ``update_environments`` workflows.
* Add a ``_system`` user for use by scripts.
* Implement expiry of collection items.
* Add APIs to create workflow templates and workflows.
* Add ``debusine-admin create_workflow`` command.
* Add ``debusine-admin delete_workspace`` command.
* Implement expiry of work requests.

Web UI
~~~~~~

* Fix ordering of work requests by task name.
* Improve rendering of multi-line strings in task data.
* Show workflow information for work requests that are part of workflows.
* Show task type in work request lists.
* Improve handling of expired artifacts in ``autopkgtest``/``lintian``
  views.
* Order a work request's artifacts by ID within each category.
* Show the user who created a work request in the work request detail view.
* Show a notice when a work request's artifacts have expired.
* Add workspace detail and collection views.

Client
~~~~~~

* Separate YAML input and output more clearly when running ``debusine
  create-artifact`` or ``debusine create-work-request``.
* Add ``debusine manage-work-request`` command to adjust work request
  priorities.
* Add ``debusine create-workflow-template`` and ``debusine create-workflow``
  commands.

Worker
~~~~~~

* Add support for passing extra packages to the ``sbuild`` task.
* Exit cleanly on failure to report a completed work request to the server.
* Restrict ``mmdebstrap`` and ``simplesystemimagebuild`` tasks to workers
  that support the requested architecture, as was done for other tasks in
  0.2.1.
* Only consider the ``autopkgtest`` task to have succeeded on exit codes 0,
  2, and 8.
* Remove network-related files that ``mmdebstrap`` copies from the host.
* Allow ``sbuild`` to produce no ``.changes`` file, so that users can
  examine the log files of failed builds.
* Improve "Unexpected artifact type" error from the image cache.
* Rename ``autopkgtest`` task's ``environment`` key to
  ``extra_environment``.
* Rename ``environment_id`` to ``environment`` in all tasks, and support the
  new lookup syntax.
* Drop insecure ``sbuild_options`` from ``sbuild`` task.
* Rename task data fields in ``autopkgtest``, ``blhc``, ``lintian``,
  ``piuparts``, ``sbuild``, and ``updatesuitelintiancollection`` tasks to
  support the new lookup syntax, removing ``_id`` from key names and
  accepting single or multiple lookups as appropriate.
* Correctly tag ``sid`` tarballs and images as ``codename=sid``.
* Don't purge build-dependencies after build in the ``sbuild`` task.

Documentation
~~~~~~~~~~~~~

* Move unimplemented features to a new "Development blueprints" section.
* Add design practices.
* Rework "Where to start" section in "Contribute to Debusine".
* Clarify parameters to ``piuparts`` task.
* Clarify the role of Incus when installing a Debusine instance.
* Add design for tasks that update collections.
* Document work request scheduling and associated worker metadata.
* Add design for workflows.
* Document image caching and cleanup.
* Add design for scheduling priorities.
* Add design for collection item lookups.
* Add design for ``sbuild`` workflow.
* Add design for ``update_environments`` workflow.
* Add how-to for setting up APT mirroring.
* Add example script to automate Incus configuration for workers.
* Document packages required for Incus VMs.
* Add example script to populate a Debusine instance with example data.
* Document environment requirements for executor backends.
* Update "Getting started with Debusine" tutorial to use workflows and
  collections.
* Add more documentation of worker behaviour.

Quality
~~~~~~~

* Validate the summary in :artifact:`debian:lintian` artifacts.
* Drop compatibility with Debian bullseye; Debusine now requires Python >=
  3.11.
* Enforce pydantic models for ``WorkRequest.workflow_data`` and
  ``WorkRequest.event_reactions``.
* Use pydantic models for ``autopkgtest`` and ``lintian`` views.
* Fix some tests on non-amd64 architectures.
* Auto-format HTML templates using djlint.
* Add infrastructure for more semantic testing of HTML output.

.. _release-0.2.1:

0.2.1 (2024-03-07)
------------------

Server
~~~~~~

* Add a Celery worker for server-side tasks.

Client
~~~~~~

* Trim down dependencies slightly.

Worker
~~~~~~

* Require KVM access for ``simplesystemimagebuild`` task.
* Change ``container`` to ``instance`` in Incus templates.
* Log task completion.
* Restrict tasks to workers that support the requested architecture.

Documentation
~~~~~~~~~~~~~

* Improve home page slightly.

Quality
~~~~~~~

* Enforce mypy project-wide, including all Django components.

.. _release-0.2.0:

0.2.0 (2024-02-29)
------------------

Highlights:

* Added artifact file storage system.
* Debian developers can use Debusine to run various QA tasks against
  packages they are preparing.  Those tasks can be scheduled through the API
  or through the web UI.

Note that it is not possible to directly migrate a database installed using
0.1.0.  Migrations from this release to future releases will be possible.

Server
~~~~~~

* Implement file storage.
* Implement artifact handling.
* Implement expiration of artifacts and their files.
* Run database migrations on ``debusine-server`` package upgrade.
* Add ``debusine-admin monthly_cleanup`` command, run from a systemd timer.
* Link work requests to workspaces.
* Add ``debusine-admin create_user``, ``debusine-admin list_users``, and
  ``debusine-admin manage_user`` commands.
* Link tokens to users.
* Allow email notifications if a work request fails.
* Depend on ``python3-daphne``.
* Ensure all local artifacts are JSON-serializable.
* Add ``debusine-admin create_workspace``, ``debusine-admin
  list_workspaces``, and ``debusine-admin manage_workspace`` commands.
* Use WorkRequest workspace in artifacts.
* Add default expiration delay to workspaces.
* Add API to list work requests.
* Make sure the Django app's secret key is never publicly readable.
* Mark workers as disconnected on ``debusine-server`` startup.
* Use ``Restart=on-failure`` rather than ``Restart=always`` in
  ``debusine-server.service``.
* Add ``debusine-admin info`` command to help with setting up deployments.
* Add daily artifact cleanup timer.
* Use pydantic models for artifact data.
* Add remote, read-only file storage backend for :file-backend:`external
  Debian archives <ExternalDebianSuite>`.

Web UI
~~~~~~

* Add web UI for work requests and workspaces.
* Add login/logout support to web UI, allowing access to non-public
  workspaces.
* Allow registering/removing user API keys using the web UI.
* Allow uploading artifacts using the web UI.
* Refinements to web UI for work requests.
* Make Django aware of HTTP/HTTPS state of requests.
* Fix download error with empty artifact file and document mmap usage.
* Implement integration with Salsa Single Sign-On.
* Add ``lintian`` view.
* Polish various aspects of the web UI.
* Add ``autopkgtest`` view.
* Fetch images for tasks directly, not via a tarball.

Client
~~~~~~

* Rename client's configuration key from ``debusine`` to ``api-url``.
* Add ``--data`` option to ``debusine create-work-request``.
* Rename ``debusine work-request-status`` to ``debusine show-work-request``.
* Add ``debusine on-work-request-completed`` to allow running a command when
  a work request completes.
* ``debusine.client``: Drop obsolete ``silent`` keyword, and stricter
  prototype tests.
* Add ``debusine --debug`` option to debug HTTP traffic.
* Implement a package downloader (``dget``).
* Implement a paginated listing API client.
* Add API client method for listing all work requests.
* Add ``debusine list-work-requests`` command.
* Add ``debusine import-debian-artifact`` command.

Worker
~~~~~~

* Modify ``sbuild`` task to use artifacts.
* Add pre-upload consistency checks on sbuild results.
* Rename worker's configuration key from ``debusine-url`` to ``api-url``.
* Upload ``sbuild`` log files even if the .dsc file did not exist.
* Add ``piuparts`` task.
* Add ``lintian`` task.
* Add ``autopkgtest`` task.
* Add ``mmdebstrap`` task.
* Avoid trying to add ``debusine-worker`` user in postinst if it already
  exists.
* Add image caching for executor backends.
* Add ``unshare`` executor.
* Port the ``autopkgtest`` and ``piuparts`` tasks to ``unshare``.
* Use ``Restart=on-failure`` rather than ``Restart=always`` in
  ``debusine-worker.service``.
* Make tasks check whether their tools are installed.
* Use a lock to protect execution of the work request.
* Add ``blhc`` task.
* Add ``simplesystemimagebuild`` task.
* Use pydantic models for task data.
* Log exceptions in task preparation and clean-up.
* Add Incus executor (for both containers and VMs).
* Add a ``qemu`` executor, currently only for ``autopkgtest`` and ``sbuild``
  tasks.

Documentation
~~~~~~~~~~~~~

* Drop the "slug" field and the "repository" type.
* Document :artifact:`debian:package-build-log` artifact in ontology.
* Document using ``local.py`` to change settings.
* Create an overview document with an elevator-pitch-style introduction.
* Add initial design for ``autopkgtest`` and ``lintian`` tasks.
* Add initial design for system tarball artifacts and debootstrap-like
  tasks.
* Add initial design for tasks building system disk images.
* Update the description of the ``sbuild`` task.
* Restructure the documentation following the Diátaxis principles.
* Clarify copyright notice, contributor status and list of contributors.
* Enable the Sphinx copybutton plugin.
* Add some documentation for the Python client API.
* Improve the "Getting started with Debusine" tutorial.
* Add documentation for ``debusine-admin`` commands.
* Add "Install your first Debusine instance" tutorial.
* Add initial design for collections.
* Refine design for workflows.

Quality
~~~~~~~

* Harmonize license to be GPL-3+ everywhere.
* Support pydantic 1 and 2.
* Apply mypy, pyupgrade, and shellcheck consistently.
* Sync ``(Build-)Depends`` with ``setup.cfg``.

.. _release-0.1.0:

0.1.0 (2022-09-09)
------------------

Initial release.  Includes a server that can drive many workers over a
worker-initiated websocket connection, where the workers use the server's
API to get work requests and provide results.  There is an ``sbuild`` task
that workers can run.
