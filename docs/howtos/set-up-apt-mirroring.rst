.. _set-up-apt-mirroring:

====================
Set up APT mirroring
====================

Introduction
------------

Debusine can mirror a suite from a remote APT repository, allowing automated
tasks to be performed on all the packages in that suite.  This will copy all
the source and binary packages from that suite into Debusine.

Create the target collection
----------------------------

Before mirroring a suite for the first time, you must create the collection
it will be stored in.

.. code-block:: console

  $ sudo -u debusine-server debusine-admin create_collection \
      bookworm debian:suite </dev/null

If you want to run a private mirror, you can use the ``--workspace`` option
to create the collection in your own workspace.  However, for public
mirrors, it is useful to create the collection in the default public
workspace so that it can be used by anyone on the same Debusine instance.

Run the mirror task
-------------------

Create a work request to mirror the suite.  You may also set this up in a
systemd timer or similar.

.. code-block:: console

  $ sudo -u debusine-server debusine-admin create_work_request \
      server aptmirror <<END
  collection: bookworm
  url: https://deb.debian.org/debian
  suite: bookworm
  components:
    - main
    - contrib
    - non-free
    - non-free-firmware
  architectures:
    - amd64
    - arm64
  signing_key: |
    -----BEGIN PGP PUBLIC KEY BLOCK-----
    
    mDMEY865UxYJKwYBBAHaRw8BAQdAd7Z0srwuhlB6JKFkcf4HU4SSS/xcRfwEQWzr
    crf6AEq0SURlYmlhbiBTdGFibGUgUmVsZWFzZSBLZXkgKDEyL2Jvb2t3b3JtKSA8
    ZGViaWFuLXJlbGVhc2VAbGlzdHMuZGViaWFuLm9yZz6IlgQTFggAPhYhBE1k/sEZ
    wgKQZ9bnkfjSWFuHg9SBBQJjzrlTAhsDBQkPCZwABQsJCAcCBhUKCQgLAgQWAgMB
    Ah4BAheAAAoJEPjSWFuHg9SBSgwBAP9qpeO5z1s5m4D4z3TcqDo1wez6DNya27QW
    WoG/4oBsAQCEN8Z00DXagPHbwrvsY2t9BCsT+PgnSn9biobwX7bDDg==
    =5NZE
    -----END PGP PUBLIC KEY BLOCK-----
  END

.. todo::

   We should have a simpler way to manage signing keys: perhaps they should
   be stored in artifacts and referenced using lookups.
