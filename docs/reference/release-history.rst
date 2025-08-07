.. _release-history:

===============
Release history
===============

.. towncrier release notes start

.. _release-0.11.1:

0.11.1 (2025-05-04)
-------------------

Server
~~~~~~

Bug Fixes
^^^^^^^^^

- Set ``Vary: Cookie, Token`` on all HTTP responses. (`#761
  <https://salsa.debian.org/freexian-team/debusine/-/issues/761>`__)
- Return multiple lookup results in a predictable order, to make it easier for
  workflows to be idempotent. (`#796
  <https://salsa.debian.org/freexian-team/debusine/-/issues/796>`__)
- Fix regression in ``update_workflows`` Celery task.


Web UI
~~~~~~

Features
^^^^^^^^

- Add a :ref:`debdiff <artifact-debdiff>` artifact view. (`#714
  <https://salsa.debian.org/freexian-team/debusine/-/issues/714>`__)
- Added list and detail views for WorkerPool. (`#733
  <https://salsa.debian.org/freexian-team/debusine/-/issues/733>`__)
- Add number of files in the "Files" tab of the artifact view.
- Redesigned table sorting and header rendering.


Bug Fixes
^^^^^^^^^

- Redesigned table filtering. (`#475
  <https://salsa.debian.org/freexian-team/debusine/-/issues/475>`__)
- Search collection page: fix "str failed to render" error in table headers.
  (`#799 <https://salsa.debian.org/freexian-team/debusine/-/issues/799>`__)
- :ref:`Autopkgtest <task-autopkgtest>`: Render extra repositories as deb822
  sources. (`#828
  <https://salsa.debian.org/freexian-team/debusine/-/issues/828>`__)
- Change the default tab in the artifact view to "Files". (`#848
  <https://salsa.debian.org/freexian-team/debusine/-/issues/848>`__)
- :ref:`Autopkgtest <task-autopkgtest>`: Fix the "Distribution" field.


Miscellaneous
^^^^^^^^^^^^^

- `#420 <https://salsa.debian.org/freexian-team/debusine/-/issues/420>`__,
  `#814 <https://salsa.debian.org/freexian-team/debusine/-/issues/814>`__


Client
~~~~~~

Features
^^^^^^^^

- ``debusine setup``: Manage the default server setting. (`#780
  <https://salsa.debian.org/freexian-team/debusine/-/issues/780>`__)


Bug Fixes
^^^^^^^^^

- Wrap Debusine errors so that they're shown cleanly by ``dput-ng``. (`#827
  <https://salsa.debian.org/freexian-team/debusine/-/issues/827>`__)
- Improve logging while uploading individual files to artifacts. (`#839
  <https://salsa.debian.org/freexian-team/debusine/-/issues/839>`__)
- Fix handling of responses without ``Content-Type``.


Workflows
~~~~~~~~~

Features
^^^^^^^^

- Allow overriding the ``environment`` in the :ref:`piuparts workflow
  <workflow-piuparts>`.
  Allow overriding the ``piuparts_environment`` in the :ref:`qa <workflow-qa>`
  and :ref:`debian-pipeline <workflow-debian-pipeline>` workflows. (`#638
  <https://salsa.debian.org/freexian-team/debusine/-/issues/638>`__)


Bug Fixes
^^^^^^^^^

- In :ref:`autopkgtest <workflow-autopkgtest>`, :ref:`piuparts
  <workflow-piuparts>` and :ref:`sbuild <workflow-sbuild>` workflows, extend
  children's ``extra_repositories`` with overlay repositories (e.g.
  ``experimental``) if ``codename`` is a known overlay. (`#780
  <https://salsa.debian.org/freexian-team/debusine/-/issues/780>`__)
- :ref:`make_signed_source <workflow-make-signed-source>`: Disambiguate
  handling of multiple signing templates for a single architecture.

  :ref:`make_signed_source <workflow-make-signed-source>`: Provide
  :ref:`debian:upload <artifact-upload>` artifacts as ``signed-source-*``
  outputs, not :ref:`debian:source-package <artifact-source-package>`.

  :ref:`debian_pipeline <workflow-debian-pipeline>`: Upload signed source
  packages and their binaries if necessary. (`#796
  <https://salsa.debian.org/freexian-team/debusine/-/issues/796>`__)
- :ref:`sbuild <workflow-sbuild>`: Improve workflow orchestration error when
  no environments were found.  (`#830
  <https://salsa.debian.org/freexian-team/debusine/-/issues/830>`__)


Tasks
~~~~~

Bug Fixes
^^^^^^^^^

- :ref:`lintian <task-lintian>`: Use ``lintian --print-version`` to extract
  the version. (`#609
  <https://salsa.debian.org/freexian-team/debusine/-/issues/609>`__)
- Fix a variety of bugs in :ref:`task-simplesystemimagebuild` image builds,
  that broke use with the ``incus-vm`` and ``qemu`` executors.
  Only require the ``python3-minimal`` package to be installed for the ``qemu``
  executor. (`#664
  <https://salsa.debian.org/freexian-team/debusine/-/issues/664>`__)
- :ref:`DebDiff <task-debdiff>`: Install ``diffstat`` package, to make the
  ``--diffstat`` flag work. (`#748
  <https://salsa.debian.org/freexian-team/debusine/-/issues/748>`__)
- :ref:`DebDiff <task-debdiff>`: Create ``relates-to`` relations to binary
  artifacts.


Worker
~~~~~~

Bug Fixes
^^^^^^^^^

- Incus LXC instances now wait for ``systemd-networkd`` to declare the network
  online, before running autopkgtests. (`#812
  <https://salsa.debian.org/freexian-team/debusine/-/issues/812>`__)


General
~~~~~~~

Documentation
^^^^^^^^^^^^^

- Add new project management practices page. (`#784
  <https://salsa.debian.org/freexian-team/debusine/-/issues/784>`__)
- Update playground setup advice. (`#797
  <https://salsa.debian.org/freexian-team/debusine/-/issues/797>`__)
- Update the introduction with more recent content.


.. _release-0.11.0:

0.11.0 (2025-04-15)
-------------------

Server
~~~~~~

Features
^^^^^^^^

- Delete artifacts that were created more than a day ago and are still
  incomplete. (`#667
  <https://salsa.debian.org/freexian-team/debusine/-/issues/667>`__)


Bug Fixes
^^^^^^^^^

- Don't create a workflow if its input validation fails. (`#432
  <https://salsa.debian.org/freexian-team/debusine/-/issues/432>`__)
- Only retry work requests up to three times in a row due to worker failures.
  (`#477 <https://salsa.debian.org/freexian-team/debusine/-/issues/477>`__)
- Rename ``debusine-server-artifacts-cleanup.{service,timer}`` to
  ``debusine-server-delete-expired.{service,timer}``, to better reflect the
  function of those units. (`#636
  <https://salsa.debian.org/freexian-team/debusine/-/issues/636>`__)
- :ref:`APTMirror <task-apt-mirror>`: Ensure that only one mirroring task for a
  given collection runs at once. (`#694
  <https://salsa.debian.org/freexian-team/debusine/-/issues/694>`__)
- Don't set the Celery worker's concurrency to 1 in the database when starting
  the scheduler or provisioner service. (`#751
  <https://salsa.debian.org/freexian-team/debusine/-/issues/751>`__)
- Record errors from server tasks in ``WorkRequest.output_data``. (`#785
  <https://salsa.debian.org/freexian-team/debusine/-/issues/785>`__)
- Optimize computing the runtime status of large workflows.
  Batch expensive workflow updates and defer them to a Celery task. (`#786
  <https://salsa.debian.org/freexian-team/debusine/-/issues/786>`__)


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
  becomes ``…/<artifact_id>/file/<path>``. (`#621
  <https://salsa.debian.org/freexian-team/debusine/-/issues/621>`__)


Features
^^^^^^^^

- Better usability for the token generation UI: copy token to clipboard, show a
  config snippet with the token. (`#421
  <https://salsa.debian.org/freexian-team/debusine/-/issues/421>`__)
- Downloading an artifact without the archive= query parameter autodetects the
  file type.

  This means that a download will by default produce a tarball only if the
  artifact contains more than one file. One can explicitly add
  ``?archive=tar.gz`` to force always returning a tarball. (`#621
  <https://salsa.debian.org/freexian-team/debusine/-/issues/621>`__)
- Add view raw and download buttons to all file display widgets. (`#621
  <https://salsa.debian.org/freexian-team/debusine/-/issues/621>`__)
- Add an indication to ``/-/status/workers/`` showing each worker's pool.
  Exclude inactive pool workers from ``/-/status/workers/``.
  Add worker details page. (`#733
  <https://salsa.debian.org/freexian-team/debusine/-/issues/733>`__)


Bug Fixes
^^^^^^^^^

- Work requests now show validation/configuration errors. (`#651
  <https://salsa.debian.org/freexian-team/debusine/-/issues/651>`__)


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

- Fix file uploads if ``api-url`` is configured with a trailing slash. (`#793
  <https://salsa.debian.org/freexian-team/debusine/-/issues/793>`__)


Workflows
~~~~~~~~~

Features
^^^^^^^^

- Restrict starting workflows to workspace contributors. (`#625
  <https://salsa.debian.org/freexian-team/debusine/-/issues/625>`__)


Bug Fixes
^^^^^^^^^

- Record errors from ``Workflow.ensure_dynamic_data``. (`#589
  <https://salsa.debian.org/freexian-team/debusine/-/issues/589>`__)
- Record orchestrator errors in ``WorkRequest.output_data``.
  :ref:`reverse_dependencies_autopkgtest
  <workflow-reverse-dependencies-autopkgtest>`: Validate ``suite_collection``
  parameter. (`#651
  <https://salsa.debian.org/freexian-team/debusine/-/issues/651>`__)
- Use ``|`` instead of ``/`` as a collection item prefix separator in
  workflows, since ``/`` is used to separate lookup string segments.
  :ref:`reverse_dependencies_autopkgtest
  <workflow-reverse-dependencies-autopkgtest>`: Fix orchestration failure for
  source package versions containing a colon.


Tasks
~~~~~

Features
^^^^^^^^

- :ref:`MergeUploads <task-merge-uploads>`: Reimplement ``mergechanges`` in
  Python, for efficiency and to avoid problems with buggy versions of ``mawk``
  in some old Debian releases. (`#512
  <https://salsa.debian.org/freexian-team/debusine/-/issues/512>`__)


Bug Fixes
^^^^^^^^^

- :ref:`ExtractForSigning <task-extract-for-signing>`: Tolerate overlap between
  template and binary artifacts. (`#763
  <https://salsa.debian.org/freexian-team/debusine/-/issues/763>`__)


Signing
~~~~~~~

Documentation
^^^^^^^^^^^^^

- Document how to find generated signing keys. (`#771
  <https://salsa.debian.org/freexian-team/debusine/-/issues/771>`__)


General
~~~~~~~

Documentation
^^^^^^^^^^^^^

- Rework :ref:`tutorial-getting-started` to create a workflow. (`#764
  <https://salsa.debian.org/freexian-team/debusine/-/issues/764>`__)


Miscellaneous
^^^^^^^^^^^^^

- `#743 <https://salsa.debian.org/freexian-team/debusine/-/issues/743>`__


.. _release-0.10.0:

0.10.0 (2025-04-02)
-------------------

Server
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :ref:`CreateExperimentWorkspace <task-create-experiment-workspace>`: Redefine
  ``expiration_delay`` as a number of days rather than a duration.
- Use Debusine permissions for managing workflow templates.  If you previously
  granted yourself the ``add_workflowtemplate`` permission, see the
  :ref:`updated tutorial <tutorial-getting-started>` for how to grant yourself
  owner access to a workspace.


Features
^^^^^^^^

- Store worker pool statistics on task completion and worker shutdown.
  Implement provisioning of pool workers. (`#721
  <https://salsa.debian.org/freexian-team/debusine/-/issues/721>`__)


Bug Fixes
^^^^^^^^^

- Retry any running work requests when terminating pool workers. (`#731
  <https://salsa.debian.org/freexian-team/debusine/-/issues/731>`__)
- Limit status views of running external tasks (``/api/1.0/service-status/``
  and ``/-/status/queue/``) to worker tasks. (`#750
  <https://salsa.debian.org/freexian-team/debusine/-/issues/750>`__)


Documentation
^^^^^^^^^^^^^

- Document cloud worker pools and storage. (`#735
  <https://salsa.debian.org/freexian-team/debusine/-/issues/735>`__)


Web UI
~~~~~~

Features
^^^^^^^^

- Add an audit log for group-related changes. (`#734
  <https://salsa.debian.org/freexian-team/debusine/-/issues/734>`__)


Bug Fixes
^^^^^^^^^

- Fix link to workflows that need input.


Client
~~~~~~

Features
^^^^^^^^

- Add ``debusine setup`` for editing server configuration interactively. (`#711
  <https://salsa.debian.org/freexian-team/debusine/-/issues/711>`__)
- Add ``dput-ng`` integration. (`#713
  <https://salsa.debian.org/freexian-team/debusine/-/issues/713>`__)


Bug Fixes
^^^^^^^^^

- ``debusine provide-signature``: Always pass ``--re-sign`` to ``debsign``.
  (`#713 <https://salsa.debian.org/freexian-team/debusine/-/issues/713>`__)


Workflows
~~~~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :ref:`create_experiment_workspace <workflow-create-experiment-workspace>`:
  Redefine ``expiration_delay`` as a number of days rather than a duration.


Bug Fixes
^^^^^^^^^

- :ref:`make_signed_source <workflow-make-signed-source>`: Pass unsigned binary
  artifacts to :ref:`sbuild <workflow-sbuild>` sub-workflow via
  ``input.extra_binary_artifacts``. (`#727
  <https://salsa.debian.org/freexian-team/debusine/-/issues/727>`__)
- :ref:`autopkgtest <workflow-autopkgtest>`, :ref:`lintian <workflow-lintian>`:
  Handle :ref:`debian:upload <artifact-upload>` source artifacts without
  original upstream source. (`#744
  <https://salsa.debian.org/freexian-team/debusine/-/issues/744>`__)


Documentation
^^^^^^^^^^^^^

- :ref:`make_signed_source <workflow-make-signed-source>`: No longer document
  :ref:`debian:binary-packages <artifact-binary-packages>` artifacts as being
  accepted in ``binary_artifacts``; they never worked. (`#747
  <https://salsa.debian.org/freexian-team/debusine/-/issues/747>`__)


Tasks
~~~~~

Features
^^^^^^^^

- :ref:`Sbuild <task-sbuild>`: Accept ``debian:upload`` artifacts in
  ``input.extra_binary_artifacts``. (`#727
  <https://salsa.debian.org/freexian-team/debusine/-/issues/727>`__)


Bug Fixes
^^^^^^^^^

- :ref:`ExtractForSigning <task-extract-for-signing>`: If given
  :ref:`debian:upload <artifact-upload>` artifacts in ``binary_artifacts``,
  follow ``extends`` relationships to find the underlying
  :ref:`debian:binary-package <artifact-binary-package>` artifacts. (`#747
  <https://salsa.debian.org/freexian-team/debusine/-/issues/747>`__)
- Handle errors while fetching task input more gracefully. (`#763
  <https://salsa.debian.org/freexian-team/debusine/-/issues/763>`__)


.. _release-0.9.1:

0.9.1 (2025-03-24)
------------------

Server
~~~~~~

Features
^^^^^^^^

- Automatically add task runs to the appropriate :ref:`debusine:task-history
  collection <collection-task-history>`. (`#510
  <https://salsa.debian.org/freexian-team/debusine/-/issues/510>`__)
- Support Hetzner Object Storage.
  Support worker pools on Hetzner Cloud. (`#543
  <https://salsa.debian.org/freexian-team/debusine/-/issues/543>`__)
- Accept scope prefixes in ``debusine-admin create_collection --workspace`` and
  ``debusine-admin create_work_request --workspace``. (`#608
  <https://salsa.debian.org/freexian-team/debusine/-/issues/608>`__)
- Implement ``populate`` and ``drain`` storage policies in ``debusine-admin
  vacuum_storage``.
  Implement store-level ``soft_max_size`` and ``max_size`` limits in
  ``debusine-admin vacuum_storage``. (`#684
  <https://salsa.debian.org/freexian-team/debusine/-/issues/684>`__)
- :ref:`debusine:cloud-provider-account asset <asset-cloud-provider-account>`:
  Add optional ``configuration.s3_endpoint_url`` for the ``aws`` provider type.
  (`#685 <https://salsa.debian.org/freexian-team/debusine/-/issues/685>`__)
- Add roles to group memberships. (`#697
  <https://salsa.debian.org/freexian-team/debusine/-/issues/697>`__)
- Add ``debusine-admin worker_pool`` command.
  Add internal per-provider API for launching and terminating dynamic workers.
  (`#720 <https://salsa.debian.org/freexian-team/debusine/-/issues/720>`__)
- Support worker pools on AWS EC2. (`#722
  <https://salsa.debian.org/freexian-team/debusine/-/issues/722>`__)


Bug Fixes
^^^^^^^^^

- Add a ``DEBUSINE_DEFAULT_WORKSPACE`` Django setting, for use if the default
  workspace has been renamed to something other than "System". (`#571
  <https://salsa.debian.org/freexian-team/debusine/-/issues/571>`__)
- Only upload to write-only stores when applying the ``populate`` storage
  policy in ``debusine-admin vacuum_storage``, not elsewhere. (`#684
  <https://salsa.debian.org/freexian-team/debusine/-/issues/684>`__)


Documentation
^^^^^^^^^^^^^

- Document file stores. (`#541
  <https://salsa.debian.org/freexian-team/debusine/-/issues/541>`__)
- Document :ref:`task-create-experiment-workspace`. (`#542
  <https://salsa.debian.org/freexian-team/debusine/-/issues/542>`__)


Web UI
~~~~~~

Features
^^^^^^^^

- Add web UI for group management: list groups, add/remove users, change user
  roles. (`#542
  <https://salsa.debian.org/freexian-team/debusine/-/issues/542>`__)


Bug Fixes
^^^^^^^^^

- Do not show "Plumbing" in the navigation bar if the view is not
  workspace-aware. (`#675
  <https://salsa.debian.org/freexian-team/debusine/-/issues/675>`__)


Workflows
~~~~~~~~~

Features
^^^^^^^^

- :ref:`package_publish <workflow-package-publish>`: Copy :ref:`task-history
  <collection-task-history>` items from the same workflow. (`#510
  <https://salsa.debian.org/freexian-team/debusine/-/issues/510>`__)


Documentation
^^^^^^^^^^^^^

- Document :ref:`create_experiment_workspace
  <workflow-create-experiment-workspace>`. (`#542
  <https://salsa.debian.org/freexian-team/debusine/-/issues/542>`__)
- Document how to implement a new workflow. (`#693
  <https://salsa.debian.org/freexian-team/debusine/-/issues/693>`__)


Tasks
~~~~~

Features
^^^^^^^^

- :ref:`MmDebstrap <task-mmdebstrap>`, :ref:`SimpleSystemImageBuild
  <task-simplesystemimagebuild>`: Support reading keyrings from
  ``/usr/local/share/keyrings/``. (`#739
  <https://salsa.debian.org/freexian-team/debusine/-/issues/739>`__)


Worker
~~~~~~

Features
^^^^^^^^

- Add worker activation tokens, which can be used to auto-enable pool workers
  when they start without needing to expose worker tokens in ``cloud-init``
  user-data. (`#732
  <https://salsa.debian.org/freexian-team/debusine/-/issues/732>`__)


General
~~~~~~~

Miscellaneous
^^^^^^^^^^^^^

- `#729 <https://salsa.debian.org/freexian-team/debusine/-/issues/729>`__


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
  longer handle file stores. (`#682
  <https://salsa.debian.org/freexian-team/debusine/-/issues/682>`__)
- Rename ``debusine-admin create_file_store`` command to ``debusine-admin
  file_store create``.  (The old name is still present, but is deprecated.)
  (`#683 <https://salsa.debian.org/freexian-team/debusine/-/issues/683>`__)
- Rename ``debusine-admin monthly_cleanup`` to ``debusine-admin
  vacuum_storage``, and run it daily.  Rename the associated ``systemd`` units
  similarly. (`#684
  <https://salsa.debian.org/freexian-team/debusine/-/issues/684>`__)


Features
^^^^^^^^

- Implement :ref:`task configuration mechanism <task-configuration>`. (`#508
  <https://salsa.debian.org/freexian-team/debusine/-/issues/508>`__)
- Implement :ref:`debusine:task-history collection <collection-task-history>`.
  (`#510 <https://salsa.debian.org/freexian-team/debusine/-/issues/510>`__)
- Add API: ``1.0/asset/`` to create and list :ref:`assets`.
  Add API:
  ``1.0/asset/<str:asset_category>/<str:asset_slug>/<str:permission_name>`` to
  check permissions on :ref:`assets`.
  Add ``debusine-admin asset`` management command to manage asset permissions.
  (`#576 <https://salsa.debian.org/freexian-team/debusine/-/issues/576>`__)
- Add ``debusine-admin scope add_file_store``, ``debusine-admin scope
  edit_file_store``, and ``debusine-admin scope remove_file_store`` commands.
  Add an ``instance_wide`` field to file stores, defaulting to True, which can
  be configured using the ``--instance-wide``/``--no-instance-wide`` options to
  ``debusine-admin file_store create``.  Non-instance-wide file stores may only
  be used by a single scope.
  Add ``soft_max_size`` and ``max_size`` fields to file stores, which can be
  configured using the ``--soft-max-size`` and ``--max-size`` options to
  ``debusine-admin file_store create``. (`#682
  <https://salsa.debian.org/freexian-team/debusine/-/issues/682>`__)
- Add ``debusine-admin scope show`` command.
  Add ``debusine-admin file_store delete`` command.
  Make ``debusine-admin file_store create`` idempotent. (`#683
  <https://salsa.debian.org/freexian-team/debusine/-/issues/683>`__)
- Generalize sweeps by ``debusine-admin vacuum_storage`` over files in local
  storage to be able to handle other backends. (`#684
  <https://salsa.debian.org/freexian-team/debusine/-/issues/684>`__)
- Add ``debusine-admin asset create`` command.
  Add an S3 file backend.
  Add ``--provider-account`` option to ``debusine-admin file_store create``, to
  allow linking file stores to cloud provider accounts. (`#685
  <https://salsa.debian.org/freexian-team/debusine/-/issues/685>`__)
- Add :ref:`debusine:cloud-provider-account assets
  <asset-cloud-provider-account>`. (`#696
  <https://salsa.debian.org/freexian-team/debusine/-/issues/696>`__)
- Implement ephemeral groups. (`#697
  <https://salsa.debian.org/freexian-team/debusine/-/issues/697>`__)
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
  build logs. (`#635
  <https://salsa.debian.org/freexian-team/debusine/-/issues/635>`__)
- Explicitly depend on ``libjs-select2.js`` in the ``debusine-server`` package.
- Set current context when running server tasks.


Documentation
^^^^^^^^^^^^^

- Add blueprint for dynamic cloud compute scaling. (`#538
  <https://salsa.debian.org/freexian-team/debusine/-/issues/538>`__)
- Add blueprint for dynamic cloud storage scaling. (`#539
  <https://salsa.debian.org/freexian-team/debusine/-/issues/539>`__)
- Split artifacts documentation by category. (`#541
  <https://salsa.debian.org/freexian-team/debusine/-/issues/541>`__)
- Add blueprint for cloning workspaces for experiments.
  Add blueprint for granting ``ADMIN`` roles on groups to users. (`#542
  <https://salsa.debian.org/freexian-team/debusine/-/issues/542>`__)


Miscellaneous
^^^^^^^^^^^^^

- `#666 <https://salsa.debian.org/freexian-team/debusine/-/issues/666>`__,
  `#704 <https://salsa.debian.org/freexian-team/debusine/-/issues/704>`__


Web UI
~~~~~~

Features
^^^^^^^^

- Workspaces can now be set to expire. Owners can configure this and other
  attributes in the web UI. (`#698
  <https://salsa.debian.org/freexian-team/debusine/-/issues/698>`__)
- Display configured task data (see :ref:`task-configuration`) in views that
  display work requests. (`#707
  <https://salsa.debian.org/freexian-team/debusine/-/issues/707>`__)
- ``/{scope}/{workspace}/workflow/``: Add ``label`` tag to "With failed work
  requests", to allow enabling/disabling the checkbox by clicking on the text.


Bug Fixes
^^^^^^^^^

- Fix collection item detail URLs to allow slashes in names. (`#676
  <https://salsa.debian.org/freexian-team/debusine/-/issues/676>`__)
- Handle empty Lintian artifacts. (`#677
  <https://salsa.debian.org/freexian-team/debusine/-/issues/677>`__)
- Filter workflow template detail view to the current workspace. (`#680
  <https://salsa.debian.org/freexian-team/debusine/-/issues/680>`__)
- Preserve redirect URL on login. (`#717
  <https://salsa.debian.org/freexian-team/debusine/-/issues/717>`__)
- Fix title of homepage and scope pages.


Client
~~~~~~

Features
^^^^^^^^

- Add ``asset_create`` and ``asset_list`` methods to create and list
  :ref:`assets`.
  Add ``create-asset`` and ``list-assets`` commands to create and list assets.
  Add ``asset_permission_check`` method to check permissions on :ref:`assets`.
  (`#576 <https://salsa.debian.org/freexian-team/debusine/-/issues/576>`__)


Workflows
~~~~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :ref:`debian_pipeline <workflow-debian-pipeline>`, :ref:`make_signed_source
  <workflow-make-signed-source>`, :ref:`package_upload
  <workflow-package-upload>`: Signing keys are now specified by fingerprint,
  rather than a lookup for an asset.
  Remove the ``debian:suite-signing-keys`` collection. (`#576
  <https://salsa.debian.org/freexian-team/debusine/-/issues/576>`__)


Features
^^^^^^^^

- Add ``subject`` to dynamic data for all workflows. (`#679
  <https://salsa.debian.org/freexian-team/debusine/-/issues/679>`__)
- Add workflow to create an experiment workspace. (`#699
  <https://salsa.debian.org/freexian-team/debusine/-/issues/699>`__)


Bug Fixes
^^^^^^^^^

- :ref:`make_signed_source <workflow-make-signed-source>`: Fix passing of
  ``debusine:signing-input`` artifacts between workflow steps. (`#689
  <https://salsa.debian.org/freexian-team/debusine/-/issues/689>`__)
- Fix handling of dependencies between workflows.  In most cases workflows
  themselves shouldn't have dependencies, but the :ref:`sbuild
  <workflow-sbuild>` sub-workflow created by :ref:`make_signed_source
  <workflow-make-signed-source>` is an exception. (`#690
  <https://salsa.debian.org/freexian-team/debusine/-/issues/690>`__)
- :ref:`make_signed_source <workflow-make-signed-source>`: Pass all outputs
  from the :ref:`task-sign` through to the :ref:`task-assemble-signed-source`,
  not just one of them. (`#692
  <https://salsa.debian.org/freexian-team/debusine/-/issues/692>`__)
- :ref:`make_signed_source <workflow-make-signed-source>`: Fix orchestration of
  :ref:`sbuild <workflow-sbuild>` sub-workflow. (`#695
  <https://salsa.debian.org/freexian-team/debusine/-/issues/695>`__)


Tasks
~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :ref:`Sbuild <task-sbuild>`: Remove ``schroot`` support. (`#660
  <https://salsa.debian.org/freexian-team/debusine/-/issues/660>`__)


Features
^^^^^^^^

- Add ``subject``, ``configuration_context``, and ``runtime_context`` to
  dynamic data for all worker tasks. (`#679
  <https://salsa.debian.org/freexian-team/debusine/-/issues/679>`__)


Bug Fixes
^^^^^^^^^

- Fix accidental leakage of keyring and customization script names between
  :ref:`task-mmdebstrap` instances on the same worker, leading to task failure.
  (`#686 <https://salsa.debian.org/freexian-team/debusine/-/issues/686>`__)


Worker
~~~~~~

Features
^^^^^^^^

- Record runtime statistics for tasks. (`#510
  <https://salsa.debian.org/freexian-team/debusine/-/issues/510>`__)
- Log task stages to a work request debug log as well.


Bug Fixes
^^^^^^^^^

- Fix various worker asyncio issues.


Signing
~~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :ref:`task-generate-key`: The result is now a ``debusine:signing-key``
  :ref:`asset <assets>` rather than an :ref:`artifact <artifact-reference>`.
  :ref:`task-debsign`, :ref:`task-sign`: The ``key`` parameter is now the key's
  fingerprint, rather than an asset lookup.
  :ref:`task-sign`, :ref:`task-debsign`: The ``signer`` role is required on
  signing key assets, by the work request creator. (`#576
  <https://salsa.debian.org/freexian-team/debusine/-/issues/576>`__)


Features
^^^^^^^^

- Allow recording username and resource data in the signing service audit log.
  Record the username and resource description in the audit log, in the
  :ref:`task-sign` and :ref:`task-debsign`. (`#576
  <https://salsa.debian.org/freexian-team/debusine/-/issues/576>`__)


General
~~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Add a new primitive, :ref:`assets`, to represent objects that need
  permissions, like :ref:`asset-signing-key`.
  Existing work requests and workflows are migrated to refer to signing keys by
  fingerprint.
  Existing ``debusine:signing-key`` artifacts are migrated to assets.
  We recommend that Debusine admins audit their database for any remaining
  artifacts with category ``debusine:signing-key``, and remove them after
  confirming that they have been migrated to assets. This will require removing
  any related artifact relations first. Audit query: ``SELECT * FROM
  db_artifact WHERE category='debusine:signing-key';`` (`#576
  <https://salsa.debian.org/freexian-team/debusine/-/issues/576>`__)


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
  (``/<scope>/<workspace>/workflow-template/<workflow-template>/``). (`#400
  <https://salsa.debian.org/freexian-team/debusine/-/issues/400>`__)


Bug Fixes
^^^^^^^^^

- Use an in-memory channel layer for tests, rather than Redis. (`#617
  <https://salsa.debian.org/freexian-team/debusine/-/issues/617>`__)
- Fix cleanup of expired work requests referenced by internal collections.
  (`#644 <https://salsa.debian.org/freexian-team/debusine/-/issues/644>`__)
- Retry any work requests that a worker is currently running when it asks for a
  new work request. (`#667
  <https://salsa.debian.org/freexian-team/debusine/-/issues/667>`__)
- Fix tests with python-debian >= 0.1.50. (`#672
  <https://salsa.debian.org/freexian-team/debusine/-/issues/672>`__)


Documentation
^^^^^^^^^^^^^

- Split collections documentation by category. (`#541
  <https://salsa.debian.org/freexian-team/debusine/-/issues/541>`__)


Web UI
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Reorganize ``/-/user/`` URLs to contain the user name, and move the logout
  view to ``/-/logout/``. (`#649
  <https://salsa.debian.org/freexian-team/debusine/-/issues/649>`__)
- Remove ``/view/`` from workspace view path (``/<scope>/<workspace>/view/``).


Features
^^^^^^^^

- Add workflows split-button pulldown to base template. (`#620
  <https://salsa.debian.org/freexian-team/debusine/-/issues/620>`__)
- For workflows that need input, link to the first work request that needs
  input. (`#674
  <https://salsa.debian.org/freexian-team/debusine/-/issues/674>`__)
- Add a user detail view.
- Extend workspace detail view to show figures about workflows.
- Use `select2 <https://select2.org/>`__ for the multiple choice fields on the
  workflow list form.


Bug Fixes
^^^^^^^^^

- Hide collections with the category ``workflow-internal`` from the navbar
  collections dropdown. (`#639
  <https://salsa.debian.org/freexian-team/debusine/-/issues/639>`__)
- Return 404 when trying to view incomplete files, rather than logging a noisy
  traceback.
  Don't link to incomplete files, and mark them as "(incomplete)".
  Mark artifacts as incomplete in artifact lists if any of their files are
  incomplete. (`#667
  <https://salsa.debian.org/freexian-team/debusine/-/issues/667>`__)
- Fix ordering of workers list by "Last seen". (`#669
  <https://salsa.debian.org/freexian-team/debusine/-/issues/669>`__)


Workflows
~~~~~~~~~

Features
^^^^^^^^

- :ref:`debian_pipeline <workflow-debian-pipeline>`, :ref:`qa <workflow-qa>`,
  :ref:`reverse_dependencies_autopkgtest
  <workflow-reverse-dependencies-autopkgtest>`, :ref:`sbuild
  <workflow-sbuild>`: Support ``debian:upload`` artifacts as input. (`#590
  <https://salsa.debian.org/freexian-team/debusine/-/issues/590>`__)
- :ref:`autopkgtest <workflow-autopkgtest>`, :ref:`piuparts
  <workflow-piuparts>`, :ref:`reverse_dependencies_autopkgtest
  <workflow-reverse-dependencies-autopkgtest>`, :ref:`qa <workflow-qa>`,
  :ref:`debian_pipeline <workflow-debian-pipeline>`: Add support for
  ``extra_repositories``. (`#622
  <https://salsa.debian.org/freexian-team/debusine/-/issues/622>`__)


Bug Fixes
^^^^^^^^^

- Fix looking up the architecture from a lookup that returns an artifact from a
  collection. (`#661
  <https://salsa.debian.org/freexian-team/debusine/-/issues/661>`__)


Tasks
~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :ref:`Autopkgtest <task-autopkgtest>`: Replace the ``extra_apt_sources``
  property with ``extra_repositories``, following the same syntax as
  :ref:`Sbuild <task-sbuild>`. (`#622
  <https://salsa.debian.org/freexian-team/debusine/-/issues/622>`__)


Features
^^^^^^^^

- Gather runtime statistics from executors. (`#510
  <https://salsa.debian.org/freexian-team/debusine/-/issues/510>`__)
- :ref:`Piuparts <task-piuparts>`: Add support for ``extra_repositories``.
  (`#622 <https://salsa.debian.org/freexian-team/debusine/-/issues/622>`__)
- :ref:`SimpleSystemImageBuild <task-simplesystemimagebuild>`: Switch from
  debos to debefivm-create for VM image creation. This also drops support for
  the Debian Jessie release.


Bug Fixes
^^^^^^^^^

- :ref:`Piuparts <task-piuparts>`: Compress processed base tarball for pre-1.3
  compatibility. (`#638
  <https://salsa.debian.org/freexian-team/debusine/-/issues/638>`__)


General
~~~~~~~

Miscellaneous
^^^^^^^^^^^^^

- `#648 <https://salsa.debian.org/freexian-team/debusine/-/issues/648>`__,
  `#670 <https://salsa.debian.org/freexian-team/debusine/-/issues/670>`__


.. _release-0.8.0:

0.8.0 (2024-12-26)
------------------

Server
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Refactor tabular output to also allow machine-readable YAML. (`#247
  <https://salsa.debian.org/freexian-team/debusine/-/issues/247>`__)
- Add permission checks to all API views that accept user authentication.
  (`#568 <https://salsa.debian.org/freexian-team/debusine/-/issues/568>`__)
- Enforce permissions when creating artifacts. (`#614
  <https://salsa.debian.org/freexian-team/debusine/-/issues/614>`__)
- Deprecate ``debusine-admin create_workspace``, ``delete_workspace``,
  ``list_workspace`` and ``manage_workspace`` in favor of
  ``debusine-admin workspace <subcommand>``.
  ``debusine-admin workspace create`` creates workspaces with a default
  30-days expiration delay (instead of no expiration by default for
  ``create_workspace``), and requires an existing owner group to be
  specified. (`#640
  <https://salsa.debian.org/freexian-team/debusine/-/issues/640>`__)
- Enforce permissions when retrying work requests.


Features
^^^^^^^^

- ``debusine-admin create_workspace``: Assign an owners group, controlled by
  the ``--with-owners-group`` option. (`#527
  <https://salsa.debian.org/freexian-team/debusine/-/issues/527>`__)
- Add infrastructure to help enforcing permissions in views. (`#598
  <https://salsa.debian.org/freexian-team/debusine/-/issues/598>`__)
- Record information about any originating workflow template in work requests,
  and add a cached human-readable summary of their most important parameters.
  (`#618 <https://salsa.debian.org/freexian-team/debusine/-/issues/618>`__)
- Implement ``debusine-admin group list`` and ``debusine-admin group members``.
  (`#623 <https://salsa.debian.org/freexian-team/debusine/-/issues/623>`__)
- Add a contributor role for workspaces; contributors can display the workspace
  and create artifacts in it. (`#625
  <https://salsa.debian.org/freexian-team/debusine/-/issues/625>`__)
- Introduce new ``debusine-admin workspace`` subcommand, regrouping and
  expanding the existing ``*_workspace``. See :ref:`debusine-admin
  workspace <debusine-admin-cli-workspace>`. (`#640
  <https://salsa.debian.org/freexian-team/debusine/-/issues/640>`__)
- Allow bare artifact IDs in workflow input.


Bug Fixes
^^^^^^^^^

- Validate new scope, user, collection, and notification channel names. (`#551
  <https://salsa.debian.org/freexian-team/debusine/-/issues/551>`__)
- Allow creating workflows using scoped workspace names. (`#570
  <https://salsa.debian.org/freexian-team/debusine/-/issues/570>`__)
- Report workflow validation errors directly to the client on creation, rather
  than leaving unvalidated workflows lying around in error states. (`#633
  <https://salsa.debian.org/freexian-team/debusine/-/issues/633>`__)
- Set up permissions context when running server tasks. (`#642
  <https://salsa.debian.org/freexian-team/debusine/-/issues/642>`__)
- Port to Django 5.1. (`#646
  <https://salsa.debian.org/freexian-team/debusine/-/issues/646>`__)
- Check work request status when running Celery tasks, to guard against
  mistakes elsewhere.
- Enable Django's ``ATOMIC_REQUESTS`` setting, avoiding a class of mistakes
  where views forget to wrap their changes in a transaction.
- Implement ``add_to_group`` option in signon providers.
- Link externally-signed artifacts to the :ref:`ExternalDebsign
  <task-external-debsign>` work request.


Miscellaneous
^^^^^^^^^^^^^

- `#626 <https://salsa.debian.org/freexian-team/debusine/-/issues/626>`__,
  `#643 <https://salsa.debian.org/freexian-team/debusine/-/issues/643>`__


Web UI
~~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- Drop workspaces from homepage; they are now visible on scope pages instead.
  (`#554 <https://salsa.debian.org/freexian-team/debusine/-/issues/554>`__)
- Move ``/api-auth/`` views to ``/api/auth/``. (`#581
  <https://salsa.debian.org/freexian-team/debusine/-/issues/581>`__)
- Move ``admin``, ``task-status``, ``user``, and ``workers`` views to unscoped
  URLs. (`#582
  <https://salsa.debian.org/freexian-team/debusine/-/issues/582>`__)
- Move account-related views to unscoped URLs. (`#583
  <https://salsa.debian.org/freexian-team/debusine/-/issues/583>`__)
- Move work request URLs under workspaces. (`#584
  <https://salsa.debian.org/freexian-team/debusine/-/issues/584>`__)
- Move artifact URLs under workspaces. (`#585
  <https://salsa.debian.org/freexian-team/debusine/-/issues/585>`__)


Features
^^^^^^^^

- Set the current workspace in views that use it. (`#395
  <https://salsa.debian.org/freexian-team/debusine/-/issues/395>`__)
- Move "Workers" and "Task status" from the navigation bar to the footer.
  Add a per-scope landing page.
  Add a "Collections" menu in workspaces.
  Add view to list and filter workflows. (`#557
  <https://salsa.debian.org/freexian-team/debusine/-/issues/557>`__)
- Show current and other workspaces in base template. (`#624
  <https://salsa.debian.org/freexian-team/debusine/-/issues/624>`__)
- Merge workspace list into scope detail view. (`#629
  <https://salsa.debian.org/freexian-team/debusine/-/issues/629>`__)
- Show the current scope as the "brand", with an optional label and icon.
  (`#630 <https://salsa.debian.org/freexian-team/debusine/-/issues/630>`__)
- Display git-based version information in footer. (`#631
  <https://salsa.debian.org/freexian-team/debusine/-/issues/631>`__)
- Show results in workflow views.
- Show workflow details open by default.


Bug Fixes
^^^^^^^^^

- Silence unnecessary logging when viewing invalid work requests. (`#588
  <https://salsa.debian.org/freexian-team/debusine/-/issues/588>`__)
- Log out via ``POST`` rather than ``GET``. (`#646
  <https://salsa.debian.org/freexian-team/debusine/-/issues/646>`__)
- :ref:`task-external-debsign`: Fix "Waiting for signature" card.
- Consider task type when selecting work request view plugins.
- Fix "Last Seen" and "Status" for Celery workers.
- List workflow templates in workspace detail view.


Documentation
^^^^^^^^^^^^^

- Document scope as required in client configuration, and simplify example if
  there is only one. (`#613
  <https://salsa.debian.org/freexian-team/debusine/-/issues/613>`__)


Miscellaneous
^^^^^^^^^^^^^

- `#645 <https://salsa.debian.org/freexian-team/debusine/-/issues/645>`__


Client
~~~~~~

Documentation
^^^^^^^^^^^^^

- Add documentation for the client configuration file. (`#613
  <https://salsa.debian.org/freexian-team/debusine/-/issues/613>`__)


Workflows
~~~~~~~~~

Features
^^^^^^^^

- Add :ref:`package_publish <workflow-package-publish>` workflow. (`#396
  <https://salsa.debian.org/freexian-team/debusine/-/issues/396>`__)
- Add :ref:`reverse_dependencies_autopkgtest
  <workflow-reverse-dependencies-autopkgtest>` workflow. (`#397
  <https://salsa.debian.org/freexian-team/debusine/-/issues/397>`__)
- :ref:`autopkgtest <workflow-autopkgtest>`, :ref:`sbuild <workflow-sbuild>`:
  Implement ``arch_all_host_architecture``. (`#574
  <https://salsa.debian.org/freexian-team/debusine/-/issues/574>`__)
- :ref:`sbuild <workflow-sbuild>`: Implement ``extra_repositories``. (`#622
  <https://salsa.debian.org/freexian-team/debusine/-/issues/622>`__)
- :ref:`package_upload <workflow-package-upload>`: Support uploading to delayed
  queues.


Bug Fixes
^^^^^^^^^

- :ref:`debian_pipeline <workflow-debian-pipeline>`: Handle some ``build-*``
  promises being missing.
- :ref:`make_signed_source <workflow-make-signed-source>`, :ref:`package_upload
  <workflow-package-upload>`: Fix invalid creation of some child work requests.
  Add validation to catch such problems in future.
- :ref:`package_upload <workflow-package-upload>`: Set correct task type for
  ``ExternalDebsign``.
- Fix work request statuses in several workflows.
- Mark empty workflows as completed.


Documentation
^^^^^^^^^^^^^

- Point to the workflow template list.


Tasks
~~~~~

Incompatible Changes
^^^^^^^^^^^^^^^^^^^^

- :ref:`Sbuild <task-sbuild>`: Stop running ``lintian``; it's now
  straightforward to run both ``sbuild`` and ``lintian`` in sequence using the
  :ref:`debian_pipeline workflow <workflow-debian-pipeline>`. (`#260
  <https://salsa.debian.org/freexian-team/debusine/-/issues/260>`__)


Features
^^^^^^^^

- :ref:`Sbuild <task-sbuild>`: Implement ``extra_repositories``. (`#622
  <https://salsa.debian.org/freexian-team/debusine/-/issues/622>`__)
- :ref:`Lintian <task-lintian>`, :ref:`Piuparts <task-piuparts>`: Capture
  ``apt-get`` output.


Bug Fixes
^^^^^^^^^

- :ref:`Sbuild <task-sbuild>`: Don't count it as a success if the host
  architecture is not supported by the source package. (`#592
  <https://salsa.debian.org/freexian-team/debusine/-/issues/592>`__)
- :ref:`Sbuild <task-sbuild>`: Drop the redundant ``--no-clean`` argument.
  (`#603 <https://salsa.debian.org/freexian-team/debusine/-/issues/603>`__)
- :ref:`Piuparts <task-piuparts>`: Handle ``piuparts`` being in either
  ``/usr/sbin`` or ``/usr/bin``.
- Wait for Incus instances to boot systemd.


Documentation
^^^^^^^^^^^^^

- Split task documentation by task types.


Miscellaneous
^^^^^^^^^^^^^

- `#652 <https://salsa.debian.org/freexian-team/debusine/-/issues/652>`__


Signing
~~~~~~~

Documentation
^^^^^^^^^^^^^

- Add blueprint for restricting use of signing keys. (`#576
  <https://salsa.debian.org/freexian-team/debusine/-/issues/576>`__)


General
~~~~~~~

Features
^^^^^^^^

- Enforce ``mypy``'s strict mode across the whole codebase.


Bug Fixes
^^^^^^^^^

- Ensure consistent ``LANG`` settings in systemd services. (`#494
  <https://salsa.debian.org/freexian-team/debusine/-/issues/494>`__)
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
* Add :ref:`make_signed_source workflow <workflow-make-signed-source>`.
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
* Add :ref:`lintian workflow <workflow-lintian>`.
* Fix ``debusine-admin create_workspace --default-expiration-delay``
  command-line parsing.
* Support lookups that match items of multiple types.
* Add :ref:`piuparts workflow <workflow-piuparts>`.
* Add :ref:`qa workflow <workflow-qa>`.
* Implement ``signing_template_names`` in :ref:`sbuild workflow
  <workflow-sbuild>`.
* Add ``same_work_request`` lookup filter to :ref:`debian:package-build-logs
  collection <collection-package-build-logs>`.
* Add :ref:`debian_pipeline workflow <workflow-debian-pipeline>`.
* Add :ref:`task-copy-collection-items`.

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

* :ref:`SystemBootstrap task <system-bootstrap-task>`:

  * Allow keyring URLs starting with ``file:///usr/share/keyrings/``.
  * Write non-ASCII-armored keyrings to ``.gpg`` rather than ``.asc``.

* :ref:`task-sbuild`:

  * Relax ``binnmu_maintainer`` validation in dynamic data to avoid failures
    if ``DEBUSINE_FQDN`` is under a non-email-suitable domain.
  * Drop unnecessary ``sbuild:host_architecture`` from dynamic metadata.

* Add :ref:`task-debdiff`.

Signing
~~~~~~~

* :ref:`task-sign`:

  * Fail if signing failed.
  * Use detached signatures when signing UEFI files.
  * Take multiple unsigned artifacts and sign them all with the same key.

* Register :ref:`task-debsign`, which previously existed but was unusable.

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
* Add :ref:`task-delay`.
* Add :ref:`task-external-debsign` and a corresponding API view to allow a
  client to provide a signature to it.
* Add a system for coordinating multiple sub-workflows within a higher-level
  workflow.
* Introduce :ref:`scopes <explanation-scopes>`.
* Introduce a basic application context.
* Run workflow orchestrators via Celery.
* Add :ref:`autopkgtest workflow <workflow-autopkgtest>`.
* Add ``debusine-admin scope`` command.
* Add :ref:`action-retry-with-delays` action for use in ``on_failure`` event
  reactions.
* :ref:`sbuild workflow <workflow-sbuild>`:

  * Support build profiles.
  * Add ``retry_delays``, which can be used for simplistic retries of
    dependency-wait failures.

* Let ``nginx`` gzip-compress text responses.
* Add :ref:`task-package-upload`.
* Add :ref:`package_upload workflow <workflow-package-upload>`.

Web UI
~~~~~~

* Improve label for :ref:`debian:binary-package artifacts
  <artifact-binary-package>`.
* Show "Waiting for signature" card on blocked :ref:`task-external-debsign`
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

* Add :ref:`task-make-source-package-upload`.
* Add :ref:`task-merge-uploads`.
* :ref:`task-sbuild`:

  * Support ``build_profiles``.
  * Don't permit architecture-independent binary-only NMUs.
  * Fix ``architecture`` field of created :ref:`debian:binary-packages
    artifacts <artifact-binary-packages>`.
  * Export ``DEB_BUILD_OPTIONS`` for ``nocheck`` and ``nodoc`` profiles.
  * Set a default maintainer for binary-only NMUs.

* Apply some environment constraints to the :ref:`task-piuparts`'s
  ``base_tgz`` lookup.
* Register :ref:`task-extract-for-signing`, which previously existed but was
  unusable.
* Fix ``unshare`` executor compatibility with Debian environments from
  before the start of the ``/usr`` merge.
* Fall back to the worker's host architecture for the purpose of environment
  lookups if the task doesn't specify one.
* Log progress through the main steps of each task.

Signing
~~~~~~~

* Add :ref:`task-debsign`.

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
* Fix ineffective ``debian:environments`` uniqueness constraint.
* Adjust the :ref:`sbuild workflow <workflow-sbuild>` to allow storing build
  logs in a new :ref:`debian:package-build-logs collection
  <collection-package-build-logs>`.
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
* Add :ref:`task-extract-for-signing`.
* Add :ref:`task-assemble-signed-source`.
* :ref:`task-sbuild`:

  * Create a :ref:`debusine-signing-input artifact
    <artifact-signing-input>`.
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
* Implement ``debian:environments`` collection.
* Implement ``debian:suite-lintian`` collection.
* Add ``debusine-admin create_collection`` command.
* Store tokens only in a hashed form.
* Implement ``debian:suite`` collection.
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
  ``debian:suite-lintian`` collection from ``debian:suite``.
* Implement ``debusine:workflow-internal`` collection.
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

* Validate the summary in ``debian:lintian`` artifacts.
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
* Add remote, read-only file storage backend for external Debian archives.

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
* Document ``debian:package-build-log`` artifact in ontology.
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
