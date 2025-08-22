.. _expiration-of-data:

==================
Expiration of data
==================

The lifetime of most data in Debusine is controlled by multiple
parameters: at the collection-level, at the workspace level and
at the artifact or work-request level.

Those parameters are used by the :ref:`debusine-admin delete_expired
<command-delete-expired>` command which is executed on a daily basis to
remove expired artifacts and work requests.

Artifacts
=========

Artifacts have their own ``expiration_delay`` (a duration value) that
can be set when the artifact is created, or that is inherited from the
workspace through the ``default_expiration_delay`` field. The expiration
date is computed by adding this delay to the ``created_at`` timestamp.

.. note::

   When the expiration delay is set to 0, it effectively instructs
   Debusine to keep the artifact forever (that is until it is manually
   deleted).

In principle an artifact is thus deleted when its expiration date is
over. But there are many cases where it can be retained:

* when it is referenced by another artifact (through a relationship) that
  has not yet expired (and/or that is also retained for some reason)
* when it is referenced as an item of a collection

Work requests and workflows
===========================

.. note::

   Workflows are implemented internally as special kind of work request.
   Hence they share the same expiration logic.

Work requests follow the same logic as artifacts, they have their own
``expiration_delay`` and inherit from the workspace's
``default_expiration_delay`` when not set.

In principle a work request is thus deleted when its expiration date is
over. But it can be retained in the following cases:

* when it generated an artifact that is stored in a collection (excluding
  internal collections of workflows)
* when it's part of a workflow that has not yet expired

.. _explanation-collection-item-retention:

Collection items
================

Collection items and the artifacts they refer to may be retained in
Debusine's database for some time after the item is removed from the
collection, depending on the values of ``full_history_retention_period`` and
``metadata_only_retention_period``.  The sequence of events is as follows:

* item is removed from collection: metadata and artifact are both still
  present
* after ``full_history_retention_period``, the link between the collection
  item and the artifact is removed: metadata is still present, but the
  artifact may be expired if nothing else prevents that from happening
* after ``full_history_retention_period`` +
  ``metadata_only_retention_period``, the collection item itself is deleted
  from the database: metadata is no longer present, so the history of the
  collection no longer records that the item in question was ever in the
  collection

If ``full_history_retention_period`` is not set, then artifacts in the
collection and the files they contain are never expired (and the value
of ``metadata_only_retention_period`` is then irrelevant). If
``metadata_only_retention_period`` is not set, then metadata-level history
of items in the collection is never expired.
