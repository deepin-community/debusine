.. _collection-singleton:

Singleton collections
=====================

Some collections are tightly associated with workspaces in such a way that
it makes sense to have exactly one of them per workspace.  For example,
:collection:`debusine:task-history` retains information about old work
requests, and is more likely to provide useful statistical information if
it's used consistently and automatically rather than needing to be
referenced manually.  Such collections are referred to as "singletons": each
workspace has at most one of each of them, normally created when the
workspace is created, and tasks can look them up implicitly rather than
needing them to be specified explicitly in task data.

Collections gain a constraint that their names may not normally begin with
an underscore (``_``).  Singleton collections are an exception to this.
Instead, collections of these categories must have a name consisting only of
a single underscore.  The existing constraint requiring collections to be
unique by name, category, and workspace then ensures that at most one such
collection may exist in any given workspace.

It is possible to refer to singleton collections using the existing
:ref:`lookup syntax <explanation-lookups>`, e.g.
``_@debusine:task-history``; this is useful in contexts such as :ref:`event
reactions <workflow-event-reactions>`.  A single underscore is valid as a
URL segment without being intrusive, so this works well when browsing
collections through the web interface.  Tasks should normally look up these
collections implicitly rather than having task data items for them.  The
existing inheritance logic falls back to parent workspaces if a singleton
collection does not exist in a given workspace.

The default ``System`` workspace has singleton collections.  Any new
workspace has them by default too, but there are options to disable their
creation.

The following collection categories are singletons:

* :collection:`debian:archive`
* :collection:`debian:package-build-logs`
* :collection:`debusine:task-history`
