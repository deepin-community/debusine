.. collection:: debian:suite

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

  * ``components``: the components that exist in this suite (must be set in
    order to be able to generate indexes)
  * ``architectures``: the architectures that exist in this suite (must be
    set in order to be able to generate indexes)
  * ``release_fields``: dictionary of static fields to set in this suite's
    ``Release`` file
  * ``may_reuse_versions``: if true, versions of packages in this suite may
    be reused provided that the previous packages with that version have
    been removed; this should be false for typical user-facing suites to
    avoid confusing behaviour from apt, but it may be useful to set it to
    true for experimental suites
  * ``duplicate_architecture_all``: if true, include ``Architecture: all``
    packages in architecture-specific ``Packages`` indexes, and set
    `No-Support-for-Architecture-all: Packages
    <https://wiki.debian.org/DebianRepository/Format#No-Support-for-Architecture-all>`__
    in the ``Release`` file; this may improve compatibility with older
    client code
  * ``signing_keys`` (list of strings, optional): the fingerprints of the
    :asset:`debusine:signing-key` assets to sign repository indexes in this
    suite with, which must each have purpose ``openpgp``; if not set, then
    the containing :collection:`archive <debian:archive>` controls which
    signing keys are used

* Valid items:

  * :artifact:`debian:source-package` artifacts
  * :artifact:`debian:binary-package` artifacts
  * :artifact:`debian:repository-index` artifacts

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
  * ``path``: for index files, the path of the file relative to the root of
    the suite's directory in ``dists`` (e.g. ``InRelease`` or
    ``main/source/Sources.xz``)

* Lookup names:

  * ``source:NAME``: the current version of the source package named
    ``NAME``.
  * ``source-version:NAME_VERSION``: the source package named ``NAME`` at
    ``VERSION``.
  * ``binary:NAME_ARCHITECTURE`` the current version of the binary package
    named ``NAME`` on ``ARCHITECTURE`` (also including ``Architecture: all``
    binary packages if ``ARCHITECTURE`` is not ``all``).
  * ``binary-version:NAME_VERSION_ARCHITECTURE`` the binary package named
    ``NAME`` at ``VERSION`` on ``ARCHITECTURE`` (also including
    ``Architecture: all`` binary packages if ``ARCHITECTURE`` is not
    ``all``).
  * ``index:PATH``: the current index file at ``PATH`` relative to the root
    of the suite's directory in ``dists``

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

To support race-free mirroring, repository index files are served via
`by-hash
<https://wiki.debian.org/DebianRepository/Format#indices_acquisition_via_hashsums_.28by-hash.29>`__
paths in addition to their base path.  These paths are handled implicitly by
the code that serves repositories, and are not recorded using separate
collection items.
