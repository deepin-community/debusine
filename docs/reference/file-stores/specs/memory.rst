.. file-backend:: Memory

Memory file backend
===================

The ``Memory`` backend stores all files in memory; they do not persist
across server restarts.  As such, it is only suitable for testing, and must
not be used in production.

* Configuration:

  * ``name`` (string): The name of the store; indexes into in-memory
    storage.

* Supports returning local paths: no
* Supports returning URLs: no
