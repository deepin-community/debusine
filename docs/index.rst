Debusine: a CI platform for Debian-based distributions
======================================================

Debusine's goal is to be an integrated solution to build, distribute and
maintain a Debian-based distribution.

Debusine is already a modern cloud-powered Continuous Integration (CI)
platform to run many packaging and distribution related workflows
for the Debian ecosystem. It can be used to automate everything from the
package build up to the generation of installer/disk/cloud/container
images, including all the intermediary quality assurance checks. It is
very versatile and easy to extend to cater to custom requirements.

To cope with the scale (dozens of thousands of packages), and with the
breadth of supported CPU architectures of a Linux distribution, Debusine
manages the scheduling and distribution of individual tasks to distributed
worker machines, including cloud workers on demand.

If you are new to Debusine, you will want to read
:ref:`introduction` first.

.. note::

    The documentation is structured by following the `Diátaxis
    <https://diataxis.fr/>`_ principles: tutorials and explanation
    are mainly useful to discover and learn, howtos and reference are
    more useful when you are familiar with Debusine already and you
    have some specific action to perform or goal to achieve.

.. toctree::
   :caption: Tutorials
   :maxdepth: 2

   tutorials/install-your-first-debusine-instance
   tutorials/getting-started-with-debusine
   tutorials/set-up-workflow-templates

.. toctree::
   :caption: Explanations
   :maxdepth: 2

   explanation/introduction
   explanation/roadmap
   explanation/concepts
   explanation/lookups
   explanation/work-request-scheduling
   explanation/expiration-of-data
   explanation/workflow-orchestration
   explanation/signing-service
   explanation/package-repositories
   explanation/talks

.. todo::

   Add new explanation pages to cover:

   * architecture (server, worker, client)

.. toctree::
   :caption: How-to guides
   :maxdepth: 2

   howtos/set-up-debusine-client
   howtos/create-an-api-token
   howtos/dput-ng
   howtos/manage-task-configuration-collection
   howtos/index-admin
   howtos/contribute
   howtos/contribute-workflow

.. toctree::
   :caption: Reference
   :maxdepth: 2

   reference/index-building-blocks
   reference/deployment/index
   reference/index-misc
   reference/index-cli
   reference/api/python-client
   reference/internals/index
   reference/index-contributors
   reference/devel-blueprints/index
   reference/release-history
   reference/sponsors

.. todo::

   Add new reference pages to cover:

   * debusine-server configuration file
   * debusine-worker configuration file

Indices and tables
==================

* :ref:`todo`
* :ref:`genindex`
* :ref:`modindex`

