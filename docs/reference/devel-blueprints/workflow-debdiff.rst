.. _workflow-debdiff:

Workflow ``debdiff``
====================

This workflow schedules :ref:`DebDiff tasks <task-debdiff>` to compare packages
from the ``original`` collection against those provided in ``source_artifact``
and ``binary_artifacts``. A single DebDiff task is scheduled for the
``source_artifact``, and one DebDiff task is scheduled per architecture
present in the ``binary_artifacts``, provided a corresponding package exists
in the ``original`` collection with a compatible architecture.

.. note::
    Architectures are considered compatible when they are equal, or when one is
    ``all`` and the other is ``any`` (to account for changes in a binary
    package's declared architecture across versions).

* ``task_data``:

  * ``source_artifact`` (:ref:`lookup-single`, required): the artifact of
    category ``debian:source-package`` to use as the `new` version in the
    comparison; it will be compared to the `original` source package found
    in ``original`` collection. ``source_artifact`` is also used to identify
    binary packages built by the original source, allowing the workflow to
    detect removed binaries.

  * ``binary_artifacts`` (:ref:`lookup-multiple`, optional): the artifacts
    of category ``debian:upload`` to use as the `new` versions in the
    comparisons; each will be compared to the corresponding `original`
    package (with a compatible architecture) found in ``original`` collection.

  * ``original`` (:ref:`lookup-single`, required): collection of category
    ``debian:suite`` where to lookup the original packages

  * ``extra_flags`` (optional): a list of command-line flags to be passed
    to each scheduled DebDiff task (see :ref:`task-debdiff` for details)

The workflow computes dynamic metadata as:

  * ``subject``: package name of ``source_artifact``.

The workflow status will be:
  * ``Success`` or ``Failure``: ``Failure`` if any of the DebDiff scheduled
    tasks returned ``Failure``, otherwise ``Success``. Note that the workflow
    will not schedule any DebDiff task if ``original`` does not contain
    a matching package for either ``source_artifact`` or any of the
    ``binary_artifact``.