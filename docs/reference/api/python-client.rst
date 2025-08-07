=================
Python client API
=================

The Debusine class
==================

.. autoclass:: debusine.client.debusine.Debusine

Associated data models
======================

The parameters of the HTTP requests and the responses of the server are
represented by Pydantic models that are described below.

.. automodule:: debusine.client.models

Representation of artifacts
===========================

Artifacts to be uploaded are first created as a
:py:class:`debusine.artifacts.local_artifact.LocalArtifact` object. There
are many descendants of that class available in
:py:mod:`debusine.artifacts`, one for each category of artifact.

.. autoclass:: debusine.artifacts.local_artifact.LocalArtifact

.. automodule:: debusine.artifacts
   :imported-members:
