.. _task-update-suite-lintian-collection:

UpdateSuiteLintianCollection task
---------------------------------

This task implements the :ref:`update-derived-collection-task` interface,
updating a derived :ref:`debian:suite-lintian collection
<collection-suite-lintian>` from a base :ref:`debian-suite collection
<collection-suite>`.

All active items in both collections are considered relevant.

Given a base ``debian:binary-package`` artifact with an architecture other
than ``all``, a derived item with the name
``{srcpkg_name}_{srcpkg_version}_{architecture}`` is desired.

Given a base ``debian:source-package`` artifact or for a base
``debian:binary-package`` artifact with ``Architecture: all``, derived items
with the names ``{srcpkg_name}_{srcpkg_version}_{architecture}`` are
desired for the following values of ``architecture``:

* ``source`` (only for a source package artifact with no corresponding
  binary package artifacts)
* ``all``
* each architecture where another base ``debian:binary-package`` artifact
  exists for the same source package name and version

The child work requests are for :ref:`Lintian tasks <task-lintian>`, with
the following ``task_data`` in addition to anything specified in
this task's ``child_task_data``:

* ``input``:

  * ``source_artifact`` (:ref:`lookup-single`): the relevant
    ``debian:source-package`` artifact in the base collection
  * ``binary_artifacts`` (:ref:`lookup-multiple`): a list of the relevant
    ``debian:binary-package`` artifacts in the base collection

Each child work request has an event reaction as follows, where
``{derived_collection}`` is the ``derived_collection`` from this task's
data:

.. code-block:: yaml

  on_success:
    - action: "update-collection-with-artifacts"
      artifact_filters:
        category: "debian:lintian"
      collection: {derived_collection}
