.. _lookup-syntax:

=============
Lookup syntax
=============

Many tasks require users to specify artifacts or collections in some way.
Rather than having people write IDs everywhere, it's better to have a query
language with a more expressive syntax.

This syntax has four forms with different YAML types:

.. _lookup-string:

String lookup
=============

A string lookup consists of one or more segments, separated by the slash
character (``/``).

The first segment identifies a collection as ``name@category`` (e.g.
``debian@debian:archive``).  Some lookup contexts may provide a default
category, in which case the first segment may just be a ``name``.

Each subsequent segment is looked up using the ``lookup`` method on the
appropriate collection manager for the collection returned by traversing up
to the previous segment.  Segments that do not contain a colon are syntactic
sugar for the ``name:`` lookup (that is, the segment ``abc`` is looked up as
``name:abc``).  It is an error to specify more segments after one that does
not return a collection.

Some strings have a special meaning:

* ``NNN@artifacts`` or ``NNN@collections`` looks up an artifact or
  collection by ID.
* In the context of a parent workflow, ``internal@collections`` is syntactic
  sugar for that workflow's internal collection; it is equivalent to
  ``workflow-NNN@debusine:workflow-internal``, where NNN is the ID of the
  relevant workflow work request (with ``task_type: workflow``).

This lookup always returns exactly one artifact or collection.  It is an
error if no match is found.

.. _lookup-int:

Integer lookup
==============

An integer lookup looks up an artifact or collection by ID.  For this lookup
type to be valid, the context must specify that it expects an artifact or a
collection respectively.

This lookup always returns exactly one artifact or collection.  It is an
error if no match is found.

.. _lookup-single:

Single lookup
=============

A lookup for a single item is a :ref:`lookup-string` or an
:ref:`lookup-int`.

.. _lookup-dict:

Dictionary lookup
=================

A dictionary lookup specifies a collection and a list of filters that are
applied to it, returning all the artifacts or collections that match.

The ``collection`` key provides a string lookup (as above) which is resolved
to a collection.  It is an error if this lookup raises an error, returns
nothing, or returns a kind of item other than a collection.

All other keys are filters on properties of the active items in this
collection.  Only certain keys are allowed:

* ``child_type``: match items with the given type (``bare``, ``artifact``,
  or ``collection``), defaulting to ``artifact``.  ``any`` matches items of
  any type.  (Note to implementers: the choices here are not quite the same
  as the internal database representation.)
* ``category``: match items with the given category.
* ``name``: match items with the given name.
* ``data__KEY``: match items whose per-item data has a field called ``KEY``
  with the given value.  ``KEY`` may not contain ``__``.
* ``lookup__KEY``: match items using a collection-specific mechanism, by
  passing ``KEY`` and the corresponding value to the ``lookup_filter``
  method on the appropriate collection manager, which returns conditions
  that are ANDed with the other filters here.

In addition, some Django-style suffixes may be added to ``name`` or
``data__KEY`` to request different matching strategies, as follows:

* ``__contains``: the value contains the given substring
* ``__endswith``: the value ends with the given string
* ``__startswith``: the value starts with the given string

Other ``__`` suffixes are not currently supported; nor is using ``__`` to
navigate deeper structures embedded in per-item data, or using ``__``
suffixes on keys other than ``name`` or ``data__KEY``.

For example, the following lookup returns all the active
``debian:binary-package`` artifacts in the collection looked up by
``debian/trixie`` that have ``package: "libc6"`` and ``version: "2.37-15"``
in their per-item data:

.. code-block:: yaml

   collection: debian/trixie
   child_type: artifact
   category: debian:binary-package
   data__package: libc6
   data__version: "2.37-15"

.. _lookup-list:

List lookup
===========

A list lookup returns all the artifacts or collections that match any one of
the items in the list, where each item is a string, integer, or dictionary
lookup as above.  It is an error if any of the lookups in the list raise an
error.

For example, the following lookup returns all the active items in the
collection looked up by ``debian/trixie`` that have either (1) ``package:
"libc6-dev"`` and ``version: "2.37-15"`` or (2) have names starting with
``debhelper_13.15.3_``, and also the artifact with ID 123:

.. code-block:: yaml

   - collection: debian/trixie
     data__package: libc6-dev
     data__version: "2.37-15"
   - collection: debian/trixie
     name__startswith: debhelper_13.15.3_
   - 123@artifacts

.. _lookup-multiple:

Multiple lookup
===============

A lookup for multiple items is a :ref:`lookup-dict` or a :ref:`lookup-list`.
