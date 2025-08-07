.. _available-workflows:

===================
Available workflows
===================

Most workflows are mainly useful as sub-workflows of a more complex
workflow. The workflows that you are most likely to use directly
is the :ref:`debian_pipeline workflow <workflow-debian-pipeline>`.

You will launch a :ref:`workflow template <workflow-template>`, which
provides some default settings, rather than directly launching the bare
workflow.
These are configured by Debusine instance admins, and are listed on the
Workspace's details page. (e.g. for `debusine.debian.net
<https://debusine.debian.net/debian/developers/>`_).

.. toctree::
   :glob:

   specs/*

========================
Runtime status workflows
========================

The runtime status can have the following values (order of checks matters
here, first match defines the status):

- "Needs input": there's at least one child work request with task type
  WAIT and status == RUNNING where ``workflow_data['needs_input']`` is true.

- "Running": there's at least one child work request with task type !=
  WAIT and status == RUNNING.

- "Waiting": there's at least one child work request with task type
  WAIT and status == RUNNING where ``workflow_data['needs_input']`` is false.

- "Pending": there's at least one child work request where status ==
  PENDING.

- "Blocked": there's at least one child work request where status == BLOCKED.

- "Aborted": there's at least one child work request where status == ABORTED.

- "Completed": all the child work requests are in status COMPLETED.

The runtime status is accessible via ``workflow.workflow_runtime_status``.
