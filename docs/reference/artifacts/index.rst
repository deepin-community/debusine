.. _artifact-reference:

=========
Artifacts
=========

The categorization of artifacts does not enforce anything on the
structure of the associated files and key-value data. However, there must
be some consistency and rules to be able to a make a meaningful use of the
system.

The :ref:`available-artifacts` section documents the various categories that
we use to manage a Debian-based distribution. For each category, we explain:

* what associated files you can find
* what key-value data you can expect
* what relationships with other artifacts are likely to exist

Data key names are used in ``pydantic`` models, and must therefore be valid
Python identifiers.

See the :ref:`explanation <explanation-artifacts>` for an overview, and
below for technical details.

.. toctree::

    relationships
    specs
