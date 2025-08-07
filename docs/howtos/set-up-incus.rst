.. _set-up-incus:

========================================
Set up Incus for Debusine Task Execution
========================================

The recommended executors for Debusine are based on `Incus
<https://linuxcontainers.org/incus/>`_. This will allow Debusine Tasks
to be executed in Incus LXC Containers or KVM VMs.

To use Incus for Task execution, an Incus install needs to be available
to the worker. The easiest way to do this is to install Incus on the
worker machine/VM itself.

Install Incus
=============

Incus is a recent fork from LXD and is not yet present in any stable
Debian releases.
You can find the package in Debian Testing (Trixie) and Debian Unstable,
and in bookworm-backports (backports for Debian 12).
You can also `use the upstream packages <https://github.com/zabbly/incus>`_.

You can install incus with:

.. code-block:: console

    $ sudo apt install incus

Initialize Incus
================

Incus needs some basic configuration. See `upstream docs
<https://linuxcontainers.org/incus/docs/main/howto/initialize/>`_ for
more details.

The Debusine repository includes a script
``bin/configure-worker-incus.sh`` that will automatically configure
Incus for a worker.
If you use this script, you can skip down to the :ref:`steps to enable
virtualization <enable_virtualization>`.

If you'd like to configure Incus manually, first give yourself
administrative powers in Incus:

.. code-block:: console

    $ sudo adduser $(whoami) incus-admin
    $ newgrp incus-admin

Then initialize Incus:

.. code-block:: console

    $ sudo incus admin init
    Would you like to use clustering? (yes/no) [default=no]:
    Do you want to configure a new storage pool? (yes/no) [default=yes]:
    Name of the new storage pool [default=default]:
    Would you like to create a new local network bridge? (yes/no) [default=yes]:
    What should the new bridge be called? [default=incusbr0]: debusinebr0
    What IPv4 address should be used? (CIDR subnet notation, “auto” or “none”) [default=auto]:
    What IPv6 address should be used? (CIDR subnet notation, “auto” or “none”) [default=auto]:

    We detected that you are running inside an unprivileged container.
    This means that unless you manually configured your host otherwise,
    you will not have enough uids and gids to allocate to your containers.

    Your container's own allocation can be reused to avoid the problem.
    Doing so makes your nested containers slightly less safe as they could
    in theory attack their parent container and gain more privileges than
    they otherwise would.

    Would you like to have your containers share their parent's allocation? (yes/no) [default=yes]: no
    Would you like the server to be available over the network? (yes/no) [default=no]:
    Would you like stale cached images to be updated automatically? (yes/no) [default=yes]:
    Would you like a YAML "init" preseed to be printed? (yes/no) [default=no]:

Create a Debusine network bridge
================================

Debusine task instances use a dedicated Network Bridge, to allow them to
firewalled appropriately. If the Incus install is only being used to run
Debusine Instances, this can be the default Incus network, as above.

If you have an existing Incus network, that you wish to keep, instead
you can create a new ``debusinebr0`` network for Debusine instances:

.. code-block:: console

    $ incus network create debusinebr0

Create a Debusine profile
=========================

Debusine task instances use a dedicated profile, assigning them to the
network bridge above.

The Debusine VM images use ``systemd-boot``, which `isn't signed for
Secure Boot <https://bugs.debian.org/1033725>`_.

Create a ``debusine`` profile:

.. code-block:: console

    $ incus profile create debusine
    Profile debusine created
    $ incus profile edit debusine
    $ incus profile set debusine raw.lxc=lxc.mount.auto=sys
    $ incus profile set debusine security.secureboot=false
    $ incus profile device add debusine host0 nic network=debusinebr0 name=host0
    $ incus profile device add debusine root disk pool=default path=/
    $ incus profile show debusine
    config:
      raw.lxc: lxc.mount.auto=sys
      security.secureboot: "false"
    description: ""
    devices:
      host0:
        name: host0
        network: debusinebr0
        type: nic
      root:
        path: /
        pool: default
        type: disk
    name: debusine
    used_by: []

Grant Debusine Worker access to Incus
=====================================

Grant Debusine Worker administrative powers in Incus:

.. code-block:: console

    $ sudo adduser debusine-worker incus-admin
    $ sudo systemctl restart debusine-worker

Verify Virtualization
=====================

If Incus is able to create containers and VMs, it should list both
``lxc`` and ``qemu`` in the drivers:

.. code-block:: console

    $ incus info | grep driver:
    driver: lxc | qemu

If it doesn't, you may need to :ref:`take steps to enable virtualisation
<enable_virtualization>`.

Test that you can launch containers and VMs:

.. code-block:: console

    $ incus launch images:debian/bookworm/amd64 test-container
    $ incus exec test-container ping 8.8.8.8
    $ incus delete --force test-container
    $ incus launch --vm images:debian/bookworm/amd64 test-vm
    $ # wait for the VM to come up ...
    $ incus exec test-vm ping 8.8.8.8
    $ incus delete --force test-vm

If an instance fails to launch, you can see the errors with:

.. code-block:: console

    $ incus info test-container --show-log

.. _enable_virtualization:

Enable Virtualization
=====================

If you are running your Debusine worker on bare-metal hardware, ensure
hardware virtualization is enabled in the BIOS/Firmware configuration.

If you are running your Debusine worker inside a VM, you'll need to
enable `Nested Virtualization on the host
<https://www.linux-kvm.org/page/Nested_Guests>`_, to be able to use KVM
within the VM.

Packages to install for Virtualization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To use VMs in Incus, you'll need to install some dependencies:

.. code-block:: console

   $ sudo apt install ovmf qemu-kvm udev

Nested Container Virtualization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you are running your Debusine worker inside an Incus/LXD container,
there are a few settings to allow nested containers and nested VMs to
function.

We enable ``security.nesting: true`` on the container to let Incus know
that we'll be running tested containers, and relax AppArmor
restrictions.

We pass through ``/dev/kvm``, ``/dev/vsock``, and ``/dev/vhost-vsock``
to the container, to allow it to run KVM VMs.

.. code-block:: console

   $ incus config set debusine security.nesting=true
   $ kvm_gid=$(incus exec debusine-worker getent group kvm | cut -d: -f 3)
   $ incus config device add debusine-worker kvm unix-char path=/dev/kvm gid=$kvm_gid
   $ incus config device add debusine-worker vsock unix-char path=/dev/vsock
   $ incus config device add debusine-worker vhost-vsock unix-char path=/dev/vhost-vsock
   $ incus restart debusine-worker
