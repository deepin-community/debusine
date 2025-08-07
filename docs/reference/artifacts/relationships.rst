.. _artifact-relationships:

Relationships
=============

Artifacts may have the following relations with each others:

* *built-using*: indicates that the build of the artifact used the target
  artifact (ex: "binary-packages" artifacts are built using
  "source-package" artifacts)
* *extends*: indicates that the artifact is extending the target artifact
  in some way (ex: a "source-upload" artifact extends a "source-package"
  artifact with target distribution information)
* *relates-to*: indicates that the artifact relates to another one in
  some way (ex: a "binary-upload" artifact relates-to a "binary-package",
  or a "package-build-log" artifact relates to a "binary-package").
