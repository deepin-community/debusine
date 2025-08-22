.. _reference-signing-service:

Signing service details
=======================

Backend overview
----------------

There is a ``debusine.signing`` application, which is a separate Django
application under the ``debusine`` project with its database models routed
to a separate database, such that it can run on a separate system from the
Debusine server and workers.  It uses Django primarily for its database
facilities: there is initially no need for it to have its own server
component, although it may eventually be useful to add one for things like
reading audit logs.

This application has a worker, reusing most of the existing
``debusine.worker`` code.  It sends metadata to the server indicating that
it is a :ref:`Signing worker <explanation-workers>`, and that it requires
HTTPS connectivity to the server.  ``debusine-admin worker enable`` and
``debusine-admin worker disable`` only enable/disable signing workers if
given the ``--worker-type signing`` option, to avoid enabling them by
accident.

:ref:`Signing tasks <task-type-signing>` are scheduled similarly to worker
tasks, and are required to use the public API to interact with artifacts in
the same way that worker tasks do, but they only execute on signing workers.
Signing workers do not take tasks of any other type.

.. todo::

   Specify how to configure keys to be used with a YubiKey.

Each successful ``generate`` and ``sign`` operation adds a row to an
append-only audit log table.

Database models
---------------

Each key has a row with the following fields:

* ``purpose``: the purpose of this key (e.g. ``uefi`` for UEFI Secure Boot)
  different key purposes typically require different tools to generate them
  or sign data using them
* ``fingerprint``: the key fingerprint; keys are unique by purpose and
  fingerprint
* ``private_key``: a :ref:`protected representation <key-protection>` of the
  private key
* ``public_key``: the public key, as binary data
* ``created_at``, ``updated_at``: timestamps for creation and update

HSM key availability
--------------------

If keys are stored in a hardware security module such as a YubiKey, then
they may not be available to all signing workers.  A worker can add the list
of such keys it supports to its dynamic metadata, and then the
``can_run_on`` method of the relevant tasks can check that metadata to avoid
dispatching requests to workers that do not have access to the relevant
keys.

Security Model
--------------

Key Creation:

* Sysadmins can generate HSM-keys, outside Debusine.
* Sysadmins can import HSM keys into Debusine.
* Sysadmins can generate keys in Debusine.
* In the future, we expect key generation (of some sort, but it may as
  well be HSM) to be a routine part of creating a workspace that holds a
  repository. Probably delegated by a workflow, once :issue:`634` has
  landed.

:ref:`assets`:

* While artifacts may be created by any user with contributor access to
  a workspace, assets can only be created by administrators or certain
  tasks (e.g.  key generation).
* Unlike artifacts, which have permissions applied by the containing
  workspace, Assets have permissions directly associated with them, that
  can be altered by their owner.
* Asset's permissions may be tied to workspaces. Roles are defined on an
  (asset, workspace) pair. There could, in the future, be optional extra
  JSON restrictions attached to a permission for defence-in-depth.

Signing Key Assets:

* Signing key assets are used to manage the security of signing keys.
  They contain the fingerprint, purpose, and public part of the key.
* The :task:`GenerateKey` task will create a signing key asset.
* The :task:`GenerateKey` task can be directly executed by scope owners.
* The :task:`GenerateKey` task may be incorporated in workflows.
* In the future, the execution of the :task:`GenerateKey` task may be
  unrestricted.

Signing:

* The :task:`Sign` task may be incorporated into workflows. The workflow
  creator can then specify the signing key to be used.
* :task:`Sign` tasks are permitted to execute if the user running the task
  has the ``SIGNER`` role on the asset (via a group membership) in the
  workspace that the task is executing in.
* :task:`Sign` tasks should not be dispatched unless the user is permitted
  to use the given signing key.
* The signing service makes API requests back to the Debusine
  server to make permission determinations, as a final check.
* In the future, once we have more comprehensive permissions for workflows,
  we may grant permission to execute a :task:`Sign` task if the workflow was
  created and blessed by a user who has permission to use a given signing
  key. See :issue:`634`.

Automated Signing:

* When we have APT repositories implemented, it will be necessary for
  :task:`Sign` tasks to be triggered periodically, by Debusine, without any
  user linked to the request.
* These Signing Key Assets for these repositories will have a permission
  granting the ``SIGNER`` role to the workspace hosting the repository,
  without any associated group.

.. todo::

   Add more precise details of how this is recorded.
