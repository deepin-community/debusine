.. _collection-lookup:

Collection items lookup
=======================

Items in collections may be looked up using various names, depending on the
category. These names are analogous to URL routing in web applications (and
indeed could be used by Debusine's URL routing, as well as when inspecting
the collection directly): a name resolves to at most one item at a time, and
an item may be accessible via more than one name.  The existence of multiple
"lookup names" that resolve to an item does not imply duplicates of that
item or any associated artifacts.

All collections support a generic ``name:NAME`` lookup, which returns the
active item whose ``name`` is equal to ``NAME``.

Data and per-item data key names are used in ``pydantic`` models, and must
therefore be valid Python identifiers.
