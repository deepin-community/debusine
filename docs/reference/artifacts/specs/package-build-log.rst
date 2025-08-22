.. artifact:: debian:package-build-log
.. bare-data:: debian:package-build-log

Category ``debian:package-build-log``
=====================================

This artifact contains a package's build log and some associated
information about the corresponding package build. It is kept around
for traceability and for diagnostic purposes.

* Data:

  * ``source``: name of the source package built
  * ``version``: version of the source package built
  * ``architecture`` (optional): architecture of the package built
  * ``filename``: name of the log file
  * ``bd_uninstallable`` (optional): reason why the package is not installable

  * maybe other information extracted out of the build log (build time,
    disk space used, etc.)

* Files:

  * a single file ``.build`` file

* Relationships:

  * relates-to: one (or more) :artifact:`debian:binary-package` and/or
    :artifact:`debian:binary-packages` built
  * relates-to: the corresponding :artifact:`debian:source-package` (if
    built from a source package)
