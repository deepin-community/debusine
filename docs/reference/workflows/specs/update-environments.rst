.. _workflow-update-environments:

Workflow ``update_environments``
================================

This workflow schedules work requests to build :ref:`tarballs
<artifact-system-tarball>` and :ref:`images <artifact-system-image>`, and
adds them to a :ref:`debian:environments collection
<collection-environments>`.

* ``task_data``:

  * ``vendor`` (required): the name of the distribution vendor, used to look
    up the target ``debian:environments`` collection
  * ``targets`` (required): a list of dictionaries as follows:

    * ``codenames`` (required): the codename of an environment to build, or
      a list of such codenames
    * ``codename_aliases`` (optional): a mapping from build codenames to
      lists of other codenames; if given, add the output to the target
      collection under the aliases in addition to the build codenames.  For
      example, ``trixie: [testing]``
    * ``variants`` (optional): an identifier to use as the variant name when
      adding the resulting artifacts to the target collection, or a list of
      such identifiers; if not given, the default is not to set a variant
      name
    * ``backends`` (optional): the name of the Debusine backend to use when
      adding the resulting artifacts to the target collection, or a list of
      such names; if not given, the default is not to set a backend name
    * ``architectures`` (required): a list of architecture names of
      environments to build for this codename
    * ``mmdebstrap_template`` (optional): a template to use to construct
      data for the :ref:`task-mmdebstrap`
    * ``simplesystemimagebuild_template`` (optional): a template to use to
      construct data for the :ref:`task-simplesystemimagebuild`

For each codename in each target, the workflow creates a :ref:`group
<workflow-group>`.  Then, for each architecture in that target, it fills in
whichever of ``mmdebstrap_template`` and ``simplesystemimagebuild_template``
that are present and uses them to construct child work requests.  In each
one, ``bootstrap_options.architecture`` is set to the target architecture,
and ``bootstrap_repositories[].suite`` is set to the codename if it is not
already set.

The workflow adds one event reaction to each child work request as follows
for each combination of the codename (including any matching entries from
``codename_aliases``), variant (``variants``, or ``[null]`` if
missing/empty), and backend (``backends``, or ``[null]`` if missing/empty).
``{vendor}`` is the ``vendor`` from the workflow's task data, and
``{category}`` is ``debian:system-tarball`` for ``mmdebstrap`` tasks and
``debian:system-image`` for ``simplesystemimagebuild`` tasks:

.. code-block:: yaml

  on_success:
    - action: "update-collection-with-artifacts"
      artifact_filters:
        category: "{category}"
      collection: "{vendor}@debian:environments"
      variables:
        - codename: {codename}
        - variant: {variant}  # omit if null
        - backend: {backend}  # omit if null
