.. _collection-suite-lintian:

Category ``debian:suite-lintian``
---------------------------------

This :ref:`derived collection <collection-derived>` represents a group of
:ref:`debian:lintian artifacts <artifact-lintian>` for packages in a
:ref:`debian:suite collection <collection-suite>`.

Lintian analysis tasks are performed on combinations of source and binary
packages together, since that provides the best test coverage.  The
resulting ``debian:lintian`` artifacts are related to all the source and
binary artifacts that were used by that task, and each of the items in this
collection is recorded as being derived from all the base
``debian:source-package`` or ``debian:binary-package`` artifacts that were
used in building the associated ``debian:lintian`` artifact.  However, each
item in this collection has exactly one architecture (including ``source``
and ``all``) in its metadata; as a result, source packages and
``Architecture: all`` binary packages may be base items for multiple derived
items at once.

Item names are set to ``{package}_{version}_{architecture}``, substituting
values from the per-item data described below.

* Variables when adding items: none

* Data: none

* Valid items:

  * ``debian:lintian`` artifacts (see :ref:`artifact-lintian`)

* Per-item data:

  * ``package``: the name of the source package being analyzed, or the
    source package from which the binary package being analyzed was built
  * ``version``: the version of the source package being analyzed, or the
    source package from which the binary package being analyzed was built
  * ``architecture``: ``source`` for a source analysis, or the appropriate
    architecture name for a binary analysis

* Lookup names:

  * ``latest:PACKAGE_ARCHITECTURE``: the latest analysis for the source
    package named ``PACKAGE`` on ``ARCHITECTURE``.
  * ``version:PACKAGE_VERSION_ARCHITECTURE``: the analysis for the source
    package named ``PACKAGE`` at ``VERSION`` on ``ARCHITECTURE``.

* Constraints:

  * there may be at most one analysis for a given source package name,
    version, and architecture active in the collection at a given time

For example, given ``hello_1.0.dsc``, ``hello-doc_1.0_all.deb``,
``hello_1.0_amd64.deb``, and ``hello_1.0_s390x.deb``, the following items
would exist:

* ``hello_1.0_source``, with ``{"package": "hello", "version": "1.0",
  "architecture": "source"}`` as per-item data, derived from
  ``hello_1.0.dsc`` and some binary packages
* ``hello_1.0_all``, with ``{"package": "hello", "version": "1.0",
  "architecture": "all"}`` as per-item data, derived from ``hello_1.0.dsc`,
  ``hello-doc_1.0_all.deb``, and possibly some other binary packages
* ``hello_1.0_amd64``, with ``{"package": "hello", "version": "1.0",
  "architecture": "amd64"}`` as per-item data, derived from
  ``hello_1.0.dsc``, ``hello-doc_1.0_all.deb``, and ``hello_1.0_amd64.deb``
* ``hello_1.0_s390x``, with ``{"package": "hello", "version": "1.0",
  "architecture": "s390x"}`` as per-item data, derived from
  ``hello_1.0.dsc``, ``hello-doc_1.0_all.deb``, and ``hello_1.0_s390x.deb``
