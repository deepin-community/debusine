.. _workflow-piuparts:

Workflow ``piuparts``
=====================

This workflow schedules ``piuparts`` checks for binaries built by a single
source package on a set of architectures.

* ``task_data``:

  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): see
    :ref:`task-piuparts`

  * ``vendor`` (string, required): the distribution vendor on which to run
    tests
  * ``codename`` (string, required): the distribution codename on which to
    run tests
  * ``backend`` (string, optional): see :ref:`task-piuparts`
  * ``environment`` (string, optional): the environment to run piuparts in
  * ``extra_repositories`` (optional): see :ref:`task-piuparts`
  * ``architectures`` (list of strings, optional): if set, only run on any
    of these architecture names
  * ``arch_all_host_architecture`` (string, defaults to ``amd64``): concrete
    architecture on which to run tasks for ``Architecture: all`` packages

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.piuparts::PiupartsWorkflow.build_dynamic_data


``piuparts`` will be run on the intersection of the provided list of
architectures (if any) and the architectures provided in
``binary_artifacts``, in each case grouping arch-all + arch-any together.
If only ``Architecture: all`` binary packages are provided in
``binary_artifacts``, then ``piuparts`` will be run once for arch-all on
``{arch_all_host_architecture}``.

The workflow creates a :ref:`task-piuparts` for each concrete architecture,
with task data:

* ``input.binary_artifacts``: the subset of ``{binary_artifacts}`` that are
  for the concrete architecture or ``all``
* ``host_architecture``: the concrete architecture, or
  ``{arch_all_host_architecture}`` if only ``Architecture: all`` binary
  packages are being checked by this task
* ``environment``: ``{environment}`` if specified, falling back to
  ``{vendor}/match:codename={codename}``
* ``base_tgz``: ``{vendor}/match:codename={codename}``
* ``backend``: ``{backend}``
* ``extra_repositories`` copied from the workflow task, and extended
  with overlay repositories (e.g. ``experimental``) if ``codename`` is a
  known overlay.

Any of the lookups in ``input.binary_artifacts`` may result in
:ref:`promises <bare-data-promise>`, and in that case the workflow adds
corresponding dependencies.  Binary promises must include an
``architecture`` field in their data.

.. todo::

    It would be useful to have a mechanism to control multiarch tests, such
    as testing i386 packages on an amd64 testbed.

.. todo::

    It would be useful to be able to set ``base_tgz`` separately from
    ``environment``.
