.. _debusine-package-repositories:

====================
Package repositories
====================

You can install Debusine from two different package repositories.

The Debian repository
---------------------

Debusine is available in the Debian archive, in sid (unstable) and trixie
(testing).

If you want to run Debusine on Debian 12, you will need to enable the
bookworm-backports repository and install some packages from that
repository:

.. code-block:: console

  # Only needed on Bookworm
  $ sudo tee /etc/apt/sources.list.d/bookworm-backports.list <<END
  deb http://deb.debian.org/debian bookworm-backports main
  END
  $ sudo apt update
  $ sudo apt install libjs-bootstrap5/bookworm-backports python3-django/bookworm-backports

The snapshot repository
-----------------------

This repository contains packages built out of the ``devel`` branch of
Debusine's git repository. It can be used to test the next release that is
still in development.

.. include:: /common/add-snapshot-repository.rst
