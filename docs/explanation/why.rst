.. _why-this-project:

================
Why this project
================

A central place for tasks processing the Debian archive
=======================================================

While working on `distro-tracker
<https://salsa.debian.org/qa/distro-tracker>`_ I have been designing
a system to run regular tasks, mainly to download data and make them
available under some useful information for package maintainers. But
as the design became nicer, I have been tempted to merge real tasks
into distro-tracker, i.e. those what would process Debian packages and
generate the data that distro-tracker would consume.

This merge always seemed a good idea, it would reduce the number of
different services scanning the whole archive and it would make it easier
to share code between them. And it would make the services more easily
available to derivative distributions as distro-tracker is designed to be
easily shared across multiple vendors.

Though when I looked at all the possible use-cases, it became evident
that this would require some serious infrastructural changes: the tasks
must be offloaded to external workers to cope with the huge load that a
multitude of services would generate, assuming that you have many workers,
you will have some logic to distribute the jobs to the various workers
taking into account the requirements of each job (architecture,
disk/memory requirement, availability of some chroots, availability of a
local mirror, etc.). This was starting to become huge and would
move distro-tracker into some territory that is not in line with
its initial design: be a central place for sharing already-existing
information.

That's how the idea of Debusine came up. I wanted a place where we
would be able to centralize all the tasks that are processing the Debian
archive and store the generated data.

Including package building
==========================

But when you think of this, you realize that the first "task" that
is offloaded to many workers is the task of building binary packages
out of source packages. You know that the buildd/wanna-build
infrastructure is aging and that the idea of PPA in Debian has been
stalled precisely for this reason and the lack of persons willing to
hack on it. Then you remember of all the archive rebuilds that have
been done on Amazon AWS without using buildd/wanna-build and you decide
that Debusine ought to be a replacement for wanna-build and the buildd
network, and that it will be cloud-friendly so that it's easy
to do archive rebuild with some special tweaks.

So now Debusine must accept "uploads" of source packages, but we know
that FTP uploads of GPG signed files are a thing of the past, we want
to accept file-uploads via HTTP but also we want to support alternate
ways to get a source package: think fetching them from a signed tag
in a git repository.

With custom workflows
=====================

At this point, we already have a lot of work ahead of us. But it's not
finished yet. We want to improve on what we already have, blindly
processing uploads and integrating them in a repository is not
satisfactory: we want to be able to have various set of rules. A few
examples:

* we want to be able to wait until all architectures have been built and
  upload all binary packages at once
* we want to run many automated checks on the source and binary packages
  and accept/reject based on the results
* we want to have humans review the output of all the automated checks
  and have them approve the upload to the target repository

So we really need some higher-level logic to implement many different
workflows.

And GitLab integration
======================

And obviously, with all those nifty checks, developers will want to have
them run earlier, before any upload, so it should be possible to hook
all this with GitLab merge request. As soon as someone creates a merge
request, GitLab would trigger Debusine which would run the checks and make
the results available back in the merge request.

You might think that this last feature duplicates `salsa-ci
<https://salsa.debian.org/salsa-ci-team/pipeline>`_ and you are right
to some point. salsa-ci is a nice project (to which I'm contributing) but
it has inherent limitations:

* the use of docker imposes limitations (would you run qemu inside
  docker to run autopkgtest for tests requiring isolation-machine?)
* the structure of the jobs makes it non-trivial to run the tests on
  multiple releases
* it's centered around a single package:

  * it's hard to run tests on many packages at once
  * it's not well suited to compare the package with the previous
    version already available in the target distribution

* the user interface is limited (a yes/no it works, and look at the
  build log to figure out the problem)

Conclusion
==========

I hope that this explanation convinced you of the need for Debusine.
If not, at least you know where I come from and where I want to go.

-- Raphaël Hertzog
