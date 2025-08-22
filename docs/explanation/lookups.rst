.. _explanation-lookups:

=======
Lookups
=======

:ref:`explanation-tasks` and :ref:`explanation-workflows` often need to
refer to :ref:`explanation-artifacts` and :ref:`explanation-collections`.
Rather than having to use raw IDs everywhere, this is normally done using
lookups, written in a special-purpose query language.  Lookups allow
traversing collections to find items stored in them.  Each :ref:`category of
collection <available-collections>` defines lookup names tailored to the
structure and content of the collection.

Some lookups resolve to a single artifact or collection, or raise an error
if it cannot be found: these are called "single lookups".  For example, a
common field in task data is ``environment``, specifying a tarball or image
that the task should use as its base execution environment.  A single lookup
that finds a bookworm image might be ``debian/match:codename=bookworm``.
This finds the ``debian`` collection (which in context is interpreted to be
of the category :collection:`debian:environments`) and asks it to run the
``match`` lookup for ``codename=bookworm``; the collection then returns a
suitable artifact.

Some lookups resolve to multiple artifacts or collections, based on filters
applied to the items in a collection: these are called "multiple lookups".
For example, a workflow might take a ``binary_artifacts`` field listing the
binary artifacts that it should process.  A multiple lookup that finds all
the binary packages produced by the ``glibc`` source package in Debian's
``trixie`` suite, regardless of architecture, might be:

.. code-block:: yaml

   collection: trixie@debian:suite
   child_type: artifact
   category: debian:binary-package
   data__srcpkg_name: glibc

This finds the ``trixie`` collection of category :collection:`debian:suite`
and then applies the given filters to all the items of that collection.

In some cases, lookups may resolve to :bare-data:`promises
<debusine:promise>` rather than to real artifacts.  These allow workflows to
refer to artifacts that may not yet exist, but that are expected to be
provided by other tasks or workflows.  In conjunction with the internal
collection associated with all workflows, this can be used to plug multiple
sub-workflows together into more complex "pipelines".  For example, under a
single root workflow, an :workflow:`autopkgtest` sub-workflow that wants to
consume the output of an :workflow:`sbuild` sub-workflow might use:

.. code-block:: yaml

   binary_artifacts:
     collection: internal@collections
     child_type: artifact
     category: debian:upload
     name__startswith: build-

See :ref:`lookup-syntax` for reference documentation on writing lookups.
