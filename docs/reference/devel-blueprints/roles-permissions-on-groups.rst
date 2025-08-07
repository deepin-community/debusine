.. _roles-permissions-on-groups:

===============================
Roles and permissions on groups
===============================

Permission management in Debusine relies on users being members of groups, and
groups having roles on resources (see :ref:`permission-management-blueprint`).

This works well, except when deciding if a user can manage the membership of a
group: following this model, for each group there should be a related group
with admin roles on it, but then who can manage the membership of *that* group?

I propose to shortcut the situation by having users to be assigned direct roles
on groups, and groups alone.

Proposed changes
================

* Change the ``Group.users`` ``ManyToManyField`` to use a ``through`` table
  ``UserGroup``
* Add a ``GroupRoles`` enum, with ``MEMBER`` and ``ADMIN`` entries
* Add a ``role`` field to ``UserGroup``, defaulting to ``MEMBER``
* Add a ``can_manage`` permission predicate to ``Group``, granted to ``ADMIN``
  users and otherwise delegated to scope admins.
