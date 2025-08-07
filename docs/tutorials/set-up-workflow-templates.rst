.. _tutorial-set-up-workflow-templates:

=========================
Set up workflow templates
=========================

The best way to ask Debusine to do some work for you is to create
:ref:`explanation-workflows`.  In this tutorial, you will discover how to
set up workflow templates on your own instance.

Prerequisites
=============

This tutorial assumes that you installed :ref:`your own instance
<tutorial-install-debusine>` and that you have administrative access it via
:ref:`debusine-admin <debusine-admin-cli>`.  You can skip this tutorial if
you're using a managed instance such as debusine.debian.net, since in those
cases suitable workflow templates have already been created for you.

If you haven't done so already, you'll need to make yourself an owner of the
default workspace in order to be allowed to create workflow templates in it.

.. code-block:: console

    $ sudo -u debusine-server debusine-admin group create debusine/Admins
    $ sudo -u debusine-server debusine-admin workspace grant_role \
        debusine/System owner Admins
    $ sudo -u debusine-server debusine-admin group members debusine/Admins \
        --add YOUR-USER-NAME

You will also need to :ref:`set up the Debusine client
<set-up-debusine-client>`.

Create a build environment
==========================

Various :ref:`worker tasks <explanation-tasks>` require build environments,
which are created using the :ref:`update_environments
<workflow-update-environments>` workflow.

Start by creating a :ref:`debian:environments <collection-environments>`
collection on your server:

.. code-block:: console

    $ sudo -u debusine-server debusine-admin create_collection \
        debian debian:environments </dev/null

Then, on your client, create a suitable workflow template and start it
running:

.. code-block:: console

    $ debusine create-workflow-template \
        update-debian-environments update_environments <<END
    vendor: "debian"
    targets:
    - codenames: ["trixie"]
      architectures: ["amd64"]
      backends: ["unshare"]
      mmdebstrap_template:
        bootstrap_options:
          variant: "minbase"
        bootstrap_repositories:
        - mirror: "http://deb.debian.org/debian"
          components: ["main"]
    - codenames: ["trixie"]
      architectures: ["amd64"]
      variants: ["sbuild"]
      backends: ["unshare"]
      mmdebstrap_template:
        bootstrap_options:
          variant: "buildd"
        bootstrap_repositories:
        - mirror: "http://deb.debian.org/debian"
          components: ["main"]
    END

    $ debusine create-workflow update-debian-environments <<END
    {}
    END

Once this workflow finishes (which will take a few minutes), you should have
a ``debian:environments`` collection populated with some useful base
tarballs for ``trixie/amd64`` that can be used with the ``unshare`` backend:
a default variant containing only essential and required packages, and an
``sbuild`` variant that also contains build-essential packages.  These can
be :ref:`looked up by name <lookup-syntax>`.  If you wish, you can vary the
``targets`` dictionary to build different environments, or automate this
workflow to run regularly.

Set up the Debian pipeline
==========================

The :ref:`debian_pipeline <workflow-debian-pipeline>` workflow is a powerful
tool that coordinates all the steps typically involved in building and
testing an upload to Debian; it also has options to run tests on other
packages that depend on your package, and perform the upload for you at the
end.

On your client, create a suitable workflow template:

.. code-block:: console

    $ debusine create-workflow-template \
        debian-qa-unshare debian_pipeline <<END
    autopkgtest_backend: unshare
    lintian_backend: unshare
    piuparts_backend: unshare
    sbuild_backend: unshare
    upload_include_binaries: false
    upload_merge_uploads: false
    vendor: debian
    END

Any workflow parameters not set in your workflow template may be set when
you create the workflow.

You will then be able to run this workflow for a given source artifact, as
shown in :ref:`tutorial-getting-started`.
