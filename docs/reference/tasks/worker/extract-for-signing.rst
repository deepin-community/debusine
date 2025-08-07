.. _task-extract-for-signing:

ExtractForSigning task
----------------------

This is a worker task that takes the output of the :ref:`Sbuild task
<task-sbuild>` and extracts :ref:`debusine:signing-input
<artifact-signing-input>` artifacts from them for use by the :ref:`Sign task
<task-sign>`.

The ``task_data`` for this task may contain the following keys:

* ``input`` (required): a dictionary describing the input data:

  * ``template_artifact`` (:ref:`lookup-single`, required): a
    ``debian:binary-package`` artifact containing a `template package
    <https://wiki.debian.org/SecureBoot/Discussion#Source_template_inside_a_binary_package>`_
  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): a list of
    ``debian:binary-package`` or ``debian:upload`` artifacts used to find
    the packages referred to by the template's ``files.json``

* ``environment`` (:ref:`lookup-single` with default category
  ``debian:environments``, required): ``debian:system-tarball`` artifact
  that will be used to unpack binary packages using the ``unshare`` backend

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.extract_for_signing::ExtractForSigning.build_dynamic_data

The task operates as follows:

* It finds the set of binary artifacts to operate on from
  ``binary_artifacts``.  It uses ``debian:binary-package`` artifacts
  directly; if it finds ``debian:upload`` artifacts, it follows ``extends``
  relationships from those to find individual ``debian:binary-package``
  artifacts, and uses those.
* It extracts the
  ``/usr/share/code-signing/$binary_package_name/files.json`` file from the
  template binary package.
* It checks that ``files.json`` uses only relative paths with no ``..``
  components.
* For each package in the template's ``files.json``:

  * It checks that the package name is a syntactically-valid Debian package
    name.
  * It finds the corresponding package among the binary artifacts.
  * If there is a ``trusted_certs`` entry, it copies it into the
    corresponding output artifact.
  * For each file:

    * It checks that the file name uses only relative paths with no ``..``
      components, and that the resulting path within the extracted binary
      package does not traverse symlinks to outside the extracted binary
      package.
    * It stores a copy of the file in the output artifact with the name
      ``$package/$file``.

The output will be provided as :ref:`debusine:signing-input
<artifact-signing-input>` artifacts, one for each package in the template's
``files.json``, with each artifact having a ``relates-to`` relationship to
the template package and to the binary package from which its files were
extracted.
