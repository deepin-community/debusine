.. _debusine-cli:

====================
The debusine command
====================

The ``debusine`` command is a command line interface to the Debusine API.
It is provided by the ``debusine-client`` package and contains many
sub-commands.

The configuration file is documented in :ref:`debusine-cli-config`.

Output of the ``debusine`` command
----------------------------------
If the ``debusine`` command succeeds, it prints relevant information to the
standard output in YAML format.

If an error occurs, the error messages will be printed to the standard error.

Return values
-------------

Return values of the ``debusine`` command:

===============  ==================================================================================
  Return value    Meaning
===============  ==================================================================================
 0                Success
 1                Error: unhandled exception. Please report the error
 2                Error: wrong arguments and options
 3                Error: any other type of error such as connection to the server is not possible,

                  invalid configuration file, etc.
===============  ==================================================================================

Sub-commands
------------

``debusine`` provides sub-commands to manipulate
:ref:`explanation-work-requests`, :ref:`explanation-workflows`,
:ref:`explanation-artifacts`, etc., described in ``--help``:

.. code-block:: console

    $ debusine --help
    usage: debusine [-h] [--server SERVER] [--config-file CONFIG_FILE] [-s] [-d]
                    {list-work-requests,show-work-request,create-work-request,manage-work-request,create-workflow-template,[...]}
                    ...
    
    Interacts with a Debusine server.
    
    positional arguments:
      {list-work-requests,show-work-request,create-work-request,manage-work-request,create-workflow-template,[...]}
                            Sub command
        list-work-requests  List all work requests
        show-work-request   Print the status of a work request
        create-work-request
                            Create a work request and schedule the execution. Work request is read from stdin in YAML format
        manage-work-request
                            Manage a work request
        create-workflow-template
                            Create a workflow template
	[...]

If you have multiple servers configured, then you may need to select which
one to use.  You can do this using ``--server FQDN/SCOPE`` (for example,
``--server debusine.debian.net/debian``), or using ``--server NAME`` (where
the available names are shown by ``debusine setup``).

Each sub-command is self-documented, use ``debusine sub-command
--help``:

.. code-block:: console

    $ debusine create-workflow --help
    usage: debusine create-workflow [-h] [--workspace WORKSPACE] [--data DATA] template_name
    
    positional arguments:
    [...]
