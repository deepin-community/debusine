.. _collection-derived:

Derived collections
===================

To support automated QA at the scale of a distribution, some collections are
derived automatically from other collections.  For example, the collection
of Lintian output for a suite would be derived automatically by running a
Lintian task on each of the packages in the corresponding ``debian:suite``
collection.  Such collections have additional information to allow keeping
track of what work needs to be done to keep them up to date:

* Per-item data:

  * ``derived_from``: a list of the internal collection item IDs from which
    this item was derived

Implementations of the :ref:`update-derived-collection-task` use this
information to keep such derived collections up to date.
