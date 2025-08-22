.. collection:: debusine:workflow-internal

Category ``debusine:workflow-internal``
---------------------------------------

This collection stores runtime data of a :ref:`workflow
<explanation-workflows>`.  Bare items can be used to store arbitrary JSON
data, while artifact items can help to share artifacts between all the tasks
(and help retain them for long-running workflows).

Items are normally added to this collection using the
:ref:`action-update-collection-with-artifacts` or
:ref:`action-update-collection-with-data` action.

* Variables when adding items: none; pass an item name instead

* Data: none

* Valid items: artifacts of any category

* Per-item data: structure defined by workflows using the
  :ref:`action-update-collection-with-artifacts` or
  :ref:`action-update-collection-with-data` event reactions.  The
  ``variables`` or ``data`` fields respectively are copied into
  per-item data.  Names starting with ``promise_`` are reserved. This
  allows matching promises or promised artifacts using
  workflow-defined criteria.

* Lookup names: only the standard ``name:NAME`` lookup

.. note::

   When a workflow is contained within another workflow they share the same
   internal collection, so that a sub-workflow can access the artifacts
   produced by its parent workflow

.. note::

   The artifacts referenced through the internal collection should not
   expire while the workflow is running. But they should be allowed to
   expire once the workflow expiration delay is over.

   This will likely require to be able to flag a collection as not
   retaining their contained artifacts. And the delete-expired-artifact
   will thus have to be able to remove artifacts from collections that
   do not retain their artifacts.

   Workflow instances can only expire when their internal collection no
   longer contains any artifact. Otherwise the workflow instance is kept
   to facilitate the analysis of (the origin of) artifacts that were created
   by the workflow.

.. todo::

   The whole expiration point needs some redesign, tracked in issue #346
