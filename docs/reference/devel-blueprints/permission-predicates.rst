.. _permission-predicates-blueprint:

=====================
Permission predicates
=====================

The :ref:`permission management blueprint <permission-management-blueprint>`
introduces the concept of permission predicates, that are functions that check
whether a user can perform an operation on a resource, based mainly on the
roles their groups have on it.

This document is about the design of permission predicates.

This is a hard design problem because there are many ways of implementing
permission checking functions, all in principle equally valid.

Because permission checking is just being introduced in Debusine, we have
little data on how they would most efficiently be used.

Before proposing an implementation strategy, this document attempts to map the
ways permission predicates are expected to be used, and restrict their possible
initial uses in order to narrow down the range of possible implementations.

See :ref:`permission-management-blueprint-initial-roles` for an initial idea of
roles and associated actions, which is useful as context for this design
discussion.


Mapping the problem
===================

Roles can be delegated
----------------------

Checking for permissions on a resource does not only depend on the roles the
user has on that resource, because roles can be delegated to other resources:

* Having certain roles on a **scope** gives some permissions over its
  workspaces, collections, artifacts, workflows, and so on, without needing
  direct roles over them.
* Having roles on a **workspace** gives permissions over collections,
  artifacts, workflows, and so on, without needing direct roles over them.

In other words, permission checks on a resource may take into consideration
whether the user has roles on a containing resource.

In fact, at least at the beginning most permission checking on Debusine
resources will rely on delegating permissions from roles on containing
resources, since user stories that require more nuanced delegation of
permissions are not in our immediate implementation range.

Note that there may be special situations where delegation is disabled, such as
embargoed workspaces not visible by default except to their direct members, to
prevent accidental data exposure. Delegation would still be a main way of
operation, to avoid the complexity of micromanaging group roles on each
resource.

We do not only check roles
--------------------------

On top of roles, we also check:

* ``User.is_superuser`` and ``context.disable_permission_checks``, which
  disable permission checks. Note that we may not want to use ``is_superuser``
  directly, and only treat ``is_superuser`` as a "sudoer" marker that allows
  the user to activate superuser powers only when needed: that way an admin can
  use the same account for ordinary use, without fears of accidental exercise
  of powers.
* ``Workspace.public``, which gives permissions on a workspace which may be
  different from the one given by roles, and the visibility of collections,
  artifacts, workflows, and so on may delegate to it.

We test permissions on individual resources
-------------------------------------------

Predicates can be used to test permissions on an individual resource: can the
user display the contents of the collection being requested? Can the user write
to it?

We list resources for which we have permissions
-----------------------------------------------

Predicates can be used to filter resources to display for UI navigation or
selection lists in forms. For example:

* list visible workspaces
* list executable workflows
* list writable collections as targets for a workflow
* list groups of the current users

Testing individual resources
----------------------------

Most per-resource permission checking will happen based on the current
application context: "can the current user, in the current scope and workspace,
perform the given action on this resource?"

This is likely to happen frequently over the course of a request, and can
benefit from caching to avoid hitting the database on each resource to be
checked, especially since permissions on containing resources are likely to be
sufficient to grant permissions in most cases via delegation.

This means that permission checking on single instances can use shortcuts to
avoid hitting the database when the current user, scope and so on match the
current context, and use the filtering code to hit the database otherwise.

Filtering code can be turned into checking code by concatenating with
``exists``::

    if Model.objects.can_display(user).filter(pk=obj.pk).exists():
        ..

Filtering resources
-------------------

Filtering resources needs to hit the database in any case, so it is less likely
to benefit from shortcuts based on cached data for the current context.

Depending on use cases, one may or may not need to restrict results to the
current scope, workspaces and so on.

Resource querysets can have ``in_current_*()`` methods, like
``in_current_scope()`` and ``in_current_workspace()``, that filter results
using different aspects of the current application context.

Caching and invalidation
------------------------

To avoid the hard problem of cache invalidation, caches are only invalidated at
the end of the request, and therefore group role or membership changes only
come into action on the next request.


Implementation proposal
=======================

Predicate API
-------------

Checking individual resources
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    instance.can_<predicate>(self, user: User | AnonymousUser) -> bool

Test the predicate on a model instance. Some examples: ``can_display``,
``can_create_workspace``, ``can_add_artifact``.

To test the predicate on the current context, one passes ``context.user`` as
the user, and predicate code can check if ``user == context.user`` to apply
shortcuts if needed.


Filtering resources
~~~~~~~~~~~~~~~~~~~


::

    Model.objects.can_<predicate>(user: User | AnonymousUser) -> QuerySet[Model]

This manipulates a queryset, and so can be further refined with other methods
like ``in_current_scope()``, ``.filter(pk=obj.pk).exists()`` (to test on single
instances), and so on.


Behind the scenes
-----------------

Predicate functions are implemented in a custom ``QuerySet``, and use the
``.as_manager()`` method to propagate them to a model's manager::

    objects = ResourceQuerySet.as_manager()

or::

    objects = ResourceManager.from_queryset(ResourceQuerySet)

Predicate functions are decorated with ``@permission_check`` or
``@permission_filter`` decorators, which provide common behaviour such as
handling ``context.disable_permission_checks`` and active superuser privileges.

The test suite infrastructure provides an ``@override_permissions`` decorator,
similar to Django's ``@override_settings``, to mock permission checks.
