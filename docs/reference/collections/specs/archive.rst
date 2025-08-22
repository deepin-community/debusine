.. collection:: debian:archive

Category ``debian:archive``
---------------------------

This collection represents a `Debian archive (a.k.a. repository)
<https://wiki.debian.org/DebianRepository/Format>`_.  It is a :ref:`singleton
collection <collection-singleton>` because there is no obvious need to have
more than one of them per workspace, and enforcing only one per workspace
simplifies the URLs that will be used for serving :ref:`repositories
<package-repositories>`: for example, the archive in the ``debian/base``
workspace can be served from ``/debian/base``, rather than something like
``/debian/base/debian``.

To simplify management, all workspaces have an archive collection by
default.  Serving a suite requires adding it to its workspace's archive
collection and arranging for the :workflow:`update_suites` workflow to be
run in its workspace.

:collection:`debian:suite` collections are only valid as items in an archive
if they have the same workspace.  Since archives are singleton collections,
this has the effect of ensuring that a suite can be in at most one archive,
allowing suites to inherit configuration from their parent archive where
needed.

* Variables when adding items: none

* Data:

  * ``may_reuse_versions``: if true, versions of packages in this archive
    may be reused provided that the previous packages with that version have
    been removed; this should be false for typical user-facing archives to
    avoid confusing behaviour from apt, but it may be useful to set it to
    true for experimental archives
  * ``signing_keys`` (list of strings, optional): the fingerprints of the
    :asset:`debusine:signing-key` assets to sign repository indexes in this
    archive with, which must each have purpose ``openpgp``

* Valid items:

  * :collection:`debian:suite` collections
  * :artifact:`debian:repository-index` artifacts

* Per-item data:

  * ``path``: for index files, the path of the file relative to the root of
    the suite's directory in ``dists`` (e.g. ``InRelease`` or
    ``main/source/Sources.xz``)

* Lookup names:

  * ``name:NAME``: the suite whose ``name`` property is ``NAME``
  * ``source-version:NAME_VERSION``: the source package named ``NAME`` at
    ``VERSION``.
  * ``binary-version:NAME_VERSION_ARCHITECTURE``: the set of binary packages
    on ``ARCHITECTURE`` whose ``srcpkg_name`` property is ``NAME`` and whose
    ``version`` property is ``VERSION`` (also including ``Architecture:
    all`` binary packages if ``ARCHITECTURE`` is not ``all``).
  * ``index:PATH``: the current index file at ``PATH`` relative to the root
    of the archive

* Constraints:

  * there may be at most one package with a given name and version (and
    architecture, in the case of binary packages) active in the collection
    at a given time, although the same package may be in multiple suites
  * each poolified file name resulting from an active artifact may only
    refer to at most one concrete file in the collection at a given time
    (this differs from the above constraint in the case of source packages,
    which contain multiple files that may overlap with other source
    packages)
  * if ``may_reuse_versions`` is false, then each poolified file name in the
    collection may only refer to at most one concrete file, regardless of
    whether conflicting files are active or removed

Most index files are stored at the suite level rather than directly at the
archive level: the code that serves an archive as a whole will look at the
appropriate suite when serving paths under ``dists/SUITE/``.  There are a
few exceptions for files that are not packages and not under ``dists/``,
such as override summaries and mirror traces; these are not consulted by
``apt`` and so implementing them is not urgent.
