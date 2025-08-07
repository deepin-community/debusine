.. _workflow-template:

WorkflowTemplate
================

The ``WorkflowTemplate`` model has (at least) the following fields:

* ``name``: a unique name given to the workflow within the workspace
* ``workspace``: a foreign key to the workspace containing the workflow
* ``task_name``: a name that refers back to the ``Workflow`` class to
  use to manage the execution of the workflow
* ``task_data``: JSON dict field representing a subset of the parameters
  needed by the workflow that cannot be overridden when instantiating the root
  ``WorkRequest``

The root ``WorkRequest`` of the workflow copies the following fields from
``WorkflowTemplate``:

* ``workspace``
* ``task_name``
* ``task_data``, combining the user-supplied data and the
  ``WorkflowTemplate``-imposed data)
