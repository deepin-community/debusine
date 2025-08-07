.. _development-team-organization:

====================================
Organization of the development team
====================================

This page documents how we intend to work as a team. It's particularly
relevant for developers paid by Freexian, but can be useful for others
too.

Roles
-----

This documentation refers to a few different roles:

* Developer: implements the requested features
* Project manager: coordinates the team and the work
* Architect: takes the biggest design decisions (interfaces,
  software's structure, choices of technologies)
* Community manager: manages relationships with Debian and other
  free software communities

Each team member can have multiple roles.

Project management
------------------

Work is planned in GitLab issues on the Debusine repository. See
:ref:`project management practices <project-management-practices>` for
more information about this.

The project managers share regular status updates to the team.

Development workflow
--------------------

The developers can self-assign issues from the `milestones of the current
quarter <https://salsa.debian.org/freexian-team/debusine/-/milestones>`_
(ex: ``2025-Q2 Maintenance`` for the maintenance milestone of the second
quarter of 2025). They should usually start with the highest priority
issues (those with label ``P1``, then ``P2`` and finally ``P3``).

Other guidelines relative to development can be found in the sections
:ref:`contribute-to-debusine` and :ref:`coding-practices`.

You are encouraged to create draft merge requests quite early in your
work, in particular when you have doubts about some design choices and
would like to have feedback.

About code reviews
------------------

Code reviews are mandatory to get any significant code contribution
merged. Developers should thus always submit merge requests for any non-trivial
change.

Developers should prioritize code reviews over development work so as to not
stall development work for others, and ensure timely and regular merge of
new code.

All non-trivial merge requests (such as new features) have to be reviewed and
approved by a core developer or architect. Initial code reviews can be picked
up by anyone. We highly encourage everyone to do such code reviews so as to get
familiar with more parts of the codebase. To avoid duplication of work,
reviewers should mark themselves as such when they start a review.

If reviewers only review some aspect of the code, they should make
that clear in their review comments. Developers should seek out extra reviewers
as needed, based on the complexity of their change.

During code reviews, you should ensure that:

* coding practices have been respected
* the commit history is clean (good commit messages, meaningful split of commits,
  any fixup commit is clearly targeted to be autosquashed)
* unit tests are meaningful and readable without (too much) duplication
* the code actually implements the initial request

You can also suggest improvements and/or simplifications based on your own
experience and knowledge of the codebase.

.. _communication:

Coordination and communication tools
------------------------------------

The bulk of the work happens asynchronously and transparently on GitLab
issues and merge requests. But we have also have real time communication
channels with IRC and Matrix.

GitLab
~~~~~~

Watching the whole project is not required. Depending on your preferences,
you could however configure custom notifications to watch at least "New
merge request" and "Merge merge request" events.

You also have the possibility to subscribe to issues with a specific
label by clicking on the "Subscribe" button next to each label
on the `Labels page
<https://salsa.debian.org/freexian-team/debusine/-/labels>`_. Following
"Design discussion" can be interesting if you want to contribute to the
overall design.

IRC channels / Matrix
~~~~~~~~~~~~~~~~~~~~~

Presence on the #debusine IRC channel (or the corresponding Matrix room)
is highly recommended to facilitate real time cooperation and
coordination. For instance, it can be used to share the work load of code
reviews, so that they are spread across all developers. It can also be
used to avoid code conflicts when two developers are working on related
parts.

The #debusine-notifications IRC channel (or the corresponding Matrix room)
offers an alternate way to consume the GitLab-based notifications (pushes,
merge requests, issues).

.. _team-organization:

Current team organization
-------------------------

People in bold are the main actors for each role. The others are in support
or as backup when the main person is not available.

* Core developers

  * Carles Pina
  * Colin Watson
  * Enrico Zini
  * Stefano Rivera

* Project manager

  * **Stefano Rivera**
  * Colin Watson
  * Raphaël Hertzog

* Architect

  * **Colin Watson**
  * Raphaël Hertzog

* Community manager

  * **Enrico Zini**
  * Stefano Rivera
