.. _artifact-source-package:

Category ``debian:source-package``
==================================

This artifact represents a set of files that can be extracted in some
way to provide a file hierarchy containing source code that can be built
into ``debian:binary-package`` artifact(s).

* Data:

  * name: the name of the source package
  * version: the version of the source package
  * type: the type of the source package

    * ``dpkg`` for a source package that can be extracted with ``dpkg-source -x`` on the ``.dsc`` file

  * dsc_fields: a parsed version of the fields available in the .dsc file

* Files: for the ``dpkg`` type, a ``.dsc`` file and all the files
  referenced in that file
* Relationships:

  * built-using: in the case of a source package that was :ref:`assembled
    automatically <task-assemble-signed-source>` after signing files, the
    :ref:`debian:binary-package <artifact-binary-package>` artifacts that
    contain the corresponding unsigned files
