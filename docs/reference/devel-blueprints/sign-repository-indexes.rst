==========================
Signing repository indexes
==========================

As part of adding :ref:`repository hosting <package-repositories>` to
Debusine, it needs to be able to sign repository indexes.

.. workflow:: update_suite

Workflow ``update_suite``
=========================

This workflow updates metadata for a single :collection:`suite
<debian:suite>`.  Initially this will just involve generating and signing
basic indexes for the suite, but later it may also generate supplementary
files such as ``Contents-*``.

* ``task_data``:

  * ``suite_collection`` (:ref:`lookup-single`, required): the suite whose
    indexes should be generated

The workflow creates a :task:`GenerateSuiteIndexes` task, with task data as
follows:

* ``suite_collection``: ``{suite_collection}``
* ``generate_at``: the transaction timestamp at which the workflow
  orchestrator is being run (this needs special care to preserve
  idempotency, since any later runs of the same workflow orchestrator would
  have a different transaction timestamp)

If the suite or, failing that, its containing archive (if any) has a
non-empty ``signing_keys`` field, the workflow additionally creates two
:task:`SignRepositoryIndex` tasks with dependencies on the
:task:`GenerateSuiteIndexes` task.  They have task data as follows:

* ``suite_collection``: ``{suite_collection}``
* ``unsigned``: the :artifact:`debian:repository-index` artifact for the
  ``Release`` file produced by the :task:`GenerateSuiteIndexes` task
* ``mode``: ``detached`` for the first task, or ``clear`` for the second
* ``signed_name``: ``Release.gpg`` for the first task, or ``InRelease`` for
  the second

These tasks each have event reactions that add the output
:artifact:`debian:repository-index` artifact to the suite at the appropriate
path (``Release.gpg`` or ``InRelease``), setting ``created_at`` to the
transaction timestamp at which the workflow orchestrator is being run.

The ``InRelease`` task depends on the ``Release.gpg`` task, in order that
the "current" view of a suite can arrange to only show a snapshot once an
``InRelease`` file exists for it.

.. note::

    We use two tasks rather than making both signatures in a single task
    because otherwise the event reactions would have no way of
    distinguishing between the two output artifacts.

Changes to :workflow:`update_suites` workflow
=============================================

The workflow does nothing if the workspace does not have a (singleton)
:collection:`debian:archive` collection.

Key generation stage
--------------------

If the archive does not have ``signing_keys`` set in its data, then the
workflow first creates a :task:`GenerateKey` task, with task data as
follows:

* ``purpose``: ``openpgp``
* ``description``: a suitable description of the new key, identifying the
  workspace

It then creates a :ref:`workflow callback <workflow-callback>` with ``step``
set to ``generated-key`` and a dependency on the :task:`GenerateKey` task,
and stops populating the workflow graph at that point.  When called, that
callback sets the ``signing_keys`` field in the archive's data to be a list
containing only the fingerprint of the new :asset:`debusine:signing-key`
asset.

Main stage
----------

Instead of creating :task:`GenerateSuiteIndexes` tasks directly, the
workflow creates an :workflow:`update_suite` sub-workflow for each suite
that it considers to need updating, with task data as follows:

* ``suite_collection``: the suite whose indexes should be generated
* ``signing_keys``: the suite's ``signing_keys``, if present; otherwise, the
  archive's ``signing_keys``

If the key generation stage above created a :task:`GenerateKey` task, then
it adds the associated workflow callback as an additional dependency of the
:workflow:`update_suite` sub-workflow.

Key management
==============

By default, each :collection:`archive <debian:archive>` has its own OpenPGP
signing key, which is used to sign indexes in its :collection:`suites
<debian:suite>`.  This provides a reasonable default for common cases, where
needing to rotate keys for an archive for any reason has limited
consequences.

The signing key is generated automatically the first time metadata updates
for the suites in an archive are needed.  An administrator can manually
change the configuration for the relevant collections to use different
signing keys.  For example:

* During key rollovers, a suite's indexes may be signed using multiple keys.
* In some high-value cases, different suites in the same archive use
  different signing keys that are generated manually and have
  carefully-selected expiry periods: Debian itself is an example of this.
* All the workspaces in a scope may be controlled by the same customer, and
  it may be simpler for them all to use the same signing key.

.. todo::

    Debusine currently only supports OpenPGP signing with software-encrypted
    keys.  It should gain support for generating keys on PKCS#11 tokens and
    signing using those keys, and for exporting those keys under wrap in
    order to deal with hardware security models with a limited number of
    object slots.
