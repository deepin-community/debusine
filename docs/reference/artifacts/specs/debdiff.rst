.. artifact:: debian:debdiff

Category ``debian:debdiff``
===========================

* Data:

  * ``original``: The name of the first file passed to debdiff.
  * ``new``: The name of the second file passed to debdiff.

* Files:

  * One debdiff output file called ``debdiff.txt`` for the corresponding two source or binary packages.

* Relationships:

  * ``relates-to``: two :artifact:`debian:source-package` or
    :artifact:`debian:upload`
