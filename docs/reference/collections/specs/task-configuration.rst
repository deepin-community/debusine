.. collection:: debusine:task-configuration

Category ``debusine:task-configuration``
----------------------------------------

This collection is used to store configuration data for workflows and tasks
(see :ref:`task-configuration`).

The collection is looked up via the ``task_configuration`` field in
``task_data``, defaulting to ``default@debusine:task-configuration``, and thus
allowing to run workflows with different sets of configuration overrides.


* Data:

  * ``git_commit`` (optional): git commit hash of the current collection. This
    is stored when pushing from git using ``debusine-client task-config-push``,
    and is removed when the collection is updated by other means.

* Valid items:

  * :bare-data:`debusine:task-configuration` bare data

* Lookup names:

  * None. The name based lookup is sufficient.

* Constraints:

  * The name of the collection item is ``TASK_TYPE:TASK_NAME:SUBJECT:CONTEXT``,
    except for a template item where it is ``template:TEMPLATE``. URL-encoding
    is applied to ``CONTEXT`` because it can contain colons.

