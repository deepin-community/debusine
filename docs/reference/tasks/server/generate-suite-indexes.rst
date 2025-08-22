.. task:: GenerateSuiteIndexes

GenerateSuiteIndexes task
-------------------------

This server task generates ``Packages``, ``Sources``, and ``Release`` files
(and their variants) for a :collection:`debian:suite` collection.

The ``task_data`` for this task contains the following keys:

* ``suite_collection`` (:ref:`lookup-single`, required): the
  :collection:`debian:suite` collection to operate on
* ``generate_at`` (datetime, required): generate indexes for packages that
  were in the suite at this timestamp

To keep track of which indexes were current at any given time, the task
operates in the following phases within a transaction:

* It finds the previous generation of ``index:*/*/Sources*``,
  ``index:*/*/Packages*``, and ``index:Release`` items in the collection:
  that is, the items with the latest ``created_at`` before ``generate_at``.
  If there are any such items, it marks them as having been removed at
  ``generate_at`` (setting ``removed_by_user``, ``removed_by_workflow``, and
  ``removed_at``).

* It searches for the next generation of ``index:*/*/Sources*``,
  ``index:*/*/Packages*``, and ``index:Release`` items in the collection:
  that is, the items with the earliest ``created_at`` after ``generate_at``.
  If there are any such items, it uses any one of them for information on
  when the indexes it is about to generate stopped being valid.

* It searches for packages that were in the suite at the given time, builds
  the appropriate index files from them (setting ``Date`` in the ``Release``
  file to the same timestamp as ``generate_at``), and adds them to the
  collection.  It sets the ``created_at`` fields of the new collection items
  to ``generate_at``.  If it found a next generation of index files in the
  previous phase, then it marks the collection items from this generation as
  being removed at the creation time of the next generation.

.. todo::

    Debian uses `Extra-Source-Only: yes <https://bugs.debian.org/814156>`__
    to indicate that a source package is only present in an index due to
    being referenced by a binary package in the suite (via ``Built-Using``
    or ``Source``).  Debusine has all the necessary information about which
    source and binary packages are in the suite and how they relate to each
    other, so it can add this field when generating ``Sources`` files.  (We
    may find that checking the relationships efficiently requires some
    additional database indexes.)
