.. _task-external-debsign:

ExternalDebsign task
--------------------

This wait task blocks until a user provides a signature for an upload.

The ``task_data`` for this task may contain the following keys:

* ``unsigned`` (:ref:`lookup-single`, required): the ``debian:upload``
  artifact whose contents should be signed

The ``workflow_data`` for this task contains ``needs_input: True``.

Running this task does not do anything. In order to complete it,
the user needs to execute ``debusine provide-signature
<work-request-id>`` to download the files to sign, sign them locally
with ``debsign``, upload them back as part of a new ``debian:upload``
artifact, and finally record that artifact as an output artifact of
the ExternalDebsign task. The web UI advises the user to run this command
when showing such a work request.

The containing workflow should then normally use an event reaction
to add that output artifact to a suitable collection (usually the
workflow's internal collection).

The task does not verify the signature, since it doesn't necessarily
have the public key available.  It remains the responsibility of
whatever would normally verify the signature (e.g. an external upload
queue) to do so.

Used by the :ref:`package_upload workflow <workflow-package-upload>`.
