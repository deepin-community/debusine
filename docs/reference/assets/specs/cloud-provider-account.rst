.. _asset-cloud-provider-account:

Category: ``debusine:cloud-provider-account``
=============================================

This asset stores details of a cloud provider account to be used by this
Debusine instance.

The details of the data in this asset are subject to change until at least
two providers have been implemented.

* Data:

  * ``provider_type`` (string): an item from an enumeration of supported
    providers
  * ``name`` (string): the name of the provider account
  * ``configuration`` (dictionary): non-secret provider-dependent
    information needed to manage instances (e.g. region name, entry point
    URLs)
  * ``credentials`` (dictionary): secret provider-dependent credentials
    needed to manage instances

For ``provider_type: aws``:

  * ``configuration``:

    * ``region_name`` (string, optional): name of AWS region (e.g.
      ``eu-west-1``); recommended for AWS S3, but may be left unset for
      other S3-compatible providers
    * ``ec2_endpoint_url`` (string, optional): EC2 endpoint URL (e.g.
      ``https://ec2.eu-west-2.amazonaws.com/``)
    * ``s3_endpoint_url`` (string, optional): S3 endpoint URL (e.g.
      ``https://s3.eu-west-2.amazonaws.com/`` or
      ``https://hel1.your-objectstorage.com/``); this may be set for non-AWS
      S3-compatible providers, or may be used to work around `bucket
      propagation delays on AWS
      <https://repost.aws/knowledge-center/s3-http-307-response>`__

  * ``credentials`` (see `Manage access keys for IAM users
    <https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html>`__
    in the AWS documentation):

    * ``access_key_id``: 20-character string
    * ``secret_access_key``: 40-character string

For ``provider_type: hetzner``:

  * ``configuration``:

    * ``region_name`` (string, optional): name of Hetzner Cloud region

  * ``credentials`` (see `Generating an API Token
    <https://docs.hetzner.com/cloud/api/getting-started/generating-api-token>`_
    in the Hetzner Cloud documentation):

    * ``api_token``: (string): The API token.

Only a single asset can exist for a given account name.

At present, only instance administrators and the relevant Debusine backend
code can create, modify, or access this category of asset: ``can_display``
should always return False, so that it can only be displayed in contexts
that disable permission checks.  In future, this may be opened up to scope
administrators for non-instance-wide provider accounts.

``provider_type: aws`` is also known to work with other providers that
implement AWS-compatible APIs, such as `Hetzner Object Storage
<https://docs.hetzner.com/storage/object-storage/>`__.
