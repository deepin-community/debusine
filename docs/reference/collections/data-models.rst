.. _collection-data-models:

Data models
===========

Collections have the following properties:

* ``category``: a string identifier indicating the structure of additional
  data; see :ref:`available-collections`
* ``name``: the name of the collection
* ``workspace``: defines access control and file storage for this collection; at
  present, all artifacts in the collection must be in the same workspace
* ``full_history_retention_period``, ``metadata_only_retention_period``:
  optional time intervals to configure the retention of items in the
  collection after removal; see :ref:`explanation-collection-item-retention`
  for details

Each item in a collection is a combination of some metadata and an optional
reference to an artifact or another collection. The permitted categories for
the artifact or collection are limited depending on the category of the
containing collection. The metadata is as follows:

* ``category``: the category of the artifact or collection, copied for
  ease of lookup and to preserve history. For bare-data items, this
  category is the reference value (it doesn't duplicate any other field).
* ``name``: a name identifying the item, which will normally be derived
  automatically from some of its properties; only one item with a given
  name and an unset removal timestamp (i.e. an active item) may exist in any
  given collection
* key-value data indicating additional properties of the item in the
  collection, stored as a JSON-encoded dictionary with a structure
  :ref:`depending on the category of the collection <available-collections>`;
  this data can:

  * provide additional data related to the item itself
  * provide additional data related to the associated artifact in the
    context of the collection (e.g. overrides for packages in suites)
  * override some artifact metadata in the context of the collection (e.g.
    vendor/codename of system tarballs)
  * duplicate some artifact metadata, to make querying easier and to
    preserve it as history even after the associated artifact has been
    expired (e.g. architecture of system tarballs)

* audit log fields for changes in the item's state:

  * timestamp (``created_at``), user (``created_by_user``),
    and workflow (``created_by_workflow``) for when it was created
  * timestamp (``removed_at``), user (``removed_by_user``),
    and workflow (``removed_by_workflow``) for when it was removed

This metadata may be retained even after a linked artifact has been expired
(see :ref:`explanation-collection-item-retention`). This means that it is
sometimes useful to design collection items to copy some basic information,
such as package names and versions, from their linked artifacts for use when
inspecting history.

The same artifact or collection may be present more than once in the same
containing collection, with different properties. For example, this is
useful when Debusine needs to use the same artifact in more than one similar
situation, such as a single system tarball that should be used for builds
for more than one suite.

A collection may impose additional constraints on the items it contains,
depending on its category. Some constraints may apply only to active items,
while some may apply to all items. If a collection contains another
collection, all relevant constraints are applied recursively.

Collections can be compared: for example, a collection of outputs of QA
tasks can be compared with the collection of inputs to those tasks, making
it easy to see which new tasks need to be scheduled to stay up to date.

Updating collections
--------------------

The purpose of some tasks is to update a collection.  Those tasks must
ensure that anything else looking at the collection always sees a consistent
state, satisfying whatever invariants are defined for that collection.  In
most cases it is sufficient to ensure that the task does all its updates
within a single database transaction.  This may be impractical for some
long-running tasks, and they might need to break up the updates into chunks
instead; in such cases they must still be careful that the state of the
collection at each transaction boundary is consistent.

To support automated QA at the scale of a distribution, some collections are
derived automatically from other collections, and there are special
arrangements for keeping those collections up to date.  See
:ref:`collection-derived`.
