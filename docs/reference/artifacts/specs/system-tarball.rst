.. _artifact-system-tarball:

Category ``debian:system-tarball``
==================================

This artifact contains a tarball of a Debian system. The tarball is
compressed with ``xz``.

* Data:

  * filename: filename of the tarball inside the artifact (e.g.
    "system.tar.xz").
  * vendor: name of the distribution vendor (can be found in ID field in
    /etc/os-release)
  * codename: name of the distribution used to bootstrap the system
  * mirror: URL of the mirror used to bootstrap the system
  * variant: value of ``--variant`` parameter of debootstrap
  * pkglist: a dictionary listing versions of installed packages (cf
    ``dpkg-query -W``)
  * architecture: the architecture of the Debian system
  * with_dev: boolean value indicating whether ``/dev`` has been populated
    with the most important special files in ``/dev`` (null, zero, full,
    random, urandom, tty, console, ptmx) as well as some usual symlinks
    (fd, stdin, stdout, stderr).
  * with_init: boolean value indicating whether the system contains an
    init system in ``/sbin/init`` and can thus be "booted" in a container.

* Files:

  * ``$filename`` (e.g. ``system.tar.xz``): tarball of the Debian system

* Relationships:

  * None.

.. note::
   In preparation of support of different compression schemes, we have
   decided that the extension of the filename dictates the compression
   scheme used and that it should be compatible with ``tar
   --auto-compress``.
