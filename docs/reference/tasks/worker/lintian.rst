.. _task-lintian:

Lintian task
------------

The ``task_data`` associated to this task can contain the following keys:

* ``input`` (required): a dictionary of values describing the input data,
  one of the sub-keys is required but both can be given at the same time
  too.

  * ``source_artifact`` (:ref:`lookup-single`, optional): the
    ``debian:source-package`` or ``debian:upload`` artifact representing the
    source package to be tested with lintian
  * ``binary_artifacts`` (:ref:`lookup-multiple`, optional): a list of
    ``debian:binary-package``, ``debian:binary-packages``, or
    ``debian:upload`` artifacts representing the binary packages to be
    tested with lintian (they are expected to be part of the same source
    package as the one identified with ``source_artifact``)

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.lintian::Lintian.build_dynamic_data

.. note::

   While it's possible to submit only a source or only a single binary
   artifact, you should aim to always submit source + arch-all + arch-any
   related artifacts to have the best test coverage as some tags can only
   be emitted when lintian has access to all of them at the same time.

* ``environment`` (:ref:`lookup-single` with default category
  ``debian:environments``, required): ``debian:system-tarball`` artifact
  that will be used to run lintian. Must have ``lintian`` installed.

* ``backend`` (optional): the virtualization backend to use, defaults to
  ``auto`` where the task is free to use the most suitable backend.
  Supported options: ``incus-lxc``, ``incus-vm``, ``unshare``.

* ``output`` (optional): a dictionary of values controlling some aspects
  of the generated artifacts

  * ``source_analysis`` (optional, defaults to True): indicates whether
    we want to generate the ``debian:lintian`` artifact for the source
    package

  * ``binary_all_analysis`` (optional, defaults to True): same as
    ``source_analysis`` but for the ``debian:lintian`` artifact related
    to ``Architecture: all`` packages

  * ``binary_any_analysis`` (optional, defaults to True): same as
    ``source_analysis`` but for the ``debian:lintian`` artifact related
    to ``Architecture: any`` packages

* ``target_distribution`` (optional): the fully qualified name of the
  distribution that will provide the lintian software to analyze the
  packages. Defaults to ``debian:unstable``.

* ``include_tags`` (optional): a list of the lintian tags that are allowed to
  be reported. If not provided (or empty), defaults to all. Translates into the
  ``--tags`` or ``--tags-from file`` command line option.

* ``exclude_tags`` (optional): a list of the lintian tags that are not
  allowed to be reported. If not provided (or empty), then no tags are
  hidden. Translates into the ``--suppress-tags`` or
  ``--suppress-tags-from file`` command line option.

* ``fail_on_severity`` (optional, defaults to ``none``): if the analysis emits
  tags of that severity or higher, then the task will return a "failure"
  instead of a "success". Valid values are (in decreasing severity)
  "error", "warning", "info", "pedantic", "experimental", "overridden".
  "none" is a special value indicating that we should never fail.

The lintian runs will always use the options ``--display-level
">=classification"`` (``>=pedantic`` in jessie) ``--no-cfg
--display-experimental --info --show-overrides`` to collect the full set of
data that lintian can provide.

.. note::

   Current lintian can generate "masked" tags (with `M:` prefix) when you
   use ``--show-overrides``. For the purpose of Debusine, we entirely
   ignore those tags on the basis that it's lintian's decision to hide
   them (and not the maintainer's decision) and as such, they don't bring
   any useful information. Lintian is full of exceptions to not emit some
   tags and the fact that some tags rely on a modular exception mechanism
   that can be diverted to generate masked tags is not useful to package
   maintainers.

   For those reasons, we suggested to lintian's maintainers to entirely
   stop emitting those tags in https://bugs.debian.org/1053892

Between 1 to 3 artifacts of category ``debian:lintian`` will be generated (one
for each source/binary package artifact submitted) and they will have a
"relates to" relationship with the corresponding artifact that has been
analyzed. The ``debian:lintian`` artifacts are described
:ref:`in the artifacts reference <artifact-lintian>`.
