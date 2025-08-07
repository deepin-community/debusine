.. _permission-management-blueprint:

=====================
Permission management
=====================

.. note::

  Permission design is currently only relevant at the enforcement and validation
  level. Permission-related UI features, like filtering a workflow input
  parameter with only the collections a user can write to, are out of scope in
  this stage of Debusine development.

Relevant domain examples
========================

Permission-related needs
------------------------

* Different organizations hosted in the same Debusine instance, each with its
  own set of admins/permissions
* Sudden loss of trust on a person
* Delegation (people with admin access to something can grant further
  permissions to other people)
* Make sure that private keys managed by Debusine can only be used by
  authorized people for specific operations

Examples of operations
----------------------

* Manage site-wide work request priorities
* Review work requests that need manual approval, e.g. signing
* Manage workflow templates
* Create workspaces (and maybe collections?)
* Adding/removing workers
* Cross-boundary operations, like copying something from a workspace to another

Organization specific permission management cases
-------------------------------------------------

* Debian Maintainers

    * Non uploading DDs are effectively DMs
    * One needs to track who allowlisted a package for a DM
    * Mirror roles from ftp-master?

These can be implemented ad-hoc, such as with organization-specific test steps
in workflows, to avoid overcomplication of the permission system design with
generalizations of edge cases.

Workflows and additional capabilities
-------------------------------------

A workflow run by a person may have capabilities that the person does not have.

Example: LTS or ELTS contributors cannot write to the published suites
directly, but can do so via workflows.

Although workflows need a way to escalate capabilities when needed, by default
they need to be constrained by the capabilities of the user who started them.

Escalating capabilities in a workflow should be designed around gaining
capabilities rather than user switching (e.g. suid workflows), to avoid
granting unintended privileges and making auditing harder.

There may be workflow-specific permission checking validation, performed right
after pydantic validation of its input.

An extra element to take into account is use of signing keys: capabilities
related to them need to only be controlled by the people who control the keys,
which might not be the same as all workspace admins.

Conceptual constraints
----------------------

Debusine users and developers are most likely to be familiar with a Unix-like
or GitLab/GitHub-like User/Group permission model, and it is important to keep
that in mind in the design.


Permission management vocabulary
================================

Scope
-----

A Debusine instance hosts one or more *scopes*.

Scopes are potentially separate and unrelated entities. They should be
broad. For example two separate customers hosted on the same Debusine
instance in a software as a service arrangement.


User
----

A user identified by the Debusine instance.

Users can have access to one or more scopes (as in the example of Debian
and Freexian)


Group
-----

A group is a scope-specific collection of users.


Resource
--------

An element on a Debusine instance on which operations can be performed.

Examples: Workspace, Collection, Workflow, Group, Scope, Artifact


Role
----

Role of a group within a resource.

Each resource in Debusine has a finite and pre-defined set of roles. One or
more groups can be attached to each role for a resource.

Resources that have categories may have a different set of pre-defined roles
for each category.

Examples: admin for a group, uploader for a debian suite, admin for a scope, …


Operation
---------

Resource-specific action that can be performed.

What follows is a list of examples. It is informative rather than normative,
since some operations can be contextualized in different ways: for example,
deleting a workspace could be an operation on a workspace or on a scope.

* Scope

  * create workspace
  * create group
  * delete group
  * add user

* User

  * reset password
  * lock user
  * delete user
  * create user
  * change email address
  * change username

* Group

  * add user
  * remove user
  * delete group

* Workspace

  * create collection
  * create artifact
  * list existing workflow templates
  * create a new workflow template

* Collection

  * list items
  * add an item
  * remove an item

* WorkflowTemplate

  * remove a workflow template
  * edit a workflow template
 
* Workflow

  * start a workflow (out of an existing WorkflowTemplate)

* Artifact

  * Download


Permission
----------

Permissions are predicates that check whether a user can perform an operation
on a resource, based on the roles their groups have on it.

Depending on implementation choices, some special-case permission predicates
may ignore groups and roles and take decisions merely based on the existence of
a user, such as checks for operations accessible by anonymous visitors, or by
any logged in user.


Catalog of permission use cases
===============================

Public workspaces
-----------------

Visible to non logged in users.

Private workspaces
------------------

Artifacts and collections are not visible except to a group of users, and can
be copied out when ready.

For example: embargoed workspaces

Signing a package
-----------------

Signing a package (like grub) using keys from Debusine’s HSM.

Restricting a key to only a list of source package can be implemented without
special support from the permission system, by having it as a policy at the
``WorkflowTemplate`` level.

Uploading an artifact to a workspace
------------------------------------

Uploading an artifact to a workspace, outside of collections, is currently
needed to provide inputs to a ``WorkflowTemplate``.

We can force a short expiration on these kind of uploaded artifacts: if a
pipeline produces artifacts related to it, it will keep it alive.

This would have a shortcoming in which if an artifact is uploaded, and it takes
a long time to start the workflow and produce the resulting artifacts, the
input artifact may expire before the workflow needs it. This can be solved by
creating the workflow internal collection immediately when the workflow is
created, and adding the artifact to it.

Run the Debian pipeline workflow
--------------------------------

* Workspace: Needs upload right to a workspace to store the artifact
* WorkflowTemplate: Needs right to start the corresponding workflow
* Collection: Needs right to fetch environment related artifacts
* PrivateKey: Needs right to generate signatures for UEFI binaries
* PrivateKey: Needs right to sign .dsc and .changes (if not signing externally)
* Collection: Needs right to add to a target collection

We can design things so that the workflow populates the graph of work requests
depending on current permissions, or so that the shape of the graph is not
influenced by permissions, which are only taken into account to check if the
WorkflowTemplate can be started.

At the current stage, we can assume the latter: that the shape of a workflow
graph will not change depending on permissions.

Debian pipeline for embargoed Security
--------------------------------------

It would be a WorkflowTemplate running the Debian pipeline with an extra step
at the end.

Any DD may start a proposed security update, and only Security Team people can see the results.

There are special considerations about this:

* If a developer can see the build logs of their packages’ embargoed builds,
  they may be able to see that some of the dependencies have new versions
  otherwise not visible, and therefore leak some information about currently
  embargoed updates.

* If a developer cannot see the build logs and their package breaks when
  building on security’s workspace, they would need assistance from the
  security team to debug what happened

The Worker would need rights to fetch some private embargoed artifacts /
collection / apt repository during the build, taken from a collection that the
user who started the workflow cannot read.

To do that, the permission for reading an artifact (as should be used by the
artifact download view) can follow the chain from the worker token to the
worker, to the work request, to the workflow, and can use the extra permissions
from the workflow if direct permissions are not available (ApplicationContext
can be leveraged to implement this).

Publish a package to a distribution (sid-like case)
---------------------------------------------------

A developer would not have permission to upload an artifact to a collection,
but they would have permission to start a WorkflowTemplate that publishes
artifacts to that collection.

Publish a package to a distribution (stable-like case)
------------------------------------------------------

This can be done via a Debian pipeline that waits for confirmation from the
release manager before publishing a package to the target collection.

The build would include the target collection as a mirror.

Only a group of release managers can approve publishing.

Release managers have an interface to list the pending workflows that intend to
publish to the collection that they manage, and provide approval.

Maintain a personal repository
------------------------------

Scope permissions: create a workspace for each “personal repository” where you
have full rights, initialized with a standard set of ``WorkflowTemplate``

Workspace permissions:

* create a collection in that workspace
* Grant people/groups access to that workspace
* Upload access may be restricted at the workflow level instead of the
  workspace level, by defining who can start a workflow that can upload
  packages
* Manually add/remove artifacts from the published collection
* Generate and maintain a signing key for the repository
* Add collections as “build dependencies” (e.g. use someone else’s Qt personal
  repository to build a desktop application)

Syncing group membership with external sources
----------------------------------------------

Examples of groups that may need to be synchronized with external sources:

* Security Team
* Release Team
* FTP Master, FTP Assistant
* Debian Developers. Note that we currently debusine.debian.net allows any
  Debian Developer to create an account for themselves, and it does not yet do
  other forms of syncing. For example, a Debian account that gets closed
  remains open in Debusine.
* Debian Maintainers and non-uploading Debian Developers that have been
  allowlisted for a package
* Other Debian contributors manually admitted by Debian Developers (for
  example: sponsoree)


High level design choices
=========================

Scoping for workspaces
----------------------

We need scoping for workspaces.

For example, Debian, Freexian and Kali may all have a distinct "Public" workspace
and a distinct "Sandbox" workspace.

There needs to be a way to share workspaces between scopes: for example, Debian
may share the workspace that hosts the mirrored suite collections.

Scoping for groups
------------------

We need scoping for groups.

For example, Debian, Freexian and Kali may all have a distinct "Admin" group.

Scoping for users
-----------------

Users are global.

Usernames are global across all scopes.

Usernames and group names are different namespaces: a user and a group may have
the same name and be completely unrelated.

SAAS hosting model
------------------

We design for a gitlab-style hoster model:

* scope is encoded in URL paths (e.g. ``https://www.debusine.net/kali/``)
* user namespace is global (e.g. user ``enrico`` is the same in Debian, Freexian and
  Kali, although it may not have access to the Kali scope)

We do not aim for a federation-like model where each hosted scope has a
distinct hostname and user namespace.

One workspace per distribution
------------------------------

We can assume that each published distribution will have its own workspace,
which contains its collection and all supporting collections needed to allow
people to cooperate on it.

This allows to delegate most, if not all, permission checks on collections and
other resources to Workspace roles.

This would structure work in Debusine around many cooperating workspaces:

* One workspace for the ftpmasters to publish the main repo:
    * Workflow: MergeProposedUpdates, can be started by release managers.
    * Workflow: UploadToUnstable, can be started by any DD.
* One workspace for the stable release managers for proposed updates:
    * Workflow: SubmitUpdate, can be started by any DD.
* One workspace for the security team to manage embargoed updates.
* One workspace for public security repo.
* One workspace for wanna-build team to maintain build environments:
    * Manage workflow to update the build environments.
    * Manage workflow to build packages missing on some architectures.

WorkflowTemplates would act as the interface that can be used by regular users
operate on the workspaces.


User and group structure
========================


Users and scopes
----------------

A user can have access to multiple scopes. For example: Debian and
Freexian contributors, or Debian and Kali Linux.

Groups
------

Users belong to one or more scope-specific groups.

Groups do not span multiple scopes, so group names can be reused across
scopes.

It left undetermined at this stage if groups can contain other groups. We can
assume, for simplicity of initial implementation, that they do not, and leave
the question open to be revised at a further design iteration.


No automatic personal collections
---------------------------------

GitLab/GitHub have a system where one's username is automatically a URL
namespace under which artifacts are published.

Debusine does not have an equivalent per-user collection, and users may or may
not be able to create collections to publish artifacts.

The permission management for creating collection may need to discriminate by
collection type, so that a user might be able to create a hypothetical
``debusine:sandbox`` collection, but not a ``debian:suite`` collection.



High level implementation plan
==============================

Checking permissions
--------------------

Permission checks should happen:

* at the API boundary (as part of the validation of an API invocation)
* at the model level (for example, making Manager or Model methods become
  user-aware)

Both API-boundary and model-level checks are useful. For example, consider a
long-running build that uploads to a collection at the end: the person who
started the build may have lost access to the collection during the build.

Permissions are checked only on groups
--------------------------------------

To simplify checks, roles can only be assigned to groups, not users.

The system provides no way to assign a role directly to a user, and a group
must be created to make the connection.

This both simplifies implementation, and encourages users to think in term of
teams rather than in terms of users: instead of assigning a role to a specific
person, one can think in terms of "hats": what hat is the person going to wear
in that scope or workspace? One can then figure out what other roles are needed
for that hat, create a group with the required set of roles to represent the
hat, and add the user to it.

It is unlikely that a given permission structure is needed by a specific
person, and it's more likely that it's needed by a specific team, even though
at the beginning the team may very well be of only one person.

When a new scope is created, it will need to be populated with at least one
``Admin`` group with the people that will manage the scope. That group will
have the roles needed to be able to take care of the scope, and it will
represent the admin team of the newly created scope.


Permission for the anonymous user
---------------------------------

A possible implementation of this using groups is that a role on a resource can
be assigned to a ``NULL`` group, or to an ``Anonymous`` group (name subject to
change at implementation time), in which case it is granted to site visitors
that are not logged in.

Another possible implementation is to special-case public and non-public
resources as we currently have.

An aspect to keep into account for choosing between the two is how easy one can
get surprising behaviour where "add the Anonymous group as reader" looks like
more like assigning reader role to a limited group rather than making the
resource public: that special casing is likely to still be needed at the UI
level.

Another aspect to consider is that if ``Anonymous`` or ``Users`` groups with
synthetic membership need special casing in permission checks, then the special
casing can be done without them, likely leading to clearer code that does not
rely on a redundant intermediate object.

Permission for any logged in user
---------------------------------

There are no know use cases for this, though it is currently implemented as a
stand-in for future, more mature permission implementations.

If a use case of this kind will emerge, it is likely it can be implemented by
checking if a user is part of any groups that belong to a scope.

Sharing workspaces across scopes
--------------------------------

Workspaces support inheritance and inheritance can be allowed to cross the
scope boundary.

Sharing a ``Mirrors`` workspace from, e.g. scope ``Debian`` to scope
``Freexian``, can be implemented by creating a ``Debian Mirrors``
workspace in ``Freexian`` that inherits from ``Debian``/``Mirrors``.

Application context
-------------------

An application context is an object, from an ``ApplicationContext`` class
hierarchy, that encodes the scope of an operation.

Its most general class would contain ``scope`` and ``user``.

More specific subclasses may contain ``workspace``, ``group`` and ``role``,
``work request``, ``collection``, ``artifact``, and so on, as appropriate for
the operation to be performed.

Application context classes provide methods to check permissions, and provide
model objects as context for operations. For example, a model method that
creates an artifact can get a WorkspaceApplicationContext argument, from which
it can derive scope, workspace and user.

Application context classes can also work as a cache for the model objects that
had to be queried during the authorization phase.

Application context instances can be stored in the request, to be available as
a low-level backend to implement checks in Django view mixins and Django
Request Framework permission objects.

Application contexts contain information about the scope of an operation, not
about its arguments, to avoid (ab)using application contexts as argument
bundles.

Workspaces and Workflows
------------------------

The initial permission structure of Debusine is modeled around cooperating
workspaces, with each workspace having its own set of admins and regular users.

Only admins would be able to manipulate collections directly inside a
workspace, while regular users would only interact using WorkflowTemplates. The
set of WorkflowTemplates configured in a workspace thus becomes the main
interface for regular work on the workspace.

Adding roles and permissions only to Workspaces and WorkflowTemplates could
mean having enough granularity to regulate both maintenance access (add/remove
elements manually, grant permissions) and operational access (run pipelines):

* Most people will have read-only access to a workspace, or not even that in
  case of embargoed workspaces
* Only admins are likely to do anything besides read-only operations directly
  on collections and other elements
* Operations are performed via WorkflowTemplates, for which permission to start
  can be granted as needed. Note that, currently, being able to start a
  WorkflowTemplate implies being able to upload an artifact to its workspace to
  provide it with input.
* Different types of workspaces differ on the set of WorkflowTemplates they get
  when they get created, or what WorkflowTemplates an admin picks for them when
  creating them

Regardless of the above, there should be a permission check at the collection
level, even if it delegates to the containing workspace: this allows us to
change permission structure later without needing to sift the code to introduce
further checks

When checking for permission on an internal workflow collection, write access
should be granted if the operation is being run by the workflow that owns it.
The person who started the workflow does not automatically get write access to
it, to avoid operations on it to happen outside the workflow control.

While to provide input to a WorkflowTemplate one needs to be able to (and have
permission for) upload an artifact to a workspace outside of its collections,
this may not be needed in the future if we change workflows so that their input
can be provided as part of the procedure to start the workflow. There is
currently no other reason for allowing an ordinary user to upload an artifact
to a workspace.

.. _permission-management-blueprint-initial-roles:

Initial roles
-------------

Expected roles on workspaces:

* Owner:
    * Can add/remove groups on each role
    * Can remove the workspace
    * Can configure workspace level settings (visibility level, inheritance, etc.)
    * And everything below it.
* Administrator:
    * Can create/remove/configure WorkflowTemplates
    * Can create/remove/configure collections
    * Can start work requests outside of workflows
    * And everything below it.
* Maintainer:
    * Can arbitrarily add/remove items to collections
    * Interact with all workflows (retry failed work requests, retry workflow, abort workflow, abort work request, etc.)
    * And everything below it.
* Developer:
    * Inspect everything inside the workspace (artifacts, collections,
      workflows, work requests, etc.)
    * Interact with its own workflows (retry failed work requests, retry workflow, abort workflow, abort work request, etc.)
* Contributor:
    * Can upload artifacts inside the workspace, but not register them in
      collections
    * Can list workflow templates within the workspace (and permissions on workflow
      templates then govern whether one is allowed to run them)
    * Can start workflows from workflow templates based on permissions at the
      workflow template level
* List the permission structure/membership structure:
  To be decided. In Debian this is currently open, but it may not be desirable
  for all scopes in Debusine. We can start allowing everyone to see it, and
  still implement permission checks for them now (and make them always succeed)
  to have them in place for when we model this at a later stage.

Expected roles on WorkflowTemplates:

* Administrator:
    * Can modify the parameters
    * Defaults to set of groups from roles >= Workspace/Administrator
* Contributor:
    * Can start a WorkflowTemplate
    * Defaults to set of groups from roles >= Workspace/Contributor

Expected roles on Scopes:

* Owner
    * Can tweak scope level settings (at least visibility, maybe associated domains, etc.)
    * Can remove the scope
    * Can add/remove groups on each role
    * Can create/remove groups
    * And everything below.
* Administrator
    * Can add/remove users to groups
    * Can create/remove/modify workspaces

Modeling permissions on signing keys is left to a following iteration of the
permission design.


Proposed implementation changes
===============================

Implement Scopes
----------------

* Add a new Scope model
* Create a ``Debusine`` scope to use as default in data migrations
* Make Workspace unique by Scope

Implement Group
---------------

* Specialize Django groups
* Make Group unique by Scope
* Create empty ``Anonymous`` and ``Users`` groups for each scope, if needed by
  predicates

Implement application context
-----------------------------

* Initial class hierarchy
* Infrastructure to instantiate application context and add them to the Django
  request

Add roles for existing use cases
--------------------------------

* Create in the code an initial set of roles for Workspace
* Create a role table for Workspace
* If needed by predicates, give ``Anonymous`` read access to existing public
  workspaces
* If needed by predicates, give ``Users`` read/write access to existing private
  workspaces

Further role/permission design is postponed to a future iteration of permission
management design work, when we have gathered a broader collection of actual use
cases.

Encode permissions
------------------

* Implement permission checks in application contexts
  (`django-rules`_ could be used as a helper library)

Add permission checks at the API boundary
-----------------------------------------

* Create View mixins that perform permission checks alongside input validation
* Cache model objects looked up by permission checks to be used by downstream
  code

Add permission checks at the model boundary
-------------------------------------------

* Make relevant Manager or Model methods user-aware
* Refactor view code to use higher level Manager or Model methods

This can be implemented either by passing the application context explicitly to
model methods, or by implementing a `contextvar` (or similar) system to
introduce the concept of a "current application context". This can be decided
at implementation time.


_`django-rules`: https://github.com/dfunckt/django-rules
