.. _dput-ng-profiles:

=======================
dput-ng profile options
=======================

A minimal `dput-ng <https://dput.readthedocs.io/en/latest/>`__ profile for
uploading to a Debusine server is as follows:

.. code-block:: json

    {
        "allow_unsigned_uploads": true,
        "debusine_scope": "debian",
        "debusine_workflow_data": {
            "source_artifact": "@UPLOAD@"
        },
        "debusine_workspace": "developers",
        "fqdn": "debusine.debian.net",
        "incoming": "/",
        "meta": "debusine",
        "method": "debusine"
    }

``incoming`` must be set, but its value is ignored.  In addition, either
``debusine_workflow`` or ``debusine_workflows_by_distribution`` must be set.

Supported options:

* ``fqdn``: the host name of the Debusine server
* ``debusine_scope``: the :ref:`scope <explanation-scopes>` to upload to
* ``debusine_workspace``: the :ref:`workspace <explanation-workspaces>` to
  upload to
* ``debusine_workflow``: the name of the :ref:`workflow
  <explanation-workflows>` template to start (configured by the workspace
  owners, and normally an instance of :workflow:`debian_pipeline`); optional
  if ``debusine_workflows_by_distribution`` is set)
* ``debusine_workflows_by_distribution``: a mapping from target distribution
  names in the ``.changes`` file to workflow template names, as in
  ``debusine_workflow``; optional if ``debusine_workflow`` is set
* ``debusine_workflow_data``: run-time parameters to provide to the
  workflow; the string ``"@UPLOAD@"`` will be replaced with the artifact ID
  of your upload

If you are uploading to ``debusine.debian.net``, then these options are set
to reasonable values by default.  In some cases you may need to override
``debusine_workspace`` and/or ``debusine_workflow``.

Once `#983160 <https://bugs.debian.org/983160>`__ is fixed, you will be able
to set these options on the ``dput`` command line.  Until then, you will
need to create a configuration file such as
``~/.dput.d/profiles/debusine.debian.net.json`` instead; see the `dput-ng
configuration file documentation
<https://dput.readthedocs.io/en/latest/reference/configs.html>`__ for
details.
