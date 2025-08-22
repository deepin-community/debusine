.. _project-management-practices:

============================
Project management practices
============================

This page documents the workflows of Debusine's project managers.

Quarterly planning
------------------

At the end of each quarter, the work for the next quarter is planned by
creating milestones whose name starts with the quarter, e.g. ``2025-Q2``
for the second quarter of 2025:

* one ``Maintenance`` milestone for the on-going maintenance work that
  the Debusine team would like to tackle
* one ``Freexian`` milestone for the issues that Freexian would like to see
  tackled
* possibly other similar milestones for other sponsors
* one feature-based milestone for significant new development (if any is
  planned)

To fill the ``Maintenance`` milestone, the project managers review
the issues from the Backlog_ milestone and move the selected issues. The
goal is to keep up with the important bugs, the refactoring that
developers recorded, the easy improvements, etc.

When changing the milestone of an issue, its priority label should be
reconsidered and updated since the priority is only meaningful in the
context of a given milestone.

Bug triage
----------

The project managers regularly triage the incoming issues. The starting
point should be the `list of issues that have no milestone assigned
<https://salsa.debian.org/freexian-team/debusine/-/issues/?sort=created_asc&state=opened&milestone_title=None&first_page_size=50>`_.

Milestone choice
~~~~~~~~~~~~~~~~

The project managers use their best judgment to put the issues in one of
the existing milestones. Most of the time, it is a matter of choosing
between those two milestones:

* Backlog_: for issues that we eventually want to tackle. This list is
  reviewed for inclusion when planning upcoming quarterly milestones.
* `One day maybe`_: for issues that we are unlikely to deal on our own.
  They can be implemented by external contributors who may find them more
  relevant to their priorities.

.. _Backlog: https://salsa.debian.org/freexian-team/debusine/-/issues/?sort=priority&state=opened&milestone_title=Backlog&first_page_size=50
.. _One day maybe: https://salsa.debian.org/freexian-team/debusine/-/issues/?sort=priority&state=opened&milestone_title=One%20day%20maybe&first_page_size=50

But when the issue is important enough that it can't wait the next
quarter, then the project managers can also pick one of the quarterly
milestones. When doing that, they should consider removing some existing
issues from the same milestone so as to not exceed the team's capacity.

Adding labels
~~~~~~~~~~~~~

The project managers label incoming issues. In practical terms, it means
classifying them along the following properties:

* Type of issue: ``Bug``, ``Feature``, ``Refactoring`` or ``Discussion``
* Priority of the issue: ``P1`` for highest priority, up to ``P3`` for
  lowest priority.
* Difficulty of fixing:

  * ``Quick fix``: small issues that are easy to start with and that
    should not take too long. Suitable for new contributors.

* Who requested/filed the issue:

  * ``Debian``: a request of a Debian team
  * ``Freexian``: a request of a Freexian team
  * ``Community``: a request from a user not related to a specific team

* What the issue is about:

  * ``Administration``: it concerns administrator-level operations or
    significantly impacts production deployments
  * ``Documentation``: changes to the documentation are needed/requested
  * ``Architecture``: discussions about software design-level considerations
  * ``Development``: CI infrastructure/tooling for Debusine developers, or
    impacts the Debusine contributors' experience
  * ``Performance``: poor performance in some situation
  * ``Project management``: discussions about planning, coordination, policies, etc.
  * ``Security``: there's a possible security impact
  * ``UI``: changes to the User Interface are needed/requested
  * ``Usability``: impacts the user experience in a significant way

* What is the next step for the issue to go forward:

  * ``Design required``: an architect needs to provide a proper design
  * ``Investigation required``: a developer or sysadmin needs to
    investigate to understand what's happening under the hood

Once these tasks are completed, the labels should be removed.
