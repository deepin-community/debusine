============================
Transitioning to scoped URLs
============================

As part of implementing :ref:`permission checks <permission-management-blueprint>`,
Debusine is moving towards scoped URLs, and it requires a transition plan.


Fallback scope introduced in migrations
=======================================

The database migration that introduces scopes for workspaces needs a fallback
scope to assign existing workspaces, and it was decided to use ``debusine``.

Existing instances of Debusine that will undergo database migration will
therefore find everything under the ``debusine`` scope. This is unlikely to be
what users need, so there needs to be a way to rename a scope after the
migration.

We implemented a management command to rename a scope. This is a
simple management command that changes ``Scope.name``, since only
foreign keys to scope are used to refer to scope at the database
level.

However, the parts of Debusine who are not yet scope aware are needing a name
to lookup a default scope, and this needs to be changed accordingly.

We added a ``DEBUSINE_DEFAULT_SCOPE`` setting (defaulting to
``debusine``) to specify the name of the default scope to use during
the transitioning period where not all of Debusine is yet scope-aware.

This is supposed to eventually disappear the moment a Debusine instance will be
able to host multiple scopes: at that point the idea of a fallback scope will
need to be removed, as it will become a cause of ambiguity.


Transitioning web URLs
======================

Most Debusine URLs are going to require a scope. For example,
``https://debusine.debian.net/workspace/`` will become
``https://debusine.debian.net/debian/workspace/``

A change of a URL namespace may break existing URL references. We can decide
that it's not worth the effort of setting up redirect, or to set up redirect
views forwarding to the configured fallback scope.

.. todo::

   Design a way to set up a system of best-effort redirects.


Transitioning APIs
==================

``debusine-client`` is currently not scope-aware, and API calls are currently
unable to use a scope different from the fallback one.

We keep using ``/api`` and pass the scope in a header.

This is straightforward, and it provides an easy compatibility option
to use the fallback scope if one is not provided in a header.

We add a new configuration (and also command line) option for
``debusine-client`` to configure a default scope.

An advantage of this over other options is that clients won't need to
edit URLs adding the scope before ``/api`` when changing scope.

.. todo::

   Document a header in API calls used to select scope.

   When the header is missing, use the fallback scope.

   Make sure we have a scope-aware client in testing and stable-backports
   before we drop support for unscoped API calls.
