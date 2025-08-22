.. _dynamic-worker-pools:

====================
Dynamic Worker Pools
====================

Worker Pools represent a dynamic number of practically identical cloud
instances, created and destroyed based on demand.
Individual worker instances are reused for replacement cloud instances.

There may be multiple pools for a single cloud provider, using different
accounts; for example, there may be pools on the same provider for
different scopes with different billing arrangements.

WorkerPools are configured via the ``WorkerPool`` database model, and
configured via the ``worker_pool`` management command. They include at
least the following fields (all JSON objects are modelled using
Pydantic):

* ``name`` (string): the name of the pool

* ``provider_account`` (foreign key to ``Asset``): a
  :asset:`debusine:cloud-provider-account` asset with details of the
  provider account to use for this pool

* ``enabled`` (boolean, defaults to True): if True, this pool is available
  for creating instances

* ``architectures`` (array of strings): the task architectures supported by
  workers in this pool

* ``tags`` (array of strings): the :issue:`worker tags <326>` supported by
  workers in this pool (note that worker tags are not fully implemented yet)

* ``specifications`` (JSON): public information indicating the type of
  instances to create, in a provider-dependent format (see below)

* ``instance_wide`` (boolean, defaults to True): if True, this pool may be
  used by any scope on this Debusine instance; if False, it may only be used
  by a single scope (i.e. there is a unique constraint on
  ``Scope``/``WorkerPool`` relations where ``WorkerPool.instance_wide`` is
  False)

* ``ephemeral`` (boolean, defaults to False): if True, configure the worker
  to shut down and require reprovisioning after running a single work
  request

* ``limits`` (JSON): instance limits, as follows:

  * ``max_active_instances`` (integer, optional): the maximum number of
    active instances in this pool
  * ``target_max_seconds_per_month`` (integer, optional): the maximum number
    of instance-seconds that should be used in this pool per month (this is
    a target maximum rather than a hard maximum, as Debusine does not
    destroy instances that are running a task; it may be None if there is no
    need to impose such a limit)
  * ``max_idle_seconds`` (integer, defaults to 3600): destroy instances that
    have been idle for this long

.. note::

    Public cloud providers typically have billing cycles corresponding to
    calendar months, which are not all the same length.  Debusine keeps
    track of instance run-times for each month in the Gregorian calendar in
    an attempt to approximate this.  It will not always produce an exactly
    accurate prediction of run-time for the purpose of provider charges,
    since providers vary in terms of how they account for things like
    instances that run for less than an hour.

    It is the administrator's responsibility to calculate appropriate limits
    based on the provider's advertised pricing for the configured instance
    type.

.. note::

    To avoid wasting resources, Debusine does not destroy instances that are
    actively running a task; this may cause it to overrun
    ``target_max_seconds_per_month``.  As a result, to keep resource usage
    under control even if some tasks take a very long time, administrators
    should normally also set ``max_active_instances``, and should
    independently set up billing alerts with their cloud providers.

Scope-level controls
--------------------

``WorkerPool`` models can be associated with scopes via the
``ScopeWorkerPool`` model. This also includes additional scope-level
limits.

* ``priority`` (integer): The priority of this worker pool for the purpose
  of scheduling work requests and creating dynamic workers to handle load
  spikes; pools with a higher priority will be selected in preference to
  pools with a lower priority.  Workers that do not have a pool implicitly
  have a higher priority than any workers that have a pool.

* ``limits`` (JSON): scope-level limits, as follows:

  * ``target_max_seconds_per_month`` (integer, optional): the maximum number
    of instance-seconds that should be used by work requests in this scope
    per month (this is a target maximum rather than a hard maximum, as
    Debusine does not destroy instances that are running a task; it may be
    None if there is no need to impose such a limit; note that idle worker
    time is not accounted to any scope)
  * ``target_latency_seconds`` (integer, optional): a target for the number
    of seconds before the last pending work request in the relevant subset
    of the work queue is dispatched to a worker, which the provisioning
    service uses as a best-effort hint when scaling dynamic workers

Worker Pool Workers
===================

As Worker Pool Workers can be very short-lived, Worker Pools reuse the
same Workers for multiple dynamic instances.

The ``Worker.instance_created_at`` field is set for dynamic workers when
their instance is created.
This is similar to ``Worker.registered_at``, but that field indicates
when the ``Worker`` row was created and remains constant across multiple
create/destroy cycles, while ``Worker.instance_created_at`` can be used
to determine the current runtime of an instance by subtracting it from
the current time.

Per-provider data (such as the instance ID) are stored in
``Worker.worker_pool_data``.

Instances are provisioned using `cloud-init
<https://cloudinit.readthedocs.io/>`_.

Amazon EC2 Specifications
=========================

.. highlight:: yaml

Amazon EC2 Worker Pool Specifications are structured like this::

    provider_type: aws
    launch_templates:
      - ImageId: ami-07d102fc0a7d12711
        # One of InstanceRequirements OR InstanceType must be specified
        InstanceType: m7a.medium
        InstanceRequirements:
          VCpuCount:
            Min: 2
            Max: 4  # optional
          MemoryMiB:
            Min: 4096
            Max: 32768  # optional

          # optional parameters:
          MemoryGiBPerVCpu:
            Min: 2
            Max: 8  # optional
          SpotMaxPricePercentageOverLowestPrice: 20
          MaxSpotPriceAsPercentageOfOptimalOnDemandPrice: 50
          BurstablePerformance: excluded

        # optional parameters:
        EbsOptimized: true
        KeyName: my-ssh-key
        NetworkInterfaces:
          # all parameters are optional
          - DeviceIndex: 0
            AssociatePublicIpAddress: true
            DeleteOnTermination: true
            Ipv6AddressCount: 1
            SubnetId: subnet-abc123
            Groups:
              - sg-abc123

        root_device_size: 30
        swap_size: 8
        tags:
          role: debusine-worker

    # optional parameters:
    instance_market_type: spot
    max_spot_price_per_hour: 0.2
    debian_release: bookworm
    debusine_install_source: backports

EC2 Specification Fields
------------------------

Many of the fields map closely to parameters to the EC2 `CreateFleet
<https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateFleet.html>`_
and `CreateLaunchTemplate
<https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateLaunchTemplate.html>`_
API calls:

* ``launch_templates`` (list of objects):
  Definitions of launch templates to use when spawning an instance.
  It may be useful to use multiple templates to allow instances to be
  launched in one of several availability zones, while still specifying
  resources that are specific to availability zones.
  It also enables configuring options that differ by instance type.
  See :ref:`ec2-launch-template-fields`.

* ``instance_market_type``
  (string, optional one of ``spot`` (default) or ``on-demand``):
  Market to purchase the instance in. Either spot or on demand pricing.

* ``max_spot_price_per_hour`` (float, optional, defaults to no limit):
  Maximum price to pay (in USD) per hour of instance runtime.
  Only applicable to spot-priced instances.

* ``debian_release`` (string, optional):
  Codename of the Debian release that the image contains. Used to apply
  appropriate quirks to instance initialization.

* ``debusine_install_source`` (string, optional, one of ``release``
  (default), ``backports``, ``daily-builds``, ``pre-installed``):
  How to install Debusine on this instance.
  It can be installed from the ``release`` or ``backports`` suites in
  the Debian archive, or from the ``daily-builds`` repository.
  If ``pre-installed``, no installation action is taken.

.. _ec2-launch-template-fields:

EC2 Launch Template fields
--------------------------

Exactly one of ``InstanceType`` and ``InstanceRequirements`` must be
specified.

* ``ImageId`` (string):
  The EC2 Image ID. Official Debian Cloud images can be found `here
  <https://wiki.debian.org/Cloud/AmazonEC2Image>`_.
  To optimize worker startup time, you can use a image with
  ``debusine-worker`` pre-installed.

* ``InstanceType`` (string, optional):
  To always use a specific `instance type
  <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-types.html>`_,
  specify it here.
  Alternatively, specify ``InstanceRequirements``.

* ``InstanceRequirements`` (object, optional):
  To let EC2 select the cheapest available instance type that meets your
  requirements, specify them here.
  Alternatively, explicitly specify an ``InstanceType``.
  See :ref:`ec2-instance-requirements-fields`.

* ``EbsOptimized`` (boolean, optional, default ``False``):
  Whether to enable Amazon EBS I/O optimization.
  This isn't available for all instance types, and costs extra when it
  is.

* ``KeyName`` (string, optional): SSH key pair name for admin access.

* ``NetworkInterfaces`` (list of objects, optional):
  Configure instance network interfaces.
  See :ref:`ec2-network-interface-fields`.

* ``root_device_size`` (integer, optional):
  Resize the root device to this size, in GiB, on creation. cloud-init
  will resize the root filesystem to fill the volume.

* ``swap_size`` (integer, optional):
  Add an EBS swap device of this size, in GiB, on creation.

* ``tags``: (object, optional):
  Tags to add to spawned instances.
  Keys are tag names, values are the tag values.

.. _ec2-instance-requirements-fields:

EC2 Instance Requirement fields
-------------------------------

Only one of ``SpotMaxPricePercentageOverLowestPrice`` and
``MaxSpotPriceAsPercentageOfOptimalOnDemandPrice`` can be specified,
neither is required.

* ``VCpuCount`` (object): Number of vCPUs. See :ref:`ec2-min-max-fields`.

* ``MemoryMiB`` (object): RAM size in MiB. See :ref:`ec2-min-max-fields`.

* ``MemoryGiBPerVCpu`` (object, optional): RAM per vCPU in GiB.
  See :ref:`ec2-min-max-fields`.

* ``SpotMaxPricePercentageOverLowestPrice`` (integer, optional, percentage):
  Configure a maximum spot price limit, at this percentage above the
  lowest identified spot price, matching the instance requirements.
  Exclusive with ``SpotMaxPricePercentageOverLowestPrice``.

* ``MaxSpotPriceAsPercentageOfOptimalOnDemandPrice``
  (integer, optional, percentage):
  Configure a maximum spot price limit, at this percentage of the
  on-demand price of the lowest identified spot price, matching the
  instance requirements.
  Exclusive with ``SpotMaxPricePercentageOverLowestPrice``.

* ``BurstablePerformance``
  (string, one of: ``included``, ``excluded`` (default), and ``required``):
  Whether burstable-performance instance types (e.g. ``T``) are included
  in the pool for consideration.

.. _ec2-min-max-fields:

EC2 (Min, Max) Requirement fields
---------------------------------

* ``Min`` (integer): Minimum value.

* ``Max`` (integer, optional, default unrestricted): Maximum value.

.. _ec2-network-interface-fields:

EC2 Network Interface fields
----------------------------

* ``DeviceIndex`` (integer, optional, default ``0``):
  Index of the network interface.

* ``AssociatePublicIpAddress``
  (boolean, optional, default is the subnet default):
  Associate a Public IPv4 address with the instance. This costs extra.
  Without a Public IPv4 address, a NAT Gateway must be running to be
  able to reach the IPv4 Internet.

* ``AssociatePublicIpAddress`` (boolean, default ``True``):
  Whether to delete the Network Interface on Instance termination.

* ``Ipv6AddressCount`` (integer, optional, default is the subnet default):
  Associate this number of Public IPv6 addresses with the instance.

* ``SubnetId`` (string, optional, default is the default subnet in this
  availability zone):
  Connect the Network Interface to this VPC Subnet.

* ``Groups`` (list of strings, optional, default is the default security group):
  Security Groups IDs of Security Groups to associate with the Network
  Interface.

Hetzner Cloud Specifications
============================

Hetzner Cloud Worker Pool Specifications are structured like this::

    provider_type: hetzner
    server_type: cx22
    image_name: debian-12

    # optional parameters:
    ssh_keys:
     - my-ssh-key
    networks:
     - network-1
    location: nbg1
    labels:
      role: debusine-worker
    enable_ipv4: True
    enable_ipv6: True
    debian_release: bookworm
    debusine_install_source: backports

Hetzner Cloud Specification Fields
----------------------------------

Many of the fields map closely to parameters to the Hetzner Cloud
`server creation <https://docs.hetzner.cloud/reference/cloud#servers-create-a-server>`_
API call:

* ``server_type`` (string):
  Name of the server type to launch.
  `Available options <https://www.hetzner.com/cloud/#pricing>`_.

* ``image_name`` (string):
  The Hetzner Cloud image ID.
  To optimize worker startup time, you can use an image with
  ``debusine-worker`` pre-installed.

* ``ssh_keys`` (list of strings, optional):
  SSH Key names for admin access.

* ``networks`` (list of strings, optional):
  Private Network names to attach the instances to.

* ``location`` (string, optional):
  Code name for the location to launch instances in.

* ``labels``: (object, optional):
  Labels to add to spawned instances.
  Keys are tag names, values are the tag values.

* ``enable_ipv4`` (boolean, optional, default is ``True``):
  Assign a public IPv4 address to the instance. This costs extra.

* ``enable_ipv6`` (boolean, optional, default is ``True``):
  Assign a public IPv6 address to the instance.

* ``debian_release`` (string, optional):
  Codename of the Debian release that the image contains. Used to apply
  appropriate quirks to instance initialization.

* ``debusine_install_source`` (string, optional, one of ``release``
  (default), ``backports``, ``daily-builds``, ``pre-installed``):
  How to install Debusine on this instance.
  It can be installed from the ``release`` or ``backports`` suites in
  the Debian archive, or from the ``daily-builds`` repository.
  If ``pre-installed``, no installation action is taken.
