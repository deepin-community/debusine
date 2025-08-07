.. _collection-suite:

Category ``debian:suite``
-------------------------

This collection represents a single `suite
<https://wiki.debian.org/DebianRepository/Format#Suite>`_ in a Debian
archive. Its ``name`` is the name of the suite.

* Variables when adding items:

  * ``component``: the component (e.g. ``main`` or ``non-free``) in which
    this package is published
  * ``section``: the section (e.g. ``python``) for this package
  * ``priority``: for binary packages, the priority (e.g. ``optional``) for
    this package

* Data:

  * ``release_fields``: dictionary of static fields to set in this suite's
    ``Release`` file
  * ``may_reuse_versions``: if true, versions of packages in this suite may
    be reused provided that the previous packages with that version have
    been removed; this should be false for typical user-facing suites to
    avoid confusing behaviour from apt, but it may be useful to set it to
    true for experimental suites

* Valid items:

  * ``debian:source-package`` artifacts (see :ref:`artifact-source-package`)
  * ``debian:binary-package`` artifacts (see :ref:`artifact-binary-package`)

* Per-item data:

  * ``srcpkg_name``: for binary packages, the name of the corresponding
    source package (copied from underlying artifact for ease of lookup and
    to preserve history)
  * ``srcpkg_version``: for binary packages, the version of the
    corresponding source package (copied from underlying artifact for ease
    of lookup and to preserve history)
  * ``package``: the name from the package's ``Package:`` field (copied from
    underlying artifact for ease of lookup and to preserve history)
  * ``version``: the version of the package (copied from underlying artifact
    for ease of lookup and to preserve history)
  * ``architecture``: for binary packages, the architecture of the package
    (copied from underlying artifact for ease of lookup and to preserve
    history)
  * ``component``: the component (e.g. ``main`` or ``non-free``) in which
    this package is published
  * ``section``: the section (e.g. ``python``) for this package
  * ``priority``: for binary packages, the priority (e.g. ``optional``) for
    this package

* Lookup names:

  * ``source:NAME``: the current version of the source package named
    ``NAME``.
  * ``source-version:NAME_VERSION``: the source package named ``NAME`` at
    ``VERSION``.
  * ``binary:NAME_ARCHITECTURE`` the current version of the binary package
    named ``NAME`` on ``ARCHITECTURE``.
  * ``binary-version:NAME_VERSION_ARCHITECTURE`` the binary package named
    ``NAME`` at ``VERSION`` on ``ARCHITECTURE``.

* Constraints:

  * there may be at most one package with a given name and version (and
    architecture, in the case of binary packages) active in the collection
    at a given time
  * each poolified file name resulting from an active artifact may only
    refer to at most one concrete file in the collection at a given time
    (this differs from the above constraint in the case of source packages,
    which contain multiple files that may overlap with other source
    packages)
  * if ``may_reuse_versions`` is false, then each poolified file name in the
    collection may only refer to at most one concrete file, regardless of
    whether conflicting files are active or removed
