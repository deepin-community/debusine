.. _task-sbuild:

Sbuild task
-----------

Regarding inputs, the ``sbuild`` task is compatible with the ontology
defined for :ref:`package-build-task` even though it implements only
a subset of the possible options at this time.

Currently unsupported ``PackageBuild`` task keys:

* ``build_architecture``
* ``build_options``
* ``build_path``

Output artifacts and relationships:

a. ``debian:package-build-log``: sbuild output

   * relates-to: ``source_artifact``
   * relates-to: ``b``, ``c``

b. ``debian:binary-package``: one for each binary package (``*.deb``) built
   from the source package

   * relates-to: ``source_artifact``

c. ``debian:binary-packages``: the binary packages (``*.deb``) built
   from the source package, grouped into a single artifact

   * relates-to: ``source_artifact``

d. ``debian:upload``: ``c`` plus the right administrative files
   (``.changes``, ``.buildinfo``) necessary for its binary upload

   * extends: ``b``, ``c``
   * relates-to: ``b``, ``c``

e. ``debusine:signing-input``: the ``.changes`` file ready for signing if
   required

   * relates-to: ``d``

f. ``debusine:work-request-debug-logs``: debusine-specific worker logs

   * relates-to: ``source_artifact``


The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.sbuild::Sbuild.build_dynamic_data
