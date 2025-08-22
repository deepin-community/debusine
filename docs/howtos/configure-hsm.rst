.. _configure-hsm:

======================================
Configure an HSM for use with Debusine
======================================

After :ref:`setting up a signing worker <set-up-debusine-signing>`, you may
want to attach a hardware security module (HSM) to provide additional
protection for some keys.  Debusine supports any HSM that you can interact
with using `PKCS #11 <https://en.wikipedia.org/wiki/PKCS_11>`__, but we have
currently only tested it with `YubiHSM 2
<https://docs.yubico.com/hardware/yubihsm-2/hsm-2-user-guide/index.html>`__
devices.  The remainder of this document describes how to set up a YubiHSM 2
for use with Debusine; it may be possible to adapt the same ideas for other
devices.

Install packages needed to use the YubiHSM 2
============================================

These packages can be installed from ``bookworm-backports`` or newer
(including ``trixie``):

.. code-block:: console

   $ sudo apt install \
       libengine-pkcs11-openssl p11-kit-modules \
       yubihsm-connector yubihsm-pkcs11 yubihsm-shell

Decide on a key policy
======================

The YubiHSM 2 user guide has some advice on `initial provisioning and
deployment
<https://docs.yubico.com/hardware/yubihsm-2/hsm-2-user-guide/hsm2-initial-provision-deploy-guide.html>`__,
which you should consider.

You may wish to have a "management" authentication key with a strong
password held offline which you can use to change the key policy later, and
as an escape hatch in case you make a mistake.  If so, note that the default
authentication key shipped with the device has a weak password; either that
key must be removed or its password must be changed.

Debusine currently only supports using "static" PKCS #11 signing keys: that
is, keys that have been pre-generated on the HSM and that can then be
registered for use by Debusine.  (In future, we may support dynamic key
generation as well.)  Debusine-specific keys should be kept within their own
`domain
<https://docs.yubico.com/hardware/yubihsm-2/hsm-2-user-guide/hsm2-core-concepts.html#domain>`__,
to allow using the HSM for other purposes.

Debusine additionally needs an authentication key that it can use to connect
to the HSM and make signatures.  This should have a strong password, but the
password will need to be made available to ``debusine-signing.service``.
This key must have enough `capabilities
<https://docs.yubico.com/hardware/yubihsm-2/hsm-2-user-guide/hsm2-core-concepts.html#capability>`__
and `delegated capabilities
<https://docs.yubico.com/hardware/yubihsm-2/hsm-2-user-guide/hsm2-core-concepts.html#delegated-capabilities>`__
to allow making use of signing keys.  A useful set of capabilities is:

* ``delete-asymmetric-key``
* ``generate-asymmetric-key``
* ``put-asymmetric-key``
* ``sign-attestation-certificate``
* ``sign-ecdsa``
* ``sign-eddsa``
* ``sign-pkcs``
* ``sign-pss``
* ``export-wrapped``
* ``import-wrapped``

And a useful set of delegated capabilities is:

* ``sign-ecdsa``
* ``sign-eddsa``
* ``sign-pkcs``
* ``sign-pss``
* ``exportable-under-wrap``

For `backup
<https://docs.yubico.com/hardware/yubihsm-2/hsm-2-user-guide/hsm2-backup-restore.html>`__
purposes, it is wise to use "wrap keys" which allow exporting keys "under
wrap" in such a way that they can be imported into devices possessing the
same wrap key.  You should have a management wrap key that allows backing up
and restoring all exportable keys on the device.  There should also be a
wrap key that ``debusine-signing`` can use to export keys in its own domain:
this is part of our plan for supporting more keys than the HSM supports, so
it will eventually become a requirement.

Although the YubiHSM itself `supports passwords from 8 to 64 bytes long
<https://docs.yubico.com/hardware/yubihsm-2/hsm-2-user-guide/hsm2-pkcs11-guide.html#logging-in>`__,
in practice the userspace stack `currently imposes a maximum length of 27
bytes <https://github.com/OpenSC/libp11/issues/547>`__.

You can use ``yubihsm-shell`` to create the various keys involved.  The
``put authkey``, ``put wrapkey``, ``generate wrapkey``, and ``put
asymmetric`` commands are especially useful here.  You should keep a record
of the script you followed and the resulting key IDs for later reference.

For each key, you will need its corresponding certificate (public key).  If
you imported an externally-generated key into the HSM, then you should
already have its certificate.  Otherwise, you can use the ``attest
asymmetric`` command in ``yubihsm-shell`` to generate one.

Configure ``debusine-signing`` to use the HSM
=============================================

#. The ``yubihsm-connector`` package provides an HTTP daemon that passes
   through requests to the USB device.  Some tools need to be pointed to the
   connector's URL.  Create a ``/etc/yubihsm_pkcs11.conf`` file (the exact
   path is not important as long as ``debusine-signing`` can read it) with
   the following contents:

   .. code-block:: ini

      connector = http://127.0.0.1:12345

#. Create the ``/etc/systemd/system/debusine-signing.service.d`` directory
   and create
   ``/etc/systemd/system/debusine-signing.service.d/override.conf`` with the
   following contents, where the path is the same as that of the file you
   created in the previous step:

   .. code-block::

      [Service]
      Environment=YUBIHSM_PKCS11_CONF=/etc/yubihsm_pkcs11.conf

#. Assemble the PIN to be used by Debusine.  This consists of its
   authentication key ID as a four-character hexadecimal string, followed by
   the actual password: for example, key ID 5 with password ``secretpw``
   would become ``0005secretpw``.

#. Encrypt this PIN for use by systemd (this must be run as root).  Note the
   use of the ``systemd-ask-password -n`` option to ensure that the PIN does
   not end with a newline character:

   .. code-block:: console

      # systemd-ask-password -n | systemd-creds encrypt --name=yubihsm-pin -p - - \
          >>/etc/systemd/system/debusine-signing.service.d/override.conf

.. _register-uefi-hsm-key:

Register a UEFI signing key stored in the HSM
=============================================

#. Use ``YUBIHSM_PKCS11_CONF=/etc/yubihsm_pkcs11.conf p11-kit list-modules``
   to find the base PKCS #11 URI for the HSM.  This will be in the ``uri:``
   line under ``module: yubihsm_pkcs11``.  You may drop the ``manufacturer``
   and ``token`` fields; ``model`` and ``serial`` should be sufficient to
   uniquely identify the device.

#. Append
   ``;pin-source=/run/credentials/debusine-signing.service/yubihsm-pin`` so
   that ``debusine-signing`` uses the PIN for its authentication key.

#. Append ``;id=KEY-ID``, where ``KEY-ID`` is the ID of the relevant signing
   key as a four-character hexadecimal string.

#. Ensure that the ``/etc/debusine/signing/certificates`` directory exists
   and write the key's certificate to
   ``/etc/debusine/signing/certificates/KEY-NAME.crt`` (for some appropriate
   identifier ``KEY-NAME``),

#. Register the key with ``debusine-signing`` as follows, where ``KEY-URI``
   is the URI assembled in the previous steps:

   .. code-block:: console

      $ sudo -u debusine-signing debusine-signing register_pkcs11_static_key \
          uefi KEY-URI /etc/debusine/signing/certificates/KEY-NAME.crt \
          'some description of the new key'

#. Create a :asset:`debusine:signing-key` corresponding to the new key:

   .. code-block:: console

      $ debusine create-asset --workspace System --data - <<END
      purpose: uefi
      fingerprint: KEY-FINGERPRINT
      public_key: BASE64-ENCODED-CERTIFICATE
      END
