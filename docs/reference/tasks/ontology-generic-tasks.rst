Ontology for generic tasks
==========================

While tasks are unique in theory, we can have different tasks sharing
some commonalities. In the Debian context in particular, we have different
ways to build Debian packages with different helper programs (sbuild,
pbuilder, etc.) and we want those tasks to reuse the same set of
parameters so that they can be called interchangeably.

This public interface is materialized by a generic task that can be
scheduled by the users and that will run one of the available
implementations that can run on one of the available workers.

This section documents those generic tasks and their interface.

There are some ``task_data`` keys that apply to all tasks:

* ``notifications`` (optional): a dictionary containing:

  * ``on_failure`` (required): a specification of what to do if the task
    fails, formatted as an array of dictionaries as follows:

    * ``channel`` (required): the ``NotificationChannel`` to use for this
      notification
    * ``data`` (optional): a dictionary as follows (for email channels; this
      may change for other notification methods):

      * ``from`` (optional): the email address to send this notification
	from (defaults to the channel's ``from`` property)
      * ``to`` (optional): a list of email addresses to send this
	notification to (defaults to the channel's ``to`` property)
      * ``cc`` (optional): a list of email addresses to CC this notification
	to (defaults to the channel's ``cc`` property, if any)
      * ``subject`` (optional): the subject line for this notification
	(defaults to the channel's ``subject`` property, or failing that to
	``WorkRequest $work_request_id completed in $work_request_result``);
	the strings ``${work_request_id}`` and ``${work_request_result}``
	(or ``$work_request_id`` and ``$work_request_result``, provided that
	they are not followed by valid identifier characters) are replaced
	by their values

Task data key names are used in ``pydantic`` models, and must therefore be
:external+python:ref:`syntactically valid Python identifiers <identifiers>`
(although they may collide with keywords, in which case ``pydantic`` aliases
should be used).

Many tasks look up their execution environment from a
:collection:`debian:environments` collection.  These lookups have
``architecture``, ``format``, and ``backend`` filters automatically added to
them based on the task data's ``host_architecture`` and ``backend`` fields,
and they will automatically try ``variant={task_name}`` followed by
``variant=``, so it is normally only necessary to specify ``codename`` (e.g.
``debian/match:codename=bookworm``).

.. task:: PackageBuild

Task ``PackageBuild``
---------------------

A generic task to represent a package build, i.e. the act of transforming
a source package (.dsc) into binary packages (.deb).

The ``task_data`` associated to this task can contain the following keys:

* ``input`` (required): a dictionary describing the input data

  * ``source_artifact`` (:ref:`lookup-single`, required): source artifact
    pointing to a source package, used to retrieve the source package to
    build.

  * ``extra_binary_artifacts``: (:ref:`lookup-multiple`, optional). List of
    artifacts.  If provided these binary package artifacts
    (:artifact:`debian:binary-package`, :artifact:`debian:binary-packages`,
    or :artifact:`debian:upload`) are downloaded and made available to apt
    when installing build-dependencies.

* ``environment`` (:ref:`lookup-single` with default category
  :collection:`debian:environments`, required):
  :artifact:`debian:system-tarball` or :artifact:`debian:system-image`
  artifact, depending on the backend type. ``QEMU`` and ``INCUS_VM`` require
  a :artifact:`debian:system-image` artifact, while the other backends
  require a :artifact:`debian:system-tarball`.
* ``backend`` (optional, defaults to ``unshare``):
  If ``auto``, the task uses the default.
  Supported backends: ``incus-lxc``, ``incus-vm``, ``qemu``, and
  ``unshare``.
* ``extra_repositories`` (optional): a list of extra repositories to enable.
  Each repository is described by a dictionary with the following
  possible keys:

  * ``url`` (required): the base URL of the repository
  * ``suite`` (required): the name of the suite in the repository; if
    this ends with ``/``, then this is a `flat repository
    <https://wiki.debian.org/DebianRepository/Format#Flat_Repository_Format>`_
    and ``components`` must be omitted
  * ``components`` (optional): components to enable
  * ``signing_key`` (optional): ASCII-armored public key used to authenticate
    this suite

* ``host_architecture`` (required): the architecture that we want to build
  for, it defines the architecture of the resulting architecture-specific
  .deb (if any)
* ``build_architecture`` (optional, defaults to the host architecture):
  the architecture on which we want to build the package (implies
  cross-compilation if different from the host architecture). Can be
  explicitly set to the undefined value (Python's ``None`` or JavaScript's
  ``null``) if we want to allow cross-compilation with any build architecture.
* ``build_components`` (optional, defaults to ``any``): list that can contain
  the following 3 words (cf ``dpkg-buildpackage --build=any,all,source``):

  * ``any``: enables build of architecture-specific .deb
  * ``all``: enables build of architecture-independent .deb
  * ``source``: enables build of the source package (.dsc)
* ``build_profiles``: list of build profiles to enable during package build (cf
  ``dpkg-buildpackage --build-profiles``)

* ``build_options``: value of ``DEB_BUILD_OPTIONS`` during build
* ``build_path`` (optional, default unset): forces the build to happen
  through a path named according to the passed value. When this value
  is not set, there's no restriction on the name of the path.

* ``binnmu`` (optional, default unset): build a `binNMU
  <https://wiki.debian.org/binNMU>`_:

  * ``changelog``: one line of text for the Debian changelog entry
  * ``suffix``: suffix appended to the binary package version, e.g. ``+b1``
  * ``timestamp`` (optional, default is now): changelog date
  * ``maintainer`` (optional, default is uploader): changelog author

* ``build_dep_resolver`` (optional, default is ``apt``): Use the
  specified dependency resolver.
* ``aspcud_criteria`` (optional): Optimization criteria for the
  ``aspcud`` ``build_dep_resolver``.

.. task:: SystemBootstrap

Task ``SystemBootstrap``
------------------------

A generic task to represent the bootstrapping of a Debian system out
of an APT repository. The end result of such a task is to generate
a :artifact:`debian:system-tarball` artifact.

The ``task_data`` associated to this task can contain the following keys:

* ``bootstrap_options``: a dictionary with a few global options:

  * ``variant`` (optional): maps to the ``--variant`` command line option
    of debootstrap
  * ``extra_packages`` (optional): list of extra packages to include in
    the bootstrapped system
  * ``architecture`` (required): the native architecture of the built
    Debian system. The task will be scheduled on a system of that
    architecture.

* ``bootstrap_repositories``: a list of repositories used to bootstrap
  the Debian system. Note that not all implementations might support
  multiple repositories.

  * ``types`` (optional): a list of source types to enable among ``deb``
    (binary repository) and ``deb-src`` (source repository).
    Defaults to a list with ``deb`` only.
  * ``mirror`` (required): the base URL of a mirror containing APT
    repositories in ``$mirror/dists/$suite``
  * ``suite`` (required): name of the distribution's repository to
    use for the bootstrap
  * ``components`` (optional): list of components to use in the APT
    repository (e.g. ``main``, ``contrib``, ``non-free``, ...). Defaults
    to download the ``Release`` from the suite and using all the Components.
  * ``check_signature_with`` (optional, defaults to ``system``): indicates
    whether we want to check the repository signature with the system-wide
    keyrings (``system``), or with the external keyring documented in the
    in the ``keyring`` key (value ``external``), or whether we don't want
    to check it at all (value ``no-check``).
  * ``keyring_package`` (optional): install an extra keyring package in
    the bootstrapped system
  * ``keyring`` (optional): provide an external keyring for the bootstrap

    * ``url`` (required): URL of the external keyring to download (must
      either have a host or be a ``file://`` URL under
      ``/usr/share/keyrings/``)
    * ``sha256sum`` (optional): SHA256 checksum of the keyring to validate
      the downloaded file
    * ``install`` (boolean, defaults to False): if True, the downloaded
      keyring is installed and used in the target system.

* ``customization_script`` (optional): a script that is copied in the
  target chroot, executed from inside the chroot and then removed. It lets
  you perform arbitrary customizations to the generated system. You can
  use apt to install extra packages. If you want to use something more
  elaborated than a shell script, you need to make sure to install the
  appropriate interpreter during the bootstrap phase with the
  ``extra_packages`` key.

Some executor backends require specific packages to be installed in the
tarball/image:

* ``incus-lxc``: Requires:
  ``extra_packages: [dbus, systemd, systemd-resolved, systemd-sysv]``,
  as the image has to be bootable and configure networking with
  systemd-networkd.

.. task:: SystemImageBuild

Task ``SystemImageBuild``
-------------------------

This generic task is an extension of the :task:`SystemBootstrap` generic
task: it should generate a disk image artifact complying with the
:artifact:`debian:system-image` definition. That disk image contains a
Debian-based system matching the description provided by the SystemBootstrap
interface.

The following additional keys are supported:

* ``disk_image``

  * ``format`` (required): desired format for the disk image. Supported values are ``raw``
    and ``qcow2``.

  * ``filename`` (optional): base of the generated disk image filename.

  * ``kernel_package`` (optional): name of the kernel package to install,
    the default value is ``linux-image-generic``, which is only
    available on Bullseye and later, on some architectures.

  * ``bootloader`` (optional): name of the bootloader package to use,
    the default value is ``systemd-boot`` on architectures that support
    it.

  * ``partitions`` (required): a list of partitions, each represented by a
    dictionary with the following keys:

    * ``size`` (required): size of the partition in gigabytes
    * ``filesystem`` (required): filesystem used in the partition, can be
      ``none`` for no filesystem, ``swap`` for a swap partition, or
      ``freespace`` for free space that doesn't result in any partition
      (it will thus just offset the position of the following partitions).
    * ``mountpoint`` (optional, defaults to ``none``): mountpoint of the
      partition in the target system, can be ``none`` for a partition that
      doesn't get a mountpoint.

Some executor backends require specific packages to be installed in the
tarball/image or specific customization:

* ``incus-vm``: Requires: A kernel and bootloader, which the
  :task:`SimpleSystemImageBuild` task will install.
  Also: ``python3`` and
  ``customization_script: /usr/share/autopkgtest/setup-commands/setup-testbed``
  to support the ``autopkgtest-virt-incus`` driver used by ``sbuild``
  and ``autopkgtest``.
* ``qemu``: Requires: A kernel and bootloader, which the
  :task:`SimpleSystemImageBuild` task will install.
