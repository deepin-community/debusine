.. _debusine-signing-cli:

============================
The debusine-signing command
============================

The ``debusine-signing`` command starts and manages a :ref:`signing worker
<explanation-workers>`.  It is provided by the ``debusine-signing`` package.
It is the usual `django-admin
<https://docs.djangoproject.com/en/4.2/ref/django-admin/>`_ command of a
Django project, but Debusine adds some `custom management commands
<https://docs.djangoproject.com/en/4.2/howto/custom-management-commands/>`_
that are documented on this page.

See also:

  * :ref:`set-up-debusine-signing`

Command output
--------------

If a command is successful: nothing is printed and the return code is 0.

Running the signing worker
--------------------------

``signing_worker``
~~~~~~~~~~~~~~~~~~

``debusine-signing signing_worker`` starts the worker process itself, and is
normally run automatically through a systemd unit.  It normally doesn't
produce any output directly, but appends status information to its log
files.  Its return values are:

===============  ==================================================================================
  Return value    Meaning
===============  ==================================================================================
 0                Success
 1                Error: unhandled exception. Please report the error
 2                Error: wrong arguments and options
 3                Error: any other type of error such as non-writable log file,

                  invalid configuration file, etc.
===============  ==================================================================================

Managing keys
-------------

``generate_service_key``
~~~~~~~~~~~~~~~~~~~~~~~~

Generate a private key for the service.  This key is used to encrypt other
private keys, when storing them in software rather than in a hardware
security module.

.. code-block:: console

   $ sudo -u debusine-signing \
       debusine-signing generate_service_key /etc/debusine/signing/0.key

``register_pkcs11_static_key``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Register a key held in a hardware security module using `PKCS #11
<https://en.wikipedia.org/wiki/PKCS_11>`__.  See
:ref:`register-uefi-hsm-key` for an example of working out the appropriate
URI.

.. code-block:: console

   $ sudo -u debusine-signing \
       debusine-signing register_pkcs11_static_key \
       uefi \
       'pkcs11:model=YubiHSM;serial=12345678;pin-source=/run/credentials/debusine-signing.service/yubihsm-pin;id=1234' \
       /etc/debusine/signing/certificates/some-key.crt \
       'some description of the new key'
