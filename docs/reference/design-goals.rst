.. _design-goals:

============
Design goals
============

A few things to keep in mind as you work on Debusine's design.

Cloud friendly
==============

The Debian archive is huge, any task processing the Debian archive
is going to take a lot of computer power and can generate huge
amounts of data.

To cope with this, the system must be able to scale by leveraging
the cloud, both to have additional workers and to store the generated
artifacts.

At the same time, using a cloud service should not be required,
one should be able to supply "static" workers. Debian will likely
want dedicated trusted workers for the build of official packages.

Vendor neutral
==============

Debusine is built for Debian but it should be usable by Debian derivatives
and anyone who wants to build Debian packages. It should be easy to
configure and be available as an official Debian package.

Easy to extend
==============

New workflows and new ideas will continue to pop up in the Debian
community and it should be easy to extend Debusine to implement and
try out those new workflows.

Permissions
===========

Among the expected workflows, there will be the security team that will
want to run checks on embargoed updates, so the system should have some
way to work on private data and respect that privacy.

Uploads are also restricted to some limited group of users, and many of
the workflows will want to restrict some actions, so the system must have
some authentication and authorization mechanism.
