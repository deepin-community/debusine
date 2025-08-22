.. file-backend:: ExternalDebianSuite

ExternalDebianSuite file backend
================================

This is a special-purpose backend that represents files in a Debian suite on
an external mirror.  It is always read-only: no files may be added to it.

* Configuration:

  * ``archive_root_url`` (string): The root URL of the remote archive.
  * ``suite`` (string): The name of the Debian suite.
  * ``components`` (list of strings): The components (e.g. ``main``) of the
    Debian suite.

* Supports returning local paths: no
* Supports returning URLs: yes
