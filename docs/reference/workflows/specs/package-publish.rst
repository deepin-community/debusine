.. _workflow-package-publish:

Workflow ``package_publish``
============================

This workflow publishes source and/or binary packages to a given target
suite.  It is normally expected to be used as a sub-workflow.

Permission considerations
~~~~~~~~~~~~~~~~~~~~~~~~~

Copying artifacts requires both the ability to read from the source and the
ability to write to the destination (either directly or via a workflow).

After artifacts have been made public, it's helpful to be able to see the
work request that created them, without having to somehow also copy the work
request around.  To achieve this, the permission predicate that checks
whether a user can see a work request may check whether any of the artifacts
produced by the work request are visible to that user, and return True in
that case even if the work request itself would not ordinarily be visible.

.. note::

   It may be surprising that this rule is "any of the artifacts produced by
   the work request" rather than "all of the artifacts produced by the work
   request"; but there isn't usually anywhere useful to copy
   :ref:`debusine:work-request-debug-logs
   <artifact-work-request-debug-logs>` artifacts to, and making only some of
   the artifacts produced by a work request public seems unlikely to be a
   realistic unembargoing use case.

While build logs may expose additional information not in the output
artifacts (such as build-dependencies where security updates are also being
prepared), similar information might easily be exposed by the output
artifacts themselves anyway, so the onus is on people who make artifacts
public to check that it is safe to do so.

.. todo::

   Permission checks for this workflow are not yet implemented.

Resource accounting considerations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

We want to be able to track the resource usage of workspaces and scopes.  If
artifacts are copied between workspaces (and hence perhaps between scopes),
then the same files may exist in multiple workspaces, complicating this kind
of analysis.  The question is likely to be something along the lines of "how
much data does Debusine need to store on behalf of this workspace or scope
that it would not otherwise need to store?".

A reasonable first cut would be to track the origin of copies, and to
account an artifact's files to a workspace (and its containing scope) if the
artifact is in that workspace and is no longer in its origin workspace.  We
therefore add a nullable ``Artifact.original_artifact`` foreign key, with
``on_delete=SET_NULL``.

Some other variations are possible, and are not made more difficult by this
design.  For example, we may wish to account for each workspace's usage
without considering whether files have been copied from or to other
workspaces (in which case the total file store size may be less than the sum
of the sizes of all workspaces); or to calculate the "unique" size of a
workspace as the total size of all files that appear only in that workspace.

Workflow definition
~~~~~~~~~~~~~~~~~~~

* ``task_data``:

  * ``source_artifact`` (:ref:`lookup-single`, optional): a
    ``debian:source-package`` or ``debian:upload`` artifact representing the
    source package (the former is used when the workflow is started based on
    a ``.dsc`` rather than a ``.changes``)
  * ``binary_artifacts`` (:ref:`lookup-multiple`, optional): a list of
    ``debian:upload`` artifacts representing the binary packages
  * ``target_suite`` (:ref:`lookup-single`, required): the ``debian:suite``
    collection to publish packages to
  * ``unembargo`` (boolean, defaults to False): if True, allow publishing
    artifacts from private workspaces to public suites
  * ``replace`` (boolean, defaults to False): if True, replace existing
    similar items
  * ``suite_variables`` (dictionary, optional): pass these variables when
    adding items to the target suite collection; if a given source or binary
    artifact came from a collection, then this is merged into the per-item
    data from the corresponding collection item, with the values given here
    taking priority in cases of conflict; see :ref:`debian:suite
    <collection-suite>` for the available variable names

At least one of ``source_artifact`` and ``binary_artifacts`` must be set.

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.package_publish::PackagePublishWorkflow.build_dynamic_data

``target_suite`` is looked up relative to this workflow's workspace.  As a
result, it must either be part of this workspace's inheritance chain, or
else be identified by ID (``NNN`` or ``NNN@collections``).

The workflow creates a :ref:`task-copy-collection-items`.  The ``copies``
field in its task data is as follows:

* ``source_items``: the union of whichever of ``{source_artifact}`` and
  ``{binary_artifacts}`` are set
* ``target_collection``: ``{target_suite}``
* ``unembargo``: ``{unembargo}``
* ``replace``: ``{replace}``
* ``variables``: ``{suite_variables}``

Any of the lookups in ``source_items`` may result in :ref:`promises
<bare-data-promise>`, and in that case the workflow adds corresponding
dependencies.

If ``binary_artifacts`` is set and the source and target workspaces have
different instances of the :ref:`debian:package-build-logs
<collection-package-build-logs>` collection, then the workflow also adds an
entry to ``copies`` as follows:

* ``source_items``:

  .. code-block:: yaml

      collection: {source build logs collection}
      lookup__same_work_request: {binary_artifacts}

* ``target_collection``: target build logs collection
* ``unembargo``: ``{unembargo}``
* ``replace``: ``{replace}``

If ``binary_artifacts`` is set and the source and target workspaces have
different instances of the :ref:`debusine:task-history
<collection-task-history>` collection, then the workflow also adds an entry
to ``copies`` as follows:

* ``source_items``:

  .. code-block:: yaml

      collection: {source task history collection}
      lookup__same_workflow: {binary_artifacts}

* ``target_collection``: target task history collection
* ``unembargo``: ``{unembargo}``
* ``replace``: ``{replace}``
