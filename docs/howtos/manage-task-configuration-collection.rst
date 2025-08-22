.. _checkout-task-configuration:

=========================================
Manage task configuration for a workspace
=========================================

.. note::

   This is only available to workspace owners.

Pulling and pushing
===================

Data is stored in :bare-data:`debusine:task-configuration` entries in a
:collection:`debusine:task-configuration` collection.

Debusine allows maintaining it by checking it out to a local directory, editing
it, and pushing it back to Debusine. If the local checkout is a git working
directory, Debusine will take advantage of that to perform extra consistency
checks, and help prevent conflicts with other ways of updating it.

You can use ``debusine task-config-pull`` to check out a collection, and
``debusine task-config-push`` to push updates to a collection. Collection
information is stored locally, so that once a collection has been checked out,
push and pull work with no extra arguments.


Naming of files
---------------

``debusine:task-configuration`` entries pulled from the server are stored as
YAML files. The file names are ignored when matching remote entries with local
ones, so you can rename files and organize them into directories as you find it
convenient.

When new entries are found on the server during a pull, they are stored inside
a ``new/`` directory from which they can be safely dispatched to a more
meaningful location in the checkout.

When pulling changes on existing files, debusine is able to locate the local
files according to their contents and update them in place.


Git and non-git
---------------

If the local checkout is in git:

* Push and pull refuse to work if there are uncommitted changes to ``.yaml``
  files.
* If an entry has been deleted on the server, it will also be deleted locally.
* Push will refuse to work if the server contents were pushed from git, and
  their commit hash is not an ancestor of the local git commit

If the local checkout is not in git:

* If an entry has been deleted on the server, it will not be deleted locally to
  prevent accidental data loss, although it will be reported in a warning.

You can use ``--force`` to bypass the checks above.


Dry run
-------

If using ``--dry-run``, push and pull operations will go through all the
motions, including download and upload of data and reporting of change
statistics, but changes are not stored in the database.


Example task configuration files
--------------------------------

YAML files in the checkout are the :bare-data:`debusine:task-configuration`
serialized to YAML. Here are a few examples of task configuration items.

A template entry with a name "reduce-parallelism" that sets sbuild's
``build_options`` to ``parallel=2`` in a forced manner:

.. code-block:: yaml

    template: reduce-parallelism
    override_values:
      build_options: parallel=2

A template entry that applies the "reduce-parallelism" template to :task:`Sbuild`
tasks for ``openjdk-17``:

.. code-block:: yaml

    task_type: Worker
    task_name: sbuild
    subject: openjdk-17
    use_templates: [reduce-parallelism]
    comment: reduce parallelism to avoid crashing workers

An entry that only builds ``u-boot`` on ARM by default:

.. code-block:: yaml

    task_type: Workflow
    task_name: debian_pipeline
    subject: u-boot
    default_values:
      architectures_allowlist: ['arm64', 'armhf']
    comment: only build on ARM by default


..
  Quick way to test these entries on ``debusine-admin shell``:

     from debusine.artifact.models import DebusineTaskConfiguration
     import yaml

     DebusineTaskConfiguration(**yaml.safe_load("""
     …entry…
     """))
