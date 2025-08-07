.. _collection-archive:

Category ``debian:archive``
---------------------------

This collection represents a `Debian archive (a.k.a. repository)
<https://wiki.debian.org/DebianRepository/Format>`_.

* Variables when adding items: none

* Data:

  * ``may_reuse_versions``: if true, versions of packages in this archive
    may be reused provided that the previous packages with that version have
    been removed; this should be false for typical user-facing archives to
    avoid confusing behaviour from apt, but it may be useful to set it to
    true for experimental archives

* Valid items:

  * ``debian:suite`` collections (see :ref:`collection-suite`)

* Per-item data: none

* Lookup names:

  * ``name:NAME``: the suite whose ``name`` property is ``NAME``
  * ``source-version:NAME_VERSION``: the source package named ``NAME`` at
    ``VERSION``.
  * ``binary-version:NAME_VERSION_ARCHITECTURE``: the set of binary packages
    on ``ARCHITECTURE`` whose ``srcpkg_name`` property is ``NAME`` and whose
    ``version`` property is ``VERSION``.

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
