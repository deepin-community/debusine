.. _howto-add-cloud-worker-pool:

Add a new Cloud Worker Pool
===========================

Introduction
------------

To support spikes in work requests, Debusine needs to be able to dynamically
make use of compute resources in clouds.  Static workers may still be
useful for various reasons, such as:

* locality
* confidentiality
* supporting an expected base load
* customers who are sensitive to making use of artifacts built in clouds
  they don't control
* support for exotic architectures
* in some cases, the ability to launch virtual machines in the worker

However, dynamic workers offer more flexibility and may be cheaper overall.

Initial Configuration
---------------------

#. Select a supported cloud provider (currently Amazon EC2 or Hetzner Cloud).
   This howto will use Hetzner Cloud, but the process is very similar
   for EC2.

#. Sign up for an account with the provider and `create an API key
   <https://docs.hetzner.com/cloud/api/getting-started/generating-api-token>`_.

#. Ensure that your Debusine server is visible to cloud instances within
   the cloud provider at its configured ``DEBUSINE_FQDN`` URL.
   If it's a private instance, not on the Internet, then you may need to
   run it within the cloud, or provide a tunnel from your private
   network in the cloud to the Debusine server.

#. Create a YAML file (``hetzner-account.yaml``) describing a
   :ref:`debusine:cloud-provider-account asset
   <asset-cloud-provider-account>`:

   .. code-block:: yaml

      provider_type: hetzner
      name: hetzner-acct
      configuration:
        region_name: eu-central
      credentials:
        api_token: LMg2iuGg47q6jzZ9aCE2ZchzOnRSeBQum5zAvhwaLi6GAKakJP

#. Create an asset containing this account data:

   .. code-block:: console

      $ debusine-admin asset create debusine:cloud-provider-account \
        --data hetzner-account.yaml

#. Create a YAML file (``specifications.yaml``) describing the worker
   pool.
   See :ref:`dynamic-worker-pools` for all the configuration details.

   .. code-block:: yaml

      provider_type: hetzner
      server_type: cx22
      image_name: debian-12
      ssh_keys:
        - my-ssh-key
      labels:
        role: debusine-worker
      debian_release: bookworm
      debusine_install_source: backports

#. Create a worker pool named ``hetzner-cloud``:

   .. code-block:: console

      $ debusine-admin worker_pool create --provider-account hetzner-acct \
        --architectures amd64 --specifications specifications.yaml --instance-wide \
        hetzner-cloud

#. Launch an instance manually to verify settings:

   .. code-block:: console

      $ debusine-admin worker_pool launch hetzner-cloud 1

#. A new worker ``hetzner-cloud-001`` should appear in the workers list,
   and after a couple of minutes should connect and show as "Idle".

#. If necessary, find the instance IP address in the Hetzner Cloud
   Console, and ``ssh`` into the instance to debug its startup.
   Instances are provisioned using `cloud-init
   <https://cloudinit.readthedocs.io/>`_.
