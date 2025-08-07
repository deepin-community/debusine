.. _workflow-package-upload:

Workflow ``package_upload``
===========================

This workflow signs and uploads source and/or binary packages to an upload
queue.  It is normally expected to be used as a sub-workflow.

* ``task_data``:

  * ``source_artifact`` (:ref:`lookup-single`, optional): a
    ``debian:source-package`` or ``debian:upload`` artifact representing the
    source package (the former is used when the workflow is started based on
    a ``.dsc`` rather than a ``.changes``)
  * ``binary_artifacts`` (:ref:`lookup-multiple`, optional): a list of
    ``debian:upload`` artifacts representing the binary packages
  * ``merge_uploads`` (boolean, defaults to False): if True, merge the
    uploads and create a single ``PackageUpload`` task to upload them all
    together; if False, create a separate ``PackageUpload`` task for each
    upload
  * ``since_version`` (string, optional): passed to
    :ref:`task-make-source-package-upload` if ``source_artifact`` is a
    ``debian:source-package``
  * ``target_distribution`` (string, optional): passed to
    :ref:`task-make-source-package-upload` if ``source_artifact`` is a
    ``debian:source-package``
  * ``key`` (string, optional): the fingerprint to sign the upload with,
    which must have purpose ``openpgp``
  * ``require_signature`` (boolean, defaults to True): whether the upload
    must be signed
  * ``target`` (required): the upload queue, as an ``ftp://`` or ``sftp://``
    URL
  * ``delayed_days`` (integer, optional): the number of days to delay this
    upload; this assumes that the upload queue implements Debian's
    convention of uploading delayed uploads to a ``DELAYED/{n}-day`` queue
  * ``vendor`` (string, optional): the distribution vendor to use for running
    :ref:`task-make-source-package-upload` and :ref:`task-merge-uploads`
  * ``codename`` (string, optional): the distribution codename to use for
    running :ref:`task-make-source-package-upload` and :ref:`task-merge-uploads`

At least one of ``source_artifact`` and ``binary_artifacts`` must be set.

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.package_upload::PackageUploadWorkflow.build_dynamic_data

The workflow creates the following tasks, each of which has a dependency on
the previous one in sequence, using event reactions to store output in the
workflow's internal collection for use by later tasks:

* if ``source_artifact`` is a ``debian:source-package`` artifact: a
  :ref:`task-make-source-package-upload` (with ``since_version`` and
  ``target_distribution``) to build a corresponding ``.changes`` file
  Uses ``vendor`` and ``codename`` to construct the environment lookup.
* if ``merge_uploads`` is True and there is more than one source and/or
  binary artifact: a :ref:`task-merge-uploads` to combine them into a single
  upload. Uses ``vendor`` and ``codename`` to construct the environment lookup.
* for each upload (or for the single merged upload, if merging):

  * if ``key`` is provided: a :ref:`task-debsign` to have Debusine sign the
    upload with the given key
  * if ``key`` is not provided and ``require_signature`` is True: an
    :ref:`task-external-debsign` to wait until a user provides a signature,
    which Debusine will then include with the upload
  * a :ref:`task-package-upload`, to upload the result to the given upload
    queue
