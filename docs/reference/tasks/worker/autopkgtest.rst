.. _task-autopkgtest:

Autopkgtest task
----------------

The ``task_data`` associated to this task can contain the following keys:

* ``input`` (required): a dictionary describing the input data:

  * ``source_artifact`` (:ref:`lookup-single`, required): the
    ``debian:source-package`` or ``debian:upload`` artifact representing the
    source package to be tested with autopkgtest
  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): a list of
    ``debian:binary-packages`` or ``debian:upload`` artifacts representing
    the binary packages to be tested with autopkgtest (they are expected to
    be part of the same source package as the one identified with
    ``source_artifact``)
  * ``context_artifacts`` (:ref:`lookup-multiple`, optional): a list of
    ``debian:binary-packages`` or ``debian:upload`` artifacts representing a
    special context for the tests. This is used to trigger autopkgtests of
    reverse dependencies, where ``context_artifacts`` is set to the
    artifacts of the updated package whose reverse dependencies are tested,
    and source/binary artifacts are one of the reverse dependencies whose
    autopkgtests will be executed.

* ``host_architecture`` (required): the Debian architecture that will be
  used in the chroot or VM where tests are going to be run.  The
  packages submitted in ``input:binary_artifacts`` usually have a matching
  architecture (but need not in the case of cross-architecture package
  testing, eg. testing i386 packages in an amd64 system).

* ``environment`` (:ref:`lookup-single` with default category
  ``debian:environments``, required): ``debian:system-tarball`` or
  ``debian:system-image`` artifact (as appropriate for the selected backend)
  that will be used to run the tests.

* ``backend`` (optional): the virtualization backend to use, defaults to
  ``auto`` where the task is free to use the most suitable backend.
  Supported: ``incus-lxc``, ``incus-vm``, ``qemu``, and ``unshare``.

* ``include_tests`` (optional): a list of the tests that will be executed.
  If not provided (or empty), defaults to all tests being executed. Translates into
  ``--test-name=TEST`` command line options.

* ``exclude_tests`` (optional): a list of tests that will skipped.
  If not provided (or empty), then no tests are skipped. Translates into
  the ``--skip-test=TEST`` command line options.

* ``debug_level`` (optional, defaults to 0): a debug level between 0 and
  3. Translates into ``-d`` up to ``-ddd`` command line options.

* ``extra_repositories`` (optional): a list of extra repositories to enable.
  Each repository is described by the same dictionary as the
  ``extra_repositories`` option in the :ref:`package-build-task`.

* ``use_packages_from_base_repository`` (optional, defaults to False): if
  True, then we pass ``--apt-default-release=$DISTRIBUTION`` with the name
  of the base distribution given in the ``distribution`` key.

* ``extra_environment`` (optional): a dictionary listing environment
  variables to inject in the build and test environment. Translates into
  (multiple) ``--env=VAR=VALUE`` command line options.

* ``needs_internet`` (optional, defaults to "run"): Translates directly
  into the ``--needs-internet`` command line option. Allowed values
  are "run", "try" and "skip".

* ``fail_on`` (optional): indicates whether the work request must be
  marked as failed in different scenario identified by the following
  sub-keys:

  * ``failed_test`` (optional, defaults to true): at least one test has
    failed (and the test was not marked as flaky).
  * ``flaky_test`` (optional, defaults to false): at least one flaky test
    has failed.
  * ``skipped_test`` (optional, defaults to false): at least one test has
    been skipped.

* ``timeout`` (optional): a dictionary where each key/value pair maps to
  the corresponding ``--timeout-KEY=VALUE`` command line option with the
  exception of the ``global`` key that maps to ``--timeout=VALUE``.
  Supported keys are ``global``, ``factor``, ``short``, ``install``, ``test``,
  ``copy`` and ``build``.

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.autopkgtest::Autopkgtest.build_dynamic_data

.. note::

   At this point, we have voluntarily not added any key for the
   ``--pin-packages`` option because that option is not explicit enough:
   differences between the mirror used to schedule jobs and the mirror
   used by the jobs result in tests that are not testing the version that
   we want. At this point, we believe it's better to submit all modified
   packages explicitly via ``input:context_artifacts`` so that we are sure
   of the .deb that we are submitting and testing with. That way we can even
   test reverse dependencies before the modified package is available in any
   repository.

   This assumes that we can submit arbitrary .deb on the command line and
   that they are effectively used as part of the package setup.

autopkgtest is always run with the options ``--apt-upgrade
--output-dir=ARTIFACT-DIR --summary=ARTIFACT-DIR/summary --no-built-binaries``.

An artifact of category ``debian:autopkgtest`` is generated to store all output
files, and is described :ref:`in the artifacts reference <artifact-autopkgtest>`.
