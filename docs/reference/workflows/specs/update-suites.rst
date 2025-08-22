.. workflow:: update_suites

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
removed since the creation timestamp of its most recent ``index:Release``
item, it creates a :task:`GenerateSuiteIndexes` task, with task data as
follows:

* ``suite_collection``: the suite whose indexes should be generated
* ``generate_at``: the transaction timestamp at which the workflow
  orchestrator is being run (this needs special care to preserve
  idempotency, since any later runs of the same workflow orchestrator would
  have a different transaction timestamp)

To begin with, this workflow can just be run periodically, although that
will not scale well to large numbers of suites.  We should eventually have a
mechanism where changes to a collection can trigger a workflow.

.. todo::

    :issue:`Future work <756>` is needed to sign ``Release`` files, and will
    need to be integrated into this task.  That will probably involve
    creating a new sub-workflow that can deal with generating and signing
    indexes for a single suite.  Once :collection:`archives
    <debian:archive>` have been implemented, we may also want to group all
    the updates for suites in a given archive under a single sub-workflow.

.. todo::

    `Valid-Until
    <https://wiki.debian.org/DebianRepository/Format#Date.2C_Valid-Until>`__
    can be supported by adding a field to the suite collection specifying
    the validity period.  This workflow would then additionally include all
    those whose validity period is nearly up in its search criteria.
