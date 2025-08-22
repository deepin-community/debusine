.. task:: APTMirror

APTMirror task
--------------

This is a :ref:`server-side task <explanation-tasks>` that updates an
existing :collection:`debian:suite` collection to reflect the state of an
external APT suite.  It creates :artifact:`source <debian:source-package>`
and :artifact:`binary <debian:binary-package>` package artifacts as
required, as well as :artifact:`repository indexes
<debian:repository-index>`.

The ``task_data`` for this task may contain the following keys:

* ``collection`` (required): the name of the :collection:`debian:suite`
  collection to update
* ``url`` (required): the base URL of the external repository
* ``suite`` (required): the name of the suite in the external repository; if
  this ends with ``/``, then this is a `flat repository
  <https://wiki.debian.org/DebianRepository/Format#Flat_Repository_Format>`_
  and ``components`` must be omitted
* ``components`` (optional): if set, only mirror these components
* ``architectures`` (required): if set, only mirror binary packages for this
  list of architectures
* ``signing_key`` (optional): ASCII-armored public key used to authenticate
  this suite

.. todo::

   ``architectures`` should be optional, but discovering the available
   architectures without having to implement delicate GPG verification code
   ourselves is hard; see `message #49 in #848194
   <https://bugs.debian.org/848194#49>`_.

The suite must have at least a ``Release`` file to make it possible to
handle multiple architectures in a reasonable way, and if ``signing_key`` is
specified then it must also have either an ``InRelease`` or a
``Release.gpg`` file.  Source and binary packages must have SHA256
checksums.

Additions and removals of collection items are timestamped as described in
:ref:`explanation-collections`, so tasks that need a static view of a
collection (e.g. so that all tasks in the same workflow instance see the
same base suite contents) can do this by filtering on collection items that
were created before or at a given point in time and were not yet removed at
that point in time.

.. todo::

   Document exactly how transactional updates of collections work in
   general: tasks need to see a coherent state, and simple updates to
   collections can be done in a database transaction, but some longer update
   tasks where using a single database transaction is impractical may need
   more careful handling.  See discussion in :mr:`517`.
