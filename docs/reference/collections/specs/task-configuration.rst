.. _collection-task-configuration:

Category ``debusine:task-configuration``
----------------------------------------

This collection is used to store configuration data for workflows and tasks
(see :ref:`task-configuration`).

The collection is looked up via the ``task_configuration`` field in
``task_data``, defaulting to ``default@debusine:task-configuration``, and thus
allowing to run workflows with different sets of configuration overrides.


* Data: none

* Valid items:

  * ``debusine:task-configuration`` bare data (see :ref:`bare-data-task-configuration`)

* Lookup names:

  * None. The name based lookup is sufficient.

* Constraints:

  * The name of the collection item is ``TASK_TYPE:TASK_NAME:SUBJECT:CONTEXT``,
    except for a template item where it is ``template:TEMPLATE``. URL-encoding
    is applied to ``CONTEXT`` because it can contain colons.

