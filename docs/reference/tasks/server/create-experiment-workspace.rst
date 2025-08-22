.. task:: CreateExperimentWorkspace

CreateExperimentWorkspace task
------------------------------

This server task creates a :ref:`workspace for experiments
<experiment-workspaces>`.  It requires the
``can_create_experiment_workspace`` permission on the base workspace.

The ``task_data`` for this task may contain the following keys:

* ``experiment_name`` (string): the name of the experiment; must start with
  an ASCII letter, and must only contain ASCII letters, digits, ``+``,
  ``.``, or ``_``
* ``public`` (boolean, defaults to True): whether the new workspace is
  public
* ``owner_group`` (string, optional): the name of the existing group in the
  base workspace's scope to set as the owner of the new workspace; if unset,
  the task will create a new ephemeral group
* ``workflow_template_names`` (list of strings, defaults to the empty list):
  names of workflow templates to copy from the base workspace to the new
  workspace
* ``expiration_delay`` (integer, optional, defaults to 60): number of days
  since the last task completion time after which the new workspace can be
  deleted

The base workspace is the workspace that this task runs in.  The task
creates a new workspace whose name is composed of the name of the base
workspace and ``experiment_name`` separated by an ASCII hyphen, makes it
inherit from the base workspace, and gives it the specified expiration
delay.  It creates a new ephemeral group (or looks up an existing one, if
``owner_group`` is set), and assigns the ``OWNER`` role for the new
workspace to that group.  Finally, it copies the specified workflow
templates to the new workspace.

If the task creates an ephemeral group, it adds the user who created the
task to the group with group administration permissions, in order that they
can manage its membership.
