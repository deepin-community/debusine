.. _introduction:

========================
Introduction to Debusine
========================

What is Debusine
----------------

At its core, Debusine manages scheduling and distribution of
Debian-related tasks to distributed worker machines. Those tasks are
usually tied together in various workflows.

Workflows can have many different purposes, but Debusine's primary
workflow is one built around a package update: it takes a new source
package, builds binary packages, launches a battery of QA checks,
signs the packages, makes them available in an APT repository (or uploads
them to an external repository).

Debusine is being developed by Freexian to help support the work
of Debian developers, by giving them access to a range of pre-configured
tools and workflows running on remote hardware.

We want to make it as easy as possible for Debian contributors to use all the
QA tools that Debian provides. We want to build the next generation of Debian’s
build infrastructure, one that will continue to reliably do what it already
does, but that will also enable distribution-wide experiments, custom package
repositories and custom workflows with advanced package reviews.

Debusine for Debian development
-------------------------------

With debusine.debian.net_ Debian developers have access to workflows to
prepare the updates that they want to upload to Debian. Each of their
updates will be built on multiple architectures (with sbuild_) and the
resulting binaries will go through various QA tools that have been hooked
into Debusine (including autopkgtest_ for the package itself and for its
reverse dependencies, lintian_ and piuparts_).

More information on the dedicated wiki page:
https://wiki.debian.org/DebusineDebianNet

.. _debusine.debian.net: https://debusine.debian.net
.. _sbuild: https://wiki.debian.org/sbuild
.. _autopkgtest: https://wiki.debian.org/ContinuousIntegration/autopkgtest
.. _lintian: https://wiki.debian.org/Lintian
.. _piuparts: https://wiki.debian.org/piuparts

Next generation Debian infrastructure
-------------------------------------

Debusine has been developed to fulfill the same requirements as the
corresponding infrastructure in use in Debian, making it viable as a
future replacement for wanna-build and buildd, and possibly more.

The end goal is to provide a more integrated experience to Debian
developers while enabling many new features that are entirely out of
reach with the current infrastructure.

Debusine will support creating pipelines with build, QA, approval and
signature steps that are triggered by package uploads. Pipelines will
understand the distinction between official Debian workers and external
workers, and only schedule builds targeting official distribution on
official workers.

Run distribution-wide QA experiments
------------------------------------

By providing a standard orchestration layer and low-level package build
and QA tasks, Debusine makes it much easier to do large-scale
distribution-wide tasks. One can build workflows for test rebuilds or
transition preparation, without needing to recreate the common
infrastructure for scheduling and executing tasks.

The distributed design of Debusine and its native cloud integration means
that any Debian developer can schedule archive-wide QA experiments without
disturbing the day-to-day work.

Debian-friendly people and companies will be able to sponsor external
computing time, while trusted builds will remain under Debian's control.

Learn more
----------

* :ref:`roadmap`
* :ref:`faq`
* :ref:`tutorial-getting-started`
* :ref:`contribute-to-debusine`
