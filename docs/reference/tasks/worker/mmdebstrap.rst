.. task:: MmDebstrap

MmDebstrap task
---------------

The ``mmdebstrap`` task fully implements the :task:`SystemBootstrap`
interface.

On top of the keys defined in that interface, it also supports the
following additional keys in ``task_data``:

* ``bootstrap_options``

  * ``use_signed_by`` (defaults to True): if set to False, then we
    do not pass the keyrings to APT via the ``Signed-By`` sources.list
    option, instead we rely on the ``--keyring`` command line parameter.

The task computes dynamic metadata as:

.. dynamic_data::
  :method: debusine.tasks.mmdebstrap::MmDebstrap.build_dynamic_data

The keys from ``bootstrap_options`` are mapped to command line options:

* ``variant`` maps to ``--variant`` (and it supports more values than
  debootstrap, see its manual page)
* ``extra_packages`` maps to ``--include``

The keys from ``bootstrap_repositories`` are used to build a sources.list
file that is then fed to ``mmdebstrap`` as input.
