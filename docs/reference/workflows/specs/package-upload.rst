.. workflow:: package_upload

Workflow ``package_upload``
===========================

This workflow signs and uploads source and/or binary packages to an upload
queue.  It is normally expected to be used as a sub-workflow.

* ``task_data``:

  * ``source_artifact`` (:ref:`lookup-single`, optional): a
    :artifact:`debian:source-package` or :artifact:`debian:upload` artifact
    representing the source package (the former is used when the workflow is
    started based on a ``.dsc`` rather than a ``.changes``)
  * ``binary_artifacts`` (:ref:`lookup-multiple`, optional): a list of
    :artifact:`debian:upload` artifacts representing the binary packages
  * ``merge_uploads`` (boolean, defaults to False): if True, merge the
    uploads and create a single ``PackageUpload`` task to upload them all
    together; if False, create a separate ``PackageUpload`` task for each
    upload
  * ``since_version`` (string, optional): passed to
    :task:`MakeSourcePackageUpload` if ``source_artifact`` is a
    :artifact:`debian:source-package`
  * ``target_distribution`` (string, optional): passed to
    :task:`MakeSourcePackageUpload` if ``source_artifact`` is a
    :artifact:`debian:source-package`
  * ``key`` (string, optional): the fingerprint to sign uploads with, which
    must have purpose ``openpgp``
  * ``binary_key`` (string, optional): the fingerprint to sign binary
    uploads with, which must have purpose ``openpgp``
  * ``require_signature`` (boolean, defaults to True): whether uploads must
    be signed
  * ``target`` (required): the upload queue, as an ``ftp://`` or ``sftp://``
    URL
  * ``delayed_days`` (integer, optional): the number of days to delay this
    upload; this assumes that the upload queue implements Debian's
    convention of uploading delayed uploads to a ``DELAYED/{n}-day`` queue
  * ``vendor`` (string, optional): the distribution vendor to use for running
    :task:`MakeSourcePackageUpload`
  * ``codename`` (string, optional): the distribution codename to use for
    running :task:`MakeSourcePackageUpload`
  * ``arch_all_host_architecture`` (string, defaults to ``amd64``): concrete
    architecture on which to run architecture-independent tasks (currently
    just :task:`MakeSourcePackageUpload`)

At least one of ``source_artifact`` and ``binary_artifacts`` must be set.

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.package_upload::PackageUploadWorkflow.build_dynamic_data

The workflow creates the following tasks, each of which has a dependency on
the previous one in sequence, using event reactions to store output in the
workflow's internal collection for use by later tasks:

* if ``source_artifact`` is a :artifact:`debian:source-package` artifact: a
  :task:`MakeSourcePackageUpload` task (with ``since_version`` and
  ``target_distribution``) to build a corresponding ``.changes`` file.
  Uses ``vendor`` and ``codename`` to construct the environment lookup.
* if ``merge_uploads`` is True and there is more than one source and/or
  binary artifact: a :task:`MergeUploads` task to combine them into a single
  upload.
* for each upload (or for the single merged upload, if merging):

  * if ``key`` is provided: a :task:`Debsign` task to have Debusine sign the
    upload with the given key
  * otherwise, if ``binary_key`` is provided and the upload contains
    binaries: a :task:`Debsign` task to have Debusine sign the upload with
    the given key; if there is a separate signing task for the source
    artifact, then this has a dependency on it
  * otherwise, if ``require_signature`` is True: an :task:`ExternalDebsign`
    task to wait until a user provides a signature, which Debusine will then
    include with the upload
  * finally, a :task:`PackageUpload` task, to upload the result to the given
    upload queue; if there is a separate upload task for the source
    artifact, then this has a dependency on it
