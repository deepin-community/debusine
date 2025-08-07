.. _design-practices:

================
Design practices
================

Before starting to code a new feature, it is important to see how
it can be implemented and whether it will require structural changes
to Debusine. We group all those activities under the generic "design"
term.

.. _design-with-issues:

Design new features with issues
-------------------------------

Design work for a new feature can happen in an `issue using the *feature request*
template
<https://salsa.debian.org/freexian-team/debusine/-/issues/new?issuable_template=Feature%20Request>`_.
This template asks you to present the new feature as a *user story* and
requires you to think about error handling and a possible implementation
plan.

The issue description is the canonical description of the proposed
feature. It is the duty of the developer proposing the new feature to
update the description to integrate the feedback of other developers.

.. _design-with-blueprints:

Design new features with development blueprints
-----------------------------------------------

For larger features that are unfit to be described within a single issue,
and where significant documentation will be required anyway,
we recommend to do the design work with the help of a :ref:`development
blueprint <development-blueprints>`.

A top-level issue with a short summary is still required for tracking
purpose. That issue can initially be labeled with ``Design required``.

It is up to the proposer (or anyone else that wants to help) to come up
with a first draft of the development blueprint and to submit it with a
merge request.

That merge request then becomes the main discussion place: other
developers open threads with their suggestions, and we can iterate on the
design much more effectively than in a simple ticket.
