.. workflow:: make_signed_source

Workflow ``make_signed_source``
===============================

This workflow produces a source package with signed contents from a
`template package
<https://wiki.debian.org/SecureBoot/Discussion#Source_template_inside_a_binary_package>`_
and some binary packages.

* ``task_data``:

  * ``binary_artifacts`` (:ref:`lookup-multiple`, required): the
    :artifact:`debian:binary-package` or :artifact:`debian:upload` artifacts
    representing the binary packages to sign
  * ``signing_template_artifacts`` (:ref:`lookup-multiple`, required): the
    :artifact:`debian:binary-package` artifacts representing the binary
    packages to sign

  * ``vendor`` (string, required): the distribution vendor on which to sign
  * ``codename`` (string, required): the distribution codename on which to
    sign
  * ``architectures`` (list of strings): the list of architectures that this
    workflow is running on, plus ``all``
  * ``purpose`` (string, required): the purpose of the key to sign with; see
    :task:`Sign`
  * ``key`` (string, required): the fingerprint to sign with; must match
    ``purpose``
  * ``sbuild_backend`` (string, optional): see :task:`PackageBuild`

The workflow computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.server.workflows.make_signed_source::MakeSignedSourceWorkflow.build_dynamic_data

Any of the lookups in ``binary_artifacts`` or ``signing_template_artifacts``
may result in :bare-data:`promises <debusine:promise>`, and in that case the
workflow adds corresponding dependencies.  Promises must include an
``architecture`` field in their data, and signing template promises must
also include a ``binary_package_name`` field.

The list of architectures to run on is the list of architectures that are
present in both ``binary_artifacts`` and ``signing_template_artifacts``,
intersected with ``architectures``.

For each architecture and for each artifact from
``signing_template_artifacts`` matching that architecture, the workflow
creates sub-workflows and tasks as follows, with substitutions based on its
own task data.  Each one has a dependency on the previous one in sequence,
using event reactions to store output in the workflow's internal collection
for use by later tasks:

* an :task:`ExtractForSigning` task, with task data:

  * ``input.template_artifact``: the subset of the lookup in this workflow's
    ``signing_template_artifacts`` for the concrete architecture in question
  * ``input.binary_artifacts``: the subset of the lookup in this workflow's
    ``binary_artifacts`` for each of ``all`` and the concrete architecture
    in question that exist
  * ``environment``: ``{vendor}/match:codename={codename}``

* a :task:`Sign` task, with task data:

  * ``purpose``: ``{purpose}``
  * ``unsigned``: the output of the previous task, from the workflow's
    internal collection
  * ``key``: ``{key}``

* an :task:`AssembleSignedSource` task, with task data:

  * ``environment``: ``{vendor}/match:codename={codename}``
  * ``template``: the subset of the lookup in this workflow's
    ``signing_template_artifacts`` for the concrete architecture in question
  * ``signed``: the output of the previous task, from the workflow's
    internal collection

* an :workflow:`sbuild` sub-workflow, with task data:

  * ``prefix``: ``signed-source-{architecture}-{signing_template_name}|``
  * ``input.source_artifact``: the output of the previous task, from the
    workflow's internal collection
  * ``input.extra_binary_artifacts``: the subset of the lookup in this
    workflow's ``binary_artifacts`` for each of ``all`` and the concrete
    architecture in question that exist
  * ``target_distribution``: ``{vendor}:{codename}``
  * ``backend``: ``{sbuild_backend}``
  * ``architectures``: if ``{architectures}`` is set, then
    ``{architectures}`` plus ``all``

The workflow adds event reactions that cause the :artifact:`debian:upload`
artifacts in the output for each architecture and signing template name to
be provided as ``signed-source-{architecture}-{signing_template_name}`` in
the workflow's internal collection.

.. todo::

    We may need to use different keys for different architectures.  For
    example, a UEFI signing key is only useful on architectures that use
    UEFI, and some architectures have other firmware signing arrangements.
