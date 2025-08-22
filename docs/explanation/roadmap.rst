.. _roadmap:

=======
Roadmap
=======

Vision
======

Debusine’s goal is to be an integrated solution to build, distribute and
maintain a Debian-based distribution. But what does it entail more
precisely?

Here's a rough list of everything that we consider to be in scope:

* Core workflows for a distribution

  * Building source and binary Debian packages
  * Maintaining package repositories
  * Building all kinds of images
  * QA analysis of packages / repositories / images
  * Distribution of packages / images

* Maintenance workflows

  * Reviews and approvals of packages in a queue
  * Migration of packages from one repository to the other

* Other distribution-wide workflows

  * Distribution-wide experiments (ex: archive rebuilds with modified
    packages)
  * Data gathering workflows (ex: uscan, appstream data extraction, etc.)

* Integrations

  * With git-based development workflows on GitHub and GitLab 
    (including their respective CI infrastructure)
  * With Debian specific services (salsa, debbugs, etc.)
  * With cloud providers

This list might not be exhaustive, one needs to remember that any
project/experiment that processes an entire distribution and needs a
network of workers with a central scheduler, is likely to be in scope
for Debusine since that's the initial motivation that led Raphaël Hertzog
to start the project.

.. note::

    The idea for Debusine came up from working on `distro-tracker
    <https://salsa.debian.org/qa/distro-tracker>`_ where many services
    providing information to be displayed on tracker.debian.org were
    reinventing a custom infrastructure to process the whole Debian
    archive just to extract some specific information.

    Debusine is meant to be a central place for tasks processing
    the Debian archive.

Our plans
=========

Our priorities for 2025 are to:

* Make Debusine useful to the Debian community, building QA workflows that
  developers need.
* Fix bugs and improve features that are gaining traction within Debian.
* Develop the support of custom package repositories (aka PPA for Debian).
* Make it possible to prepare transitions in such package repositories.

If you want to dig deeper, you should know that we plan our work with
quarterly milestones and we thus reevaluate our priorities regularly.
See :ref:`project-management-practices`.

Your plans
==========

The above is the focus of the core developers paid by Freexian, but
any initiative that extends Debusine in one of the directions
outlined above is certainly welcome.

If you have specific needs, please `reach out to Freexian
<https://www.freexian.com/services/debusine/>`_ so that they can
offer you to develop the features that you need.
