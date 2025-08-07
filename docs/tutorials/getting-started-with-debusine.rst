.. _tutorial-getting-started:

=============================
Getting started with Debusine
=============================

In this tutorial you will discover two of Debusine's building blocks:
:ref:`workflows <explanation-workflows>` and :ref:`artifacts
<explanation-artifacts>`.  Workflows let you schedule the execution of
different kind of :ref:`tasks <explanation-tasks>` on remote workers, while
artifacts represent a set of files managed by Debusine.

For the purpose of this tutorial, we will use Debusine's command line
interface to upload a source package, build it, and run various tests on it.

Prerequisites
-------------

You need to have access to a working Debusine instance:

* If you are a Debian developer, you can use `debusine.debian.net
  <https://debusine.debian.net>`_. You can login there with your
  salsa.debian.org account, and it will automatically create
  your Debusine account.
* Otherwise, please follow the instructions from
  :ref:`tutorial-install-debusine`. Once completed, the Debusine instance
  will be available under the ``debusine.internal`` hostname and this is
  the name that we will continue to use here.

Install the client and configure it
-----------------------------------

You should first install the ``debusine-client`` package (if needed
configure APT with one of the :ref:`supported package repositories
<debusine-package-repositories>`):

.. code-block:: console

   $ sudo apt install debusine-client

Now it's time to create yourself a token that the client will use to connect to
the server.  With ``debusine-client`` version 0.10.0 or newer, you can run
``debusine setup`` and follow the prompts.  With older versions, see
:ref:`create-api-token`.

More information about the Debusine command line interface is available
in :ref:`debusine-cli`.

Set up workflow templates
-------------------------

If you're using a managed instance such as debusine.debian.net, you can skip
this step.  Otherwise, follow the instructions in
:ref:`tutorial-set-up-workflow-templates` to prepare your Debusine instance for
running workflows.

Check workspace configuration
-----------------------------

Debusine organizes most objects into :ref:`workspaces
<explanation-workspaces>`, and you need to work within a workspace where you
have at least the "contributor" role.

If you're using your own instance, then the default workspace (``System``) is
fine.  Otherwise, you will need to add appropriate ``--workspace`` options to
the commands below.  For example, on debusine.debian.net, Debian developers may
use ``--workspace developers`` (see https://wiki.debian.org/DebusineDebianNet
for more details).

Create an artifact by uploading a source package
------------------------------------------------

The low-level ``debusine create-artifact`` command can be used to create any
arbitrary artifact, but when it comes to Debian source packages (``.dsc``)
or Debian uploads (``.changes``), Debusine offers a more convenient
interface with ``debusine import-debian-artifact [FILE|URL]``. You can refer
to a local file or to a remote URL.

For instance, on your client, you can create and upload an artifact for the
"hello" source package with:

.. code-block:: console

    $ debusine import-debian-artifact http://deb.debian.org/debian/pool/main/h/hello/hello_2.10-3.dsc
    [...]
    message: New artifact created in http://debusine.internal/api in workspace System with id 536.
    artifact_id: 536

Or, if you're using debusine.debian.net:

.. code-block:: console

    $ debusine import-debian-artifact --workspace developers \
        http://deb.debian.org/debian/pool/main/h/hello/hello_2.10-3.dsc

Artifacts can be provided as input to many different Debusine workflows and
tasks, using their artifact ID: take note of it.

Create a workflow to build and test your package
------------------------------------------------

Creating a :ref:`workflow <explanation-workflows>` asks the Debusine server to
do some work for you, possibly involving many smaller tasks.  Debusine can run
many different :ref:`workflows <workflow-reference>` with many different
underlying :ref:`tasks <task-reference>`.

It is up to each workspace owner to define which workflows their workspace
supports, using templates.  You can find out what workflow templates are
currently available by looking at the workspace in the web interface (e.g.
``http://debusine.internal/debusine/System/`` or
https://debusine.debian.net/debian/developers/).

Once you know what workflow template you want to use, you can start a
workflow.  The ``debusine create-workflow`` command takes key-value
parameters for each workflow as YAML data on standard input.  Try this on
your client, taking care to refer to the ID of the artifact that we created
in the previous step (``536`` for the source package in this example):

.. code-block:: console

    $ debusine create-workflow debian-qa-unshare <<END
    source_artifact: 536
    codename: trixie
    END

Or, if you're using debusine.debian.net:

.. code-block:: console

    $ debusine create-workflow --workspace developers upload-to-unstable <<END
    source_artifact: 536
    END

This outputs some YAML structured information:

.. code-block:: yaml

    result: success
    message: Workflow created on http://debusine.internal/api with id 315.
    workflow_id: 315

At this point, the task has not been executed yet, but it has been accepted
and will be processed as soon as workers become available. You can follow
the status of the workflow through the web interface (click on *Workflows*
in the top menu to find it).

If the ``enable_upload`` parameter is true (as in the ``upload-to-unstable``
workflow on debusine.debian.net), then you will need to provide a signature
for the package if you're happy with its QA results.  Select the "Wait for
signature" work request in your workflow, and run the ``debusine
provide-signature`` command it shows you.  After that, Debusine will upload
the package to Debian for you.

You can explore the :ref:`available debian_pipeline parameters
<workflow-debian-pipeline>` and try passing them to ``debusine
create-workflow`` to run your workflow in different ways.

Examine work requests and artifacts
-----------------------------------

You can explore the various work requests that are part of a workflow,
either in the web interface or using ``debusine show-work-request``.  Work
requests typically have some input artifacts and some output artifacts.  For
example, an ``sbuild`` task will produce :ref:`debian:upload
<artifact-upload>`, :ref:`debian:binary-package <artifact-binary-package>`,
and :ref:`debian:package-build-log <artifact-package-build-log>` artifacts.
You will also get a :ref:`debusine:work-request-debug-logs
<artifact-work-request-debug-logs>` artifact containing various files
generated by Debusine to help troubleshoot issues with the task.

The generated artifacts can be browsed and downloaded from the web
interface, or using the Debusine client:

.. code-block:: console

    $ debusine download-artifact 538
    Downloading artifact and uncompressing into /home/debian
    hello_2.10-3_amd64.deb

As well as the contained files, the artifact category also defines the
structure of the metadata that is associated with the artifact.  You can
inspect those metadata and the file listing on the web interface or on the
command line with ``debusine show-artifact ARTIFACT_ID`` (here ``540`` is
the artifact ID of a ``debian:upload`` artifact):

.. code-block:: console

    $ debusine show-artifact 540
    id: 540
    workspace: System
    category: debian:upload
    created_at: '2024-01-24T17:05:04.975882+00:00'
    data:
      type: dpkg
      changes_fields:
        Date: Mon, 26 Dec 2022 16:30:00 +0100
    […]
    download_tar_gz_url: http://debusine.internal/artifact/539/?archive=tar.gz
    files_to_upload: []
    expire_at: null
    files:
      hello_2.10-3_amd64.buildinfo:
        size: 5511
        checksums:
          sha256: 422aef340c827d2ed2b38c353f660b70e754509bc0ddb0952975090d9f25caaa
        type: file
        url: http://debusine.internal/artifact/539/hello_2.10-3_amd64.buildinfo
      hello_2.10-3_amd64.changes:
        size: 1889
        checksums:
          sha256: d5d694b42b94587d38a5f883fe1fc5d44368ffe974ac3d506d55bcbef0ab0767
        type: file
        url: http://debusine.internal/artifact/539/hello_2.10-3_amd64.changes
      hello_2.10-3_amd64.deb:
        size: 53084
        checksums:
          sha256: 069754b87d7a546253554813252dacbd7a53e959845cc9f6e8f4c1c8fe3746c5
        type: file
        url: http://debusine.internal/artifact/539/hello_2.10-3_amd64.deb
      hello-dbgsym_2.10-3_amd64.deb:
        size: 35096
        checksums:
          sha256: 1550fcd93105a3cf8fddfc776fda0fbebb51dd7c2d2286eeabc43cb37896ad1e
        type: file
        url: http://debusine.internal/artifact/539/hello-dbgsym_2.10-3_amd64.deb
