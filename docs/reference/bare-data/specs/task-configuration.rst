.. _bare-data-task-configuration:

Category ``debusine:task-configuration``
========================================

This bare data item represents overrides that can be applied to task
configuration.

It has two sets of fields: some that define when the overrides should be
applied, and some that define the overrides themselves.

* Data used to identify where the configuration is applied:

  * ``task_type`` (required if ``template`` is None): the ``task_type`` of the
    task for which we want to provide default values and overrides
  * ``task_name`` (required if ``template`` is None): the ``task_name`` of the
    task for which we want to provide default values and overrides
  * ``subject`` (defaults to None):  an abstract string value representing the
    *subject* of the task (i.e. something passed as input). It is meant to
    group possible inputs for the tasks into groups that we expect to
    configure similarly.
  * ``context`` (defaults to None): an abstract string value representing the
    *configuration context* of the task. It is typically another important
    task parameter (or derived from it).
  * ``template`` (defaults to None): the name of a template entry

Constraints:

  * When ``template`` is set, ``task_type``, ``task_name``, ``subject`` and
    ``context`` should be None

* Data representing the actions to perform on the matching task configuration
  data (all fields are optional):

  * ``use_templates`` (list): a list of template names whose corresponding
    entries shall be retrieved and imported as part of the configuration
    returned for the current entry
  * ``delete_values`` (list): a list of configuration keys to delete from the
    values returned by the previous configuration levels
  * ``default_values`` (dict): values to use as default values if the user did not
    provide any value for the given configuration keys
  * ``override_values`` (dict): values to use even if the user did provide a
    value for the given configuration key
  * ``lock_values`` (list): a list of configuration keys that should
    be locked (i.e. next configuration level can no longer provide or modify
    the corresponding value)
  * ``comment`` (string): multiline free form text used to document the
    reasons behind the provided configuration. Text can use Markdown syntax.

This mechanism only allows to control top-level configuration keys in
``task_data`` fields. It is not possible to override a single value
in a nested dictionary, but you can override the whole dictionary if you
wish so.

When the same configuration key appears in ``default_values`` and
``override_values`` (either in a single entry, or in the entry created by
combining the different levels), the one from ``override_values`` take
precedence over the one from ``default_values``.
