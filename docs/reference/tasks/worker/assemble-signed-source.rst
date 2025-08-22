.. task:: AssembleSignedSource

AssembleSignedSource task
-------------------------

This is a worker task that takes :artifact:`debusine:signing-output`
artifacts produced by :task:`Sign` tasks and assembles the resulting source
package.

The ``task_data`` for this task may contain the following keys:

* ``environment`` (:ref:`lookup-single` with default category
  :collection:`debian:environments`, required):
  :artifact:`debian:system-tarball` artifact that will be used to pack the
  source package using the ``unshare`` backend.  ``dpkg-dev`` will be
  installed there if necessary.
* ``template`` (:ref:`lookup-single`, required): a
  :artifact:`debian:binary-package` artifact containing a `source template
  <https://wiki.debian.org/SecureBoot/Discussion#Source_template_inside_a_binary_package>`_
* ``signed`` (:ref:`lookup-multiple`, required): signed
  :artifact:`debusine:signing-output` artifacts matching the template

The task operates as follows:

* It makes a copy of the
  ``/usr/share/code-signing/$binary_package_name/source-template/``
  directory from the template binary package.
* It checks that ``debian/source/format`` is exactly ``3.0 (native)`` and
  that neither ``debian/source/options`` nor ``debian/source/local-options``
  exists.
* It checks that ``files.json`` uses only relative paths with no ``..``
  components.
* For each package name and file name in the template's ``files.json``, it
  finds the corresponding file in the signed artifacts and copies it into
  ``debian/signatures/$package/$file.sig``.  For this to work, the names of
  the files in the :artifact:`debusine:signing-input` and
  :artifact:`debusine:signing-output` artifacts must be composed of the
  binary package name, followed by ``/``, followed by the path in the
  corresponding ``file`` key in ``files.json``.
* It packs the resulting assembled source package using ``dpkg-source -b``,
  and makes a suitable ``.changes`` file for it using ``dpkg-genchanges``.

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.assemble_signed_source::AssembleSignedSource.build_dynamic_data

The output will be provided as a :artifact:`debian:source-package` artifact,
with a ``built-using`` relationship to the :artifact:`debian:binary-package`
artifacts that were related to the :artifact:`input to the Sign task
<debusine:signing-input>`, and a :artifact:`debian:upload` artifact
containing that source package and the corresponding ``.changes`` file.
