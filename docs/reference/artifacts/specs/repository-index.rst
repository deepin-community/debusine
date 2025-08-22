.. artifact:: debian:repository-index

Category ``debian:repository-index``
====================================

This artifact stores an index file in a repository: as well as the `standard
repository index files <https://wiki.debian.org/DebianRepository/Format>`__,
this covers any file that is not part of a source or binary package.

* Data:

  * ``path``: the path of the file relative to the root of the
    :collection:`debian:suite` or :collection:`debian:archive` collection
    where it will be used

* Files:

  * exactly one file, whose name is the base name of the path of the index
    file in the repository (e.g. ``Packages.xz``)

* Relationships:

  * relates-to: for ``Release`` files, the other index files mentioned in
    them
  * extends: for ``Release.gpg`` and ``InRelease`` files, the corresponding
    unsigned ``Release`` file

Note that while in principle a ``Packages`` or ``Sources`` file relates to
all the individual packages mentioned in it, actually creating those
artifact relationships would result in a very large amount of churn in the
``ArtifactRelation`` table for relatively minimal benefit, so we
intentionally skip those.
