.. _explanation-signing-service:

===============
Signing service
===============

Some packages need to be signed for the benefit of UEFI Secure Boot and
other similar firmware arrangements that ensure the authenticity of code
executed by the firmware.  See `SecureBoot/Discussion
<https://wiki.debian.org/SecureBoot/Discussion>`_ for an outline of the
current implementation in Debian.

To support this while keeping keys protected, Debusine has a signing
service, offering internal APIs for generating keys and signing data, and
retaining an audit log of all signatures it performs.

The signing service acts as a signing oracle, allowing Debusine to sign
things using any key it holds.  The Debusine server has responsibility for
controlling the authority to sign using any given key.  The signing service
prevents all other components from extracting the plaintext of private keys
or from tampering with audit logs; for particularly high-value keys, it can
also make use of a hardware security module (HSM), so that even the signing
service itself has no access to the plaintext of private keys.

The signing service includes a :ref:`signing worker <explanation-workers>`
which runs only signing tasks.  In order that the Debusine server can
control authorization to use keys, signing tasks may only be created by
certain :ref:`explanation-workflows`, or by using the ``debusine-admin
create_work_request`` command on the server.

Signing workflows
=================

For UEFI Secure Boot, Debian has a system of `template packages
<https://wiki.debian.org/SecureBoot/Discussion#Template_organization>`__
that specify what is to be signed and provide a structure for uploading the
results back to the archive.  Handling this system requires three basic
steps: extracting input binary packages, signing files, and building the
output source package.  To avoid risk from vulnerabilities such as those
listed above, unpacking binary packages and building source packages must
always be run on an external worker within some kind of container.

There are three tasks for this (:task:`ExtractForSigning`, :task:`Sign`, and
:task:`AssembleSignedSource`), an asset (:asset:`debusine:signing-key`), two
artifact categories to mediate these tasks
(:artifact:`debusine:signing-input` and
:artifact:`debusine:signing-output`), and a :workflow:`make_signed_source`
workflow to tie everything together.

The signing service may be used for purposes other than UEFI Secure Boot.
For example, the :task:`Debsign` task allows signing a package upload using
an OpenPGP key, which is used by the :workflow:`package_upload` workflow.
This allows Debusine to act as a build daemon.

Signing key management
======================

The :ref:`task-configuration` mechanism should be used to select the
appropriate signing keys automatically.

There will be API and UI actions to generate new signing keys for a package
in a suite.

.. _key-protection:

Key protection
==============

Private key material is never stored in the clear.  If it is stored directly
in the database, it is encrypted at rest with a configured key.  The
encrypted form includes both the public key and the ciphertext to allow for
key rotation.

Alternatively, private keys may be stored as a PKCS#11 URI referring to an
attached hardware security module.

Signature methods
=================

Debusine currently supports making UEFI Secure Boot and OpenPGP signatures.
It is straightforward to add other signing methods if needed.

:ref:`reference-signing-service` has some more details of how the signing
service is implemented.
