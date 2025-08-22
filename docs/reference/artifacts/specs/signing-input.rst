.. artifact:: debusine:signing-input

Category ``debusine:signing-input``
===================================

This artifact provides input to a :task:`Sign` task.  It will typically be
created by the :task:`ExtractForSigning` task or the :task:`Sbuild` task.

* Data:

  * ``trusted_certs``: a list of SHA-256 fingerprints of certificates built
    into the signed code as roots of trust for verifying additional
    privileged code (see `Describing the trust chain
    <https://wiki.debian.org/SecureBoot/Discussion#Describing_the_trust_chain>`_).
    If present, all the listed fingerprints must be listed in the
    ``DEBUSINE_SIGNING_TRUSTED_CERTS`` Django setting.  This is used to
    avoid accidentally creating trust chains from production to test signing
    certificates.
  * ``binary_package_name``: the name of the binary package that this
    artifact was extracted from, if any

* Files: one or more files to be signed

* Relationships:

  * ``relates-to``: any other artifacts from which the files to be signed
    were extracted
