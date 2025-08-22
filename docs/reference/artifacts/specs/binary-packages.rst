.. artifact:: debian:binary-packages

Category ``debian:binary-packages``
===================================

This artifact represents the set of binary packages (``.deb`` files and
similar) produced during the build of a source package for a given
architecture.

If the build of a source-package produces binaries of more than one
architecture, one ``debian:binary-packages`` artifact is created for each
architecture, listing only the binary packages for that architecture.

* Data:

  * srcpkg_name: the name of the source package
  * srcpkg_version: the version of the source package
  * version: the version used for the build (can be different from the
    source version in case of binary-only rebuilds; note that individual
    binary packages may have versions that differ from this if the source
    package uses ``dpkg-gencontrol -v``)
  * architecture: the architecture that the packages have been built for.
    Can be any real Debian architecture or ``all``.
  * packages: the list of binary packages that are part of the build
    for this architecture.

* Files: one or more ``.deb`` files
* Relationships:

  * built-using: the corresponding :artifact:`debian:source-package`
  * built-using: other :artifact:`debian:binary-package` (for example in the
    case of signed packages duplicating the content of an unsigned package)
  * built-using: other :artifact:`debian:source-package` (general case of
    Debian's ``Built-Using`` field)
