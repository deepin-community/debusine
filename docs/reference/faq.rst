.. _faq:

=========================================
Frequently Asked Questions about Debusine
=========================================

Design choices
==============

See :ref:`design-goals` and :ref:`debusine-concepts` for a general design
introduction.

Why not...
----------

... GitLab CI?
    Debian would never replace a core part of its infrastructure with something
    developed by a single company relying on the open core model to sustain
    itself.

    GitLab CI, while relatively generic, is still dependent on an underlying
    git repository and not all packages are maintained in a git repository (yet)

... Another existing CI/CD system?
    Debian likes to be in control of its core infrastructure: the plan for
    Debusine is to be primarily controlled by Debian's community and needs, and
    to be extended to accommodate current and future Debian workflows, rather
    than have Debian accommodate the peculiarities of an existing system.
