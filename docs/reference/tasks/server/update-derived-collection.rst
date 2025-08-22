.. task:: UpdateDerivedCollection

UpdateDerivedCollection task
----------------------------

This is a generic :ref:`server-side task <explanation-tasks>` that compares
two collections, one of which is :ref:`derived <collection-derived>` from
the other, and creates any work requests necessary to update the derived
collection.

The ``task_data`` for this task may contain the following keys:

* ``base_collection`` (:ref:`lookup-single`, required): the "base"
  collection which we are using as a source of data
* ``derived_collection`` (:ref:`lookup-single`, required): the "derived"
  collection that we are updating
* ``child_task_data`` (optional): a dictionary to use as the ``task_data``
  of child work requests, with additional items merged into it as indicated
  by the specific implementation; for example, it may be useful to specify
  an ``environment`` here
* ``force`` (boolean, defaults to False): if True, schedule work requests
  for each matching artifact in the base collection regardless of whether
  there is already a corresponding artifact in the derived collection (for
  example, this might be useful when the implementation of the task has
  changed)

Specific tasks based on this interface are responsible for determining the
relevant subsets of active items in each of the base and derived collections
that are compared, for defining the desired derived item names given a set
of base items, and for defining the work requests needed to perform each
individual update to the derived collection.

This task takes the relevant subset of the derived collection and finds the
items in the base collection from which each of them were derived, using
``derived_from`` in each of the per-item data fields.  (Multiple items may
be derived from the same base items.)  It then compares these items to the
relevant subset of the base collection and determines the derived items that
need to be changed given the current contents of the base collection, in one
of these ways:

* add: a derived item is desired but does not exist
* replace: a derived item is desired with the same ``name`` as one that
  already exists, but either its base items have changed or ``force`` is
  True
* remove: a derived item exists but is not desired

The definition of a collection item's ``name`` guarantees that only one
active item with a given name may exist in any given collection, so it is a
convenient key to use here.

For each derived item that should be added or replaced, the task creates
suitable child work requests to create a new derived item and update the
derived collection, according to the specific implementation.  It only
creates a work request if another work request with the same parameters does
not already exist, or if ``force`` is True.

For each derived item that should be removed, the task immediately removes
it from the derived collection.

If this task is part of a workflow, then each of the created work requests
is created as a sub-step of it in the same workflow.
