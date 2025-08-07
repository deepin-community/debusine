==================
Uploading packages
==================

Pipeline considerations
=======================

The :ref:`package_upload workflow <workflow-package-upload>` will typically
be used as a sub-workflow of a smart "pipeline" workflow.  There are three
main use cases:

* Upload a source package to Debusine, have it tested, and then have
  Debusine pass on that upload to an external upload queue.

  In this case, the ``package_upload`` workflow's task data should set
  ``source_artifact`` to the source package and leave ``binary_artifacts``
  empty.

* Upload a source package to Debusine, have it tested, and then have
  Debusine upload both the source and all built binaries to an external
  upload queue.  (For example, this is useful when uploading a package to
  Debian that will land in the NEW queue, since Debian currently requires
  binaries for NEW uploads.)

  In this case, the ``package_upload`` workflow's task data should set
  ``source_artifact`` to the source package, set ``binary_artifacts`` to a
  list of :ref:`single lookups <lookup-single>` matching each of the binary
  uploads from the super-workflow's internal collection (e.g.
  ``[internal@collections/name:build-all,
  internal@collections/name:build-amd64]``, and set ``merge_uploads`` to
  True.

* Debusine acts as a build daemon, building a source package for a number of
  architectures and uploading each of them as soon as the builds finish.

  In this case, the ``package_upload`` workflow's task data should leave
  ``source_artifact`` unset, set ``binary_artifacts`` to a list of
  :ref:`single lookups <lookup-single>` matching each of the binary uploads
  from the super-workflow's internal collection, and set ``merge_uploads``
  to False.

Generic code for creating child work requests using artifacts from the
workflow's internal collection adds appropriate dependencies on the work
requests that are expected to provide those artifacts.

If the parent workflow needs some kind of manual validation step to complete
before starting the upload (typically in the case of manual uploads but not
when acting as a build daemon), it should add a dependency from the
``package_upload`` sub-workflow to the validation workflow step.  The
``package_upload`` sub-workflow will be populated before validation is
complete (since the root workflow handles population of all its
sub-workflows), but it will not start running until validation is complete.
