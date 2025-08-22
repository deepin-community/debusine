.. artifact:: debian:upload

Category ``debian:upload``
==========================

This artifact represents an upload of source and/or binary packages.
Currently uploads are always represented with ``.changes`` file but the
structure of the artifact makes it possible to represent other kind of
uploads in the future (like uploads with signed git tags, or some
Debusine native internal upload).

Note that source uploads in artifacts of this category may lack
``.orig.tar.*`` files.  Workflows whose tasks need the actual contents of
the source package should normally locate the related
:artifact:`debian:source-package` artifact and use that instead.

* Data:

  * type: the type of the source upload

    * ``dpkg``: for an upload generated out of a ``.changes`` file created
      by ``dpkg-buildpackage``

  * changes_fields: a parsed version of the fields available in the
    ``.changes`` file

* Files:

  * a ``.changes`` file
  * All files mentioned in the ``.changes`` file

* Relationships:

  * extends: (optional) one :artifact:`debian:source-package`
  * extends: (optional) one or more :artifact:`debian:binary-package` and/or
    :artifact:`debian:binary-packages`
