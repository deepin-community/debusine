.. artifact:: debian:binary-package

Category ``debian:binary-package``
==================================

This artifact represents a single binary package (a ``.deb`` file or
similar) produced during the build of a source package for a given
architecture.

If the build of a source-package produces more than one binary for a given
architecture, or binaries of more than one architecture, one
``debian:binary-package`` artifact is created for each binary and
architecture.

* Data:

  * srcpkg_name: the name of the source package
  * srcpkg_version: the version of the source package
  * deb_fields: a parsed version of the fields available in the ``.deb``'s
    control file
  * deb_control_files: a list of the files in the ``.deb``'s control part

* Files: a ``.deb`` file
* Relationships:

  * built-using: the corresponding :artifact:`debian:source-package`
  * built-using: other ``debian:binary-package`` (for example in the case of
    signed packages duplicating the content of an unsigned package)
  * built-using: other :artifact:`debian:source-package` (general case of
    Debian's ``Built-Using`` field)
