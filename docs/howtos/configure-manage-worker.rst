.. _configure-manage-worker:

=============================
Configure and manage a worker
=============================

After having set up a new worker, you might want to configure its static metadata
with :ref:`debusine-admin worker edit_metadata <debusine-admin-worker-edit-metadata>`
to alter its behaviour in different ways, as presented in this page.

.. note::

    The examples below always configure only one single aspect and
    use a non-interactive method that overwrites all worker's metadata.
    In practice you will often combine different metadata: you can do that
    either interactively in the text editor that is spawned by
    ``debusine-admin worker edit_metadata`` or you need to provide a YAML file
    combining the different keys if you use the non-interactive approach
    shown in the examples.

Configure the accepted tasks
----------------------------

With an allow list
~~~~~~~~~~~~~~~~~~

You can restrict the list of tasks that are allowed to run on the worker
by providing a list of task names in the ``tasks_allowlist`` metadata:

.. code-block:: console

  $ cat > /tmp/metadata <<END
  tasks_allowlist:
  - sbuild
  - lintian
  END
  $ sudo -u debusine-server debusine-admin worker edit_metadata \
    debusine-internal --set /tmp/metadata

.. note::

   Task names are lowercased compared to the names shown in the
   :ref:`tasks reference list <task-reference>`.

With a deny list
~~~~~~~~~~~~~~~~

You can allow all tasks except a few exceptions by providing the list of
forbidden tasks in the ``tasks_denylist`` metadata.

.. code-block:: console

  $ cat > /tmp/metadata <<END
  tasks_denylist:
  - sbuild
  - simplesystemimagebuild
  END
  $ sudo -u debusine-server debusine-admin worker edit_metadata \
    debusine-internal --set /tmp/metadata

.. _howto-configure-worker-architectures:

Configure the list of compatible architectures
----------------------------------------------

For tasks that have architecture requirements, by default, a worker will
only run those which are corresponding to its host architecture. But if
the worker is compatible with other architectures, it is possible to
document the list of compatible architectures in its
``system:architectures`` metadata key.

.. code-block:: console

  $ cat > /tmp/metadata <<END
  system:architectures:
  - amd64
  - i386
  END
  $ sudo -u debusine-server debusine-admin worker edit_metadata \
    debusine-internal --set /tmp/metadata

Most typical installations will use the following lists:

* ``amd64`` workers will have ``system:architectures: ['amd64', 'i386']``
* ``arm64`` workers will have ``system:architectures: ['arm64', 'armhf', 'armel']``
