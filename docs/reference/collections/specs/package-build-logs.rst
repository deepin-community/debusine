.. _collection-package-build-logs:

Category ``debian:package-build-logs``
--------------------------------------

This :ref:`singleton collection <collection-singleton>` is used to ensure
that build logs are retained even when other corresponding artifacts from
the same work request have been expired.  Build logs are typically small and
compress well compared to other artifacts, and if the artifact ended up
being distributed to users (for example, a binary package in a distribution)
then its build logs are often useful when figuring out what happened in the
past.  Furthermore, if a task that previously succeeded now fails, then
comparing build logs often quickly helps to narrow down the problem.

When a work request that is expected to produce a build log is created, it
should use an :ref:`action-update-collection-with-data` event reaction to
add a bare item to this collection, in order that scheduled but incomplete
builds can be made visible in views that allow browsing this collection.  It
should use a corresponding :ref:`action-update-collection-with-artifacts`
event reaction to replace that item with an artifact item when the build log
is created.  Workflows such as the :ref:`sbuild workflow <workflow-sbuild>`
are expected to handle the details of this.

Views of this collection that need to filter by things like the result of
the work request should join with the ``WorkRequest`` table, using the
``work_request_id`` entry in the per-item data.  (This avoids the extra
complexity of keeping this collection up to date with the lifecycle of work
requests.)

The collection manager sets item names to
``{vendor}_{codename}_{architecture}_{srcpkg_name}_{srcpkg_version}_{work_request_id}``,
computed from the supplied variables.

* Variables when adding items: see "Per-item data" below

* Data: none

* Valid items:

  * ``debian:package-build-log`` bare items, indicating builds that have not
    yet completed (see :ref:`bare-data-package-build-log`)
  * ``debian:package-build-log`` artifacts; when added, these replace bare
    items with the same category and item name (see :ref:`artifact-package-build-log`)

* Per-item data:

  * ``work_request_id``: ID of the work request for this build
  * ``worker`` (optional, inferred from work request when adding item): name
    of the worker that the work request is assigned to
  * ``vendor``: name of the distribution vendor that this package was built
    for
  * ``codename``: codename of the distribution version that this package was
    built for
  * ``architecture``: name of the architecture that this package was built
    for
  * ``srcpkg_name``: name of the source package
  * ``srcpkg_version``: version of the source package

* Lookup names: none (since this collection is for retention and browsing,
  we expect that it will normally be queried using the
  :ref:`lookup-multiple` syntax instead, or by a UI in front of that)

* Multiple lookup filters:

  * ``same_work_request``: given a :ref:`lookup-multiple`, return conditions
    matching build logs that were created by the same work request as any of
    the resulting artifacts

* Constraints: none
