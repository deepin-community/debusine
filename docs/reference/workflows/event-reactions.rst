.. _workflow-event-reactions:

Event reactions
===============

The ``event_reactions`` field on a workflow is a dictionary mapping events
to a list of actions. Each action is described with a dictionary where the
``action`` key defines the action to perform and where the remaining keys
are used to define the specifics of the action to be performed. See section
below for details. The supported events are the following:

* ``on_creation``: event triggered when the work request is created
* ``on_unblock``: event triggered when the work request is unblocked
* ``on_assignment``: event triggered when the work request is assigned to a
  worker
* ``on_success``: event triggered when the work request completes
  successfully
* ``on_failure``: event triggered when the work request fails or errors
  out

Supported actions
~~~~~~~~~~~~~~~~~

.. _action-skip-if-lookup-result-changed:

``skip-if-lookup-result-changed``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Skip this work request if the result of a lookup has changed since the work
request was created.  This is intended for use in ``on_assignment`` events,
and can be used when a workflow anticipates that multiple instances of
itself may race to update the same piece of information in a collection.

If a work request is skipped, the ``skip_reason`` field in its output data
is set with a suitable explanation.

* ``lookup`` (:ref:`lookup-single`, required): lookup to perform; this must
  be within a collection, not just a bare artifact or collection ID
* ``collection_item_id`` (integer, optional): the collection item ID
  resulting from the lookup when the work request was created, or None if
  the lookup did not return a result
* ``promise_name`` (string, optional): if set, the promise in the current
  workflow's internal collection to update with the result of the lookup

.. _action-send-notification:

``send-notification``
^^^^^^^^^^^^^^^^^^^^^

Sends a notification of the event using an existing notification channel.

* ``channel``: name of the notification channel to use
* ``data``: parameters for the notification method

.. _action-update-collection-with-artifacts:

``update-collection-with-artifacts``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Adds or replaces artifact-based collection items with artifacts generated
by the current work request.

* ``collection`` (:ref:`lookup-single`, required): collection to update
* ``name_template`` (string, optional): template used to generate the name for the collection
  item associated to a given artifact. Uses the ``str.format`` templating
  syntax (with variables inside curly braces).
* ``variables`` (dict, optional): definition of variables to prepare to be able to
  compute the name for the collection item.  Keys and values in this
  dictionary are interpreted as follows:

  * Keys beginning with ``$`` are handled using `JSON paths
    <https://pypi.org/project/jsonpath-rw/>`_.  The part of the key after
    the ``$`` is the name of the variable, and the value is a JSON path
    query to execute against the ``data`` dictionary of the target artifact
    in order to compute the value of the variable.

  * Keys that do not begin with ``$`` simply set the variable named by the
    key to the value, which is a constant string.

  * It is an error to specify keys for the same variable name both with and
    without an initial ``$``.

* ``artifact_filters`` (dict, required): this parameter makes it possible
  to identify a subset of generated artifacts to add to the collection.
  Each key-value represents a specific Django's ORM filter query against
  the Artifact model so that one can run
  ``work_request.artifact_set.filter(**artifact_filters)`` to
  identify the desired set of artifacts.
* ``created_at`` (datetime, optional): if set, mark the new collection item
  as having been created at this timestamp rather than now.

.. note::

   When the ``name_template`` key is not provided, it is expected that
   the collection will compute the name for the new artifact-based
   collection item.  Some collection categories might not even allow you to
   override the name.

.. note::

   After any JSON path expansion, the ``variables`` field is passed to the
   collection manager's ``add_artifact``, so it may use those expanded
   variables to compute its own item names or per-item data.

As an example, you could register all the binary packages having
``Section: python`` and a dependency on libpython3.12 out of a ``sbuild``
task with names like ``$PACKAGE_$VERSION`` by using this action::

    action: 'update-collection-with-artifacts'
    artifact_filters:
      category: 'debian:binary-package'
      data__deb_fields__Section: 'python'
      data__deb_fields__Depends__contains: 'libpython3.12'
    collection: 'internal@collections'
    name_template: '{package}_{version}'
    variables:
      '$package': 'deb_fields.Package'
      '$version': 'deb_fields.Version'

.. _action-update-collection-with-data:

``update-collection-with-data``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Adds or replaces a bare collection item based on the current work request.

This is similar to :ref:`action-update-collection-with-artifacts`, except
that of course it does not refer to artifacts.  This can be used in
situations where no artifact is available, such as in ``on_creation``
events.

* ``collection`` (:ref:`lookup-single`, required): collection to update
* ``category`` (string, required): the category of the item to add
* ``name_template`` (string, optional): template used to generate the name
  for the collection item.  Uses the ``str.format`` templating syntax (with
  variables inside curly braces, referring to keys in ``data``).
* ``data`` (dict, optional): data for the collection item.  This may also be
  used to compute the name for the item, either via substitution into
  ``name_template`` or by rules defined by the collection manager.
* ``created_at`` (datetime, optional): if set, mark the new collection item
  as having been created at this timestamp rather than now.

.. note::

   When the ``name_template`` key is not provided, it is expected that the
   collection will compute the name for the new bare collection item.  Some
   collection categories might not even allow you to override the name.

.. _action-retry-with-delays:

``retry-with-delays``
^^^^^^^^^^^^^^^^^^^^^

This action is used in ``on_failure`` event reactions.  It causes the work
request to be retried automatically with various parameters, adding a
dependency on a newly-created :task:`Delay` task.

The current delay scheme is limited and simplistic, but we expect that more
complex schemes can be added as variations on the parameters to this action.

* ``delays`` (list, required): a list of delays to apply to each successive
  retry; each item is an integer suffixed with ``m`` for minutes, ``h`` for
  hours, ``d`` for days, or ``w`` for weeks.

The workflow data model for work requests gains a ``retry_count`` field,
defaulting to 0 and incrementing on each successive automatic retry.  When
this action runs, it creates a :task:`Delay` task with its ``delay_until``
field set to the current time plus the item from ``delays`` corresponding to
the current retry count, adds a dependency from its work request to that,
and marks its work request as blocked on that dependency.  If the retry
count is greater than the number of items in ``delays``, then the action
does nothing.

.. _action-record-in-task-history:

``record-in-task-history``
^^^^^^^^^^^^^^^^^^^^^^^^^^

This action is meant to be used as an event reaction to store the current
task run in a :collection:`debusine:task-history` collection. The following
fields are supported:

* ``subject`` (optional, defaults to value stored in dynamic_data): the
  subject string used to record the statistics
* ``context`` (optional, defaults to value stored in dynamic_data): the
  *runtime context* string used to record the statistics

When the action is executed, it looks up the
:collection:`debusine:task-history` singleton collection corresponding to
the work request's workspace, and adds a new entry to it.  If there is no
such collection, it does nothing.

.. note::

   This action is not meant to be manually added on each work request.
   Instead it should be automatically executed upon completion of each work
   request.
