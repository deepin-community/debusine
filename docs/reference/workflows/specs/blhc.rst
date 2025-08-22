.. workflow:: blhc

Workflow ``blhc``
====================

This workflow schedules :task:`Blhc` tasks to analyze the logs from builds.
A single :task:`Blhc` is scheduled for each artifact.

* ``task_data``:

  * ``package_build_logs`` (:ref:`lookup-multiple`, required): the
    :artifact:`debian:package-build-log` artifacts to analyze

  * ``extra_flags`` (optional): a list of command-line flags to be passed
    to each scheduled :task:`Blhc` task

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.blhc::BlhcWorkflow.build_dynamic_data
