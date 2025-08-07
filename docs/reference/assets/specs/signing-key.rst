.. _asset-signing-key:

Category: ``debusine:signing-key``
==================================

This asset records the existence of a key in the signing service.

* Data:

  * ``purpose``: the purpose of this key: ``uefi`` or ``openpgp``
  * ``fingerprint``: the fingerprint of this key
  * ``public_key``: the base64-encoded public key
  * ``description``: an (optional) human readable description of the key

Only a single asset can exist for each fingerprint.
