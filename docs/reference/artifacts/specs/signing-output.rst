.. artifact:: debusine:signing-output

Category ``debusine:signing-output``
====================================

This artifact contains the output of a :task:`Sign` task.

* Data:

  * ``purpose``: the purpose of the key used to sign these files: ``uefi``
    or ``openpgp``.
  * ``fingerprint``: the fingerprint of the key used to sign these files
  * ``results``: a list of dictionaries describing signed files, as follows
    (exactly one of ``output_file`` and ``error_message`` must be present):

    * ``file``: name of the file that was signed
    * ``output_file``: name of the file containing the signature
    * ``error_message``: error message resulting from attempting to sign
      the file

  * ``binary_package_name``: the name of the binary package that this
    artifact was extracted from, if any (copied from the corresponding
    :artifact:`debusine:signing-input` artifact)

* Files:

  * zero or more files containing signatures

* Relationships:

  * ``relates-to``: the corresponding :artifact:`debusine:signing-input`
    artifact
