.. _task-piuparts:

Piuparts task
-------------

A specific task to represent a binary package check using the
``piuparts`` utility.

To use ``piuparts`` under one of the container-based executors requires
``piuparts >= 1.3``.
This is available in Debian since ``trixie``, so we'd recommend using
``trixie`` or later as the environment for ``piuparts`` tasks.
Select the appropriate ``base_tgz`` for the distribution release
actually under test.

The ``task_data`` associated to this task can contain the following keys:

* ``input`` (required): a dictionary describing the input data

  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): a list of
    ``debian:binary-packages`` or ``debian:upload`` artifacts representing
    the binary packages to be tested. Multiple artifacts can be provided so
    as to support e.g. testing binary packages from split indep/arch builds.

* ``backend`` (optional, defaults to ``unshare``).
  If ``auto``, the task uses the default.
  Supported backends: ``incus-lxc``, ``incus-vm``, and ``unshare``.
* ``environment`` (:ref:`lookup-single` with default category
  ``debian:environments``, required): artifact of category
  ``debian:system-tarball`` that will be used to run piuparts itself.
* ``base_tgz`` (:ref:`lookup-single` with default category
  ``debian:environments``, required): artifact of category
  ``debian:system-tarball`` that will be used to run piuparts tests, through
  ``piuparts --base-tgz``. If the artifact's data has ``with_dev: True``,
  the task will remove the files ``/dev/*`` before using it.

* ``host_architecture`` (required): the architecture that we want to
  test on.

* ``extra_repositories`` (optional): a list of extra repositories to enable.
  Each repository is described by the same dictionary as the
  ``extra_repositories`` option in the :ref:`package-build-task`.

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.piuparts::Piuparts.build_dynamic_data

The ``piuparts`` output will be provided as a new artifact.
