.. _dput-ng:

================================
Upload to Debusine using dput-ng
================================

Debusine includes support for uploading packages using `dput-ng
<https://dput.readthedocs.io/en/latest/>`__.

Initial setup
-------------

You must have at least ``debusine-client`` version 0.10.0, and you must have
permission to upload to the Debian archive.

Follow :ref:`set-up-debusine-client` to configure the Debusine client for
the appropriate Debusine server.  The ``dput-ng`` integration only supports
``debusine.debian.net`` out of the box, although you can configure it to use
a different server.

Upload a package
----------------

First, construct the source package that you want to upload (out of scope of
this document), targeting either ``unstable`` or ``experimental``.  You can
then upload it as follows:

.. code-block:: console

    $ dput debusine.debian.net foo_1.0_source.changes
    Uploading dput-ng using debusine to debusine.debian.net (host: debusine.debian.net; directory: /)
    running debusine-check-workflow: check debusine workflow for distribution
    running checksum: verify checksums before uploading
    running suite-mismatch: check the target distribution for common errors
    running gpg: check GnuPG signatures before the upload
    Not checking GPG signature due to allow_unsigned_uploads being set.
    Uploading foo_1.0.dsc
    Uploading foo_1.0.tar.xz
    Uploading foo_1.0_source.changes
    Created artifact: https://debusine.debian.net/debian/developers/artifact/2/
    running debusine-create-workflow: create a debusine workflow
    Created workflow: https://debusine.debian.net/debian/developers/work-request/1/

This does not yet set up email notifications, so open the link to the
created workflow in a web browser.  When all QA has passed, or when you have
reviewed any failures and have decided that they can be ignored, go to the
"Wait for signature on upload" work request and run the command shown there
to sign the upload.  Your package should then be uploaded to the Debian
archive.

Further configuration
---------------------

There are various :ref:`options <dput-ng-profiles>` that you can use if the
default behaviour does not suit your needs.
