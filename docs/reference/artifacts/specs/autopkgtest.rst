.. artifact:: debian:autopkgtest

Category ``debian:autopkgtest``
===============================

* Data:

  * ``results``: a dictionary with details about the tests that have been
    run. Each key is the name of the test (as shown in the summary file)
    and the value is another dictionary with the following keys:

    * ``status``: one of ``PASS``, ``FAIL``, ``FLAKY`` or ``SKIP``
    * ``details``: more details when available

  * ``cmdline``: the complete command line that has been used for the run
  * ``source_package``: a dictionary with some information about the source
    package hosting the tests that have been run. It has the following
    sub-keys:

    * ``name``:the name of the source package
    * ``version``: the version of the source package
    * ``url``: the URL of the source package

  * ``architecture``: the architecture of the system where tests have been
    run
  * ``distribution``: the distribution of the system where tests have been
    run (formatted as ``VENDOR:CODENAME``)

* Files:

  * Every file found in the autopkgtest output directory, except for files in
    ``binaries/`` that are excluded to save space.

* Relationships:

  * None currently (see :issue:`806`).
