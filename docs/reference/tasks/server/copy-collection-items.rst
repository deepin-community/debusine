.. _task-copy-collection-items:

CopyCollectionItems task
------------------------

This server task copies items into given target collections, which may or
may not be in the same workspace as the original items.  It returns an error
if:

* the user/workflow that created the task does not have permission to read
  the items or to write to the target collection
* any of the items is a collection
* ``unembargo`` is False, any of the items are in a private workspace, and
  the target collection is in a public workspace
* the collection manager fails to add the items (e.g. because they are
  incompatible with the collection)

The ``task_data`` for this task may contain the following keys:

* ``copies``: a list of dictionaries as follows:

  * ``source_items`` (:ref:`lookup-multiple`, required): a list of items to
    copy (as usual for lookups, these may be collection items or they may be
    artifacts looked up directly by ID)
  * ``target_collection`` (:ref:`lookup-single`, required): the collection
    to copy items into
  * ``unembargo`` (boolean, defaults to False): if True, allow copying from
    private to public workspaces
  * ``replace`` (boolean, defaults to False): if True, replace existing
    similar items
  * ``name_template`` (string, optional): template used to generate the name
    for the target collection item, using the ``str.format`` templating
    syntax (with variables inside curly braces)
  * ``variables`` (dictionary, optional): pass these variables when adding
    items to the target collection; if a given source item came from a
    collection, then this is merged into the per-item data from the
    corresponding source collection item, with the values given here taking
    priority in cases of conflict

For each of the entries in ``copies``, the task copies the source items to
the target collection's workspace; when copying artifacts, if the contained
files are already in one of that workspace's file stores, then it copies
references to them, and otherwise it copies the file contents.  For each
source item, it then adds a collection item to the target collection, using
``name_template`` and ``variables`` in the same way as in
:ref:`action-update-collection-with-artifacts`.

All the requested copies happen in a single database transaction; if one of
them fails then they are all rolled back.
