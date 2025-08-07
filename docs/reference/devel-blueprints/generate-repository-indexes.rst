=============================
Generating repository indexes
=============================

As the first step in adding repository hosting to Debusine, we need to be
able to generate `repository index files
<https://wiki.debian.org/DebianRepository/Format>`__ for :ref:`debian:suite
<collection-suite>` collections.

We should design the storage for these files in such a way as to make it
easy to serve repositories directly from Debusine artifacts (as opposed to
needing to write out the repositories to disk).

It is useful to serve historical snapshots as well as the current state of
the archive, as long as Debusine still retains the necessary data under
whatever retention policies are in effect.  The :ref:`collection data model
<collection-data-models>` already includes timestamps for when collection
items were created or removed, and supports :ref:`retaining
<explanation-collection-item-retention>` items even after they are no longer
active in their parent collection; as a result, repository indexes at a
particular timestamp can be found by querying for collection items
containing indexes that were not created after that timestamp and not
removed before that timestamp.

To support race-free mirroring, index files are served via `by-hash
<https://wiki.debian.org/DebianRepository/Format#indices_acquisition_via_hashsums_.28by-hash.29>`__
paths in addition to their base path.  These paths are handled implicitly by
the code that serves repositories, and are not recorded using separate
collection items.

.. _artifact-repository-index:

Category ``debian:repository-index``
====================================

This artifact stores an index file in a repository: this covers any file
that is not part of a source or binary package.

* Data:

* Files:

* Relationships:

  * relates-to: for ``Release`` files, the other index files mentioned in
    them
  * extends: for ``Release.gpg`` and ``InRelease`` files, the corresponding
    unsigned ``Release`` file

Note that while in principle a ``Packages`` or ``Sources`` file relates to
all the individual packages mentioned in it, actually creating those
artifact relationships would result in a very large amount of churn in the
``ArtifactRelation`` table for relatively minimal benefit, so we
intentionally skip those.

Changes to existing collections
===============================

The :ref:`debian:suite <collection-suite>` and :ref:`debian:archive
<collection-archive>` collections gain the following:

* Valid items:

  * ``debian:repository-index`` artifacts

* Per-item data:

  * ``path``: for index files, the path of the file relative to the root of
    the suite's directory in ``dists`` (e.g. ``InRelease`` or
    ``main/source/Sources.xz``)

* Lookup names:

  * ``index:PATH``: the current index file at ``PATH`` relative to the root
    of the suite's directory in ``dists``

:ref:`debian:suite <collection-suite>` also gains:

* Data:

  * ``indexes_generated_at``: the transaction timestamp at which the
    :ref:`task-generate-suite-indexes` was most recently run
  * ``duplicate_architecture_all``: if true, include ``Architecture: all``
    packages in architecture-specific ``Packages`` indexes, and set
    `No-Support-for-Architecture-all: Packages
    <https://wiki.debian.org/DebianRepository/Format#No-Support-for-Architecture-all>`__
    in the ``Release`` file; this may improve compatibility with older
    client code

Most index files are stored at the suite level: the code that serves an
archive as a whole will look at the appropriate suite when serving paths
under ``dists/SUITE/``.  There are a few exceptions for files that are not
packages and not under ``dists/``, such as override summaries and mirror
traces; these are not consulted by ``apt`` and so implementing them is not
urgent.

Overrides
=========

Overrides are used by Debian's traditional archive management software to
store the component, section, and (for binary packages) priority of each
package.  (Ubuntu's archive management software also supports `phased update
percentages
<https://wiki.debian.org/DebianRepository/Format#Phased-Update-Percentage>`__,
which are handled by overrides; other extensions are possible.)

While the name "override" might suggest that these are only applied where
the values are something other than the ones supplied by the package, in
fact every package in the archive has overrides even if those are equal to
the ones supplied by the package.  In Debian's traditional archive
management software, uploads of packages without overrides go into the
``NEW`` queue for manual review.

Review workflows of this kind are not yet in scope, but we already have
per-item data for component, section, and priority in the :ref:`debian:suite
<collection-suite>` collection which represent the common set of overrides
and are considered when generating ``Packages`` and ``Sources`` files.
Debian also publishes override summaries in an ``indices`` directory; we
treat these as just another kind of repository index file, although they are
stored at the archive level rather than at the suite level.

.. _task-generate-suite-indexes:

GenerateSuiteIndexes task
=========================

This is a :ref:`server-side task <explanation-tasks>` that generates
``Packages``, ``Sources``, and ``Release`` files (and their variants) for a
:ref:`debian:suite <collection-suite>` collection.

The ``task_data`` for this task may contain the following keys:

* ``suite_collection`` (:ref:`lookup-single`, required): the
  :ref:`debian:suite <collection-suite>` collection to operate on
* ``generate_at`` (datetime, required): generate indexes for packages that
  were in the suite at this timestamp

The task searches for packages that were in the suite at the given time,
builds the appropriate index files from them (setting ``Date`` in the
``Release`` file to the same timestamp as ``generate_at``), and adds them to
the collection.  It sets the ``created_at`` fields of the new collection
items to ``generate_at``; if there are any index files in the collection
that are not the most recent ones (which may include the ones that were just
created!), then it sets ``removed_at`` to the timestamp when the next-newest
index files were created.

.. _workflow-update-suites:

Workflow ``update_suites``
==========================

This workflow does whatever is needed to coordinate metadata updates for all
archives in a :ref:`workspace <explanation-workspaces>`.  Initially this
will just involve generating basic indexes for each of the suites in those
archives that have been changed, but later it may also generate
supplementary files such as ``Contents-*``.

The workflow operates on the workspace in which it was created.

* ``task_data``:

  * ``force_basic_indexes`` (boolean, defaults to False): if True,
    regenerate basic indexes (``Packages``, ``Sources``, ``Release``, and
    their variants) even if the state of the archive does not seem to have
    changed since they were last generated

For each suite in the workspace, if any collection items have been added or
removed since its current ``indexes_generated_at`` value, it creates a
:ref:`GenerateSuiteIndexes <task-generate-suite-indexes>` task, with task
data as follows:

* ``suite_collection``: the suite whose indexes should be generated
* ``generate_at``: the transaction timestamp at which the workflow
  orchestrator is being run (this needs special care to preserve
  idempotency, since any later runs of the same workflow orchestrator would
  have a different transaction timestamp)

To begin with, this workflow can just be run periodically, although that
will not scale well to large numbers of suites.  We should eventually have a
mechanism where changes to a collection can trigger a workflow.

.. todo::

    `Future work
    <https://salsa.debian.org/freexian-team/debusine/-/issues/756>`__ is
    needed to sign ``Release`` files, and will need to be integrated into
    this task.  That will probably involve creating a new sub-workflow that
    can deal with generating and signing indexes for a single suite.  Once
    :ref:`archives <collection-archive>` have been implemented, we may also
    want to group all the updates for suites in a given archive under a
    single sub-workflow.

Future work
===========

This blueprint only covers the bare minimum needed to generate valid
repository indexes, but real repositories often use more complex features.
For instance:

* `Valid-Until
  <https://wiki.debian.org/DebianRepository/Format#Date.2C_Valid-Until>`__
  can be supported by adding a field to the suite collection specifying the
  validity period.  The workflow that decides which indexes need to be
  regenerated would additionally include all those whose validity period is
  nearly up in its search criteria.

* Debian uses `Extra-Source-Only: yes <https://bugs.debian.org/814156>`__ to
  indicate that a source package is only present in an index due to being
  referenced by a binary package in the suite (via ``Built-Using`` or
  ``Source``).  Debusine has all the necessary information about which
  source and binary packages are in the suite and how they relate to each
  other, so it can add this field when generating ``Sources`` files.  (We
  may find that checking the relationships efficiently requires some
  additional database indexes.)
