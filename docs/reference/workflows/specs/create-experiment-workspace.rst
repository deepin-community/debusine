.. _workflow-create-experiment-workspace:

Workflow ``create_experiment_workspace``
========================================

This workflow creates a :ref:`workspace for experiments
<experiment-workspaces>`.  It provides a simple interface for users to
invoke the :ref:`corresponding task <task-create-experiment-workspace>`.

* ``task_data``:

  * ``experiment_name`` (string): the name of the experiment; must start
    with an ASCII letter, and must only contain ASCII letters, digits,
    ``+``, ``.``, or ``_``
  * ``public`` (boolean, defaults to True): whether the new workspace is
    public
  * ``owner_group`` (string, optional): the name of the existing group in
    the base workspace's scope to set as the owner of the new workspace; if
    unset, the task will create a new ephemeral group
  * ``workflow_template_names`` (list of strings, defaults to the empty
    list): names of workflow templates to copy from the base workspace to
    the new workspace
  * ``expiration_delay`` (integer, optional, defaults to 60): number of days
    since the last task completion time after which the new workspace can be
    deleted

The workflow creates a :ref:`task-create-experiment-workspace`, with the
same task data.
