.. task:: SimpleSystemImageBuild

SimpleSystemImageBuild task
---------------------------

The ``simplesystemimagebuild`` task implements the :task:`SystemImageBuild`
interface except that it expects a single entry in the list of partitions:
the entry for the root filesystem (thus with a mountpoint of ``/``).

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.mmdebstrap::MmDebstrap.build_dynamic_data

In terms of compliance with the :task:`SystemBootstrap` interface, the
bootstrap phase only uses a single repository but the remaining
repositories are enabled after the bootstrap.

This task is implemented with the help of the ``debefivm-create`` tool.
