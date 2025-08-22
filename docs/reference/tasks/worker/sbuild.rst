.. task:: Sbuild

Sbuild task
-----------

Regarding inputs, the ``sbuild`` task is compatible with the ontology
defined for :task:`PackageBuild` even though it implements only a subset of
the possible options at this time.

Currently unsupported :task:`PackageBuild` task keys:

* ``build_architecture``
* ``build_options``
* ``build_path``

Output artifacts and relationships:

a. :artifact:`debian:package-build-log`: sbuild output

   * relates-to: ``source_artifact``
   * relates-to: ``b``, ``c``

b. :artifact:`debian:binary-package`: one for each binary package
   (``*.deb``) built from the source package

   * relates-to: ``source_artifact``

c. :artifact:`debian:binary-packages`: the binary packages (``*.deb``) built
   from the source package, grouped into a single artifact

   * relates-to: ``source_artifact``

d. :artifact:`debian:upload`: ``c`` plus the right administrative files
   (``.changes``, ``.buildinfo``) necessary for its binary upload

   * extends: ``b``, ``c``
   * relates-to: ``b``, ``c``

e. :artifact:`debusine:signing-input`: the ``.changes`` file ready for
   signing if required

   * relates-to: ``d``

f. :artifact:`debusine:work-request-debug-logs`: debusine-specific worker
   logs

   * relates-to: ``source_artifact``


The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.sbuild::Sbuild.build_dynamic_data
