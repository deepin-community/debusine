.. _updating-release-notes:

======================
Updating release notes
======================

We use `towncrier <https://towncrier.readthedocs.io/en/stable/>`__ to
maintain the :ref:`release history <release-history>`.  This is where we
keep users up to date with new features and breaking changes.

During development
==================

Where relevant, merge requests should include a "news fragment" with a brief
user-focused description of the change.  This is a requirement for
backward-incompatible changes, and encouraged for features and bug-fixes.
Refactoring, test-only changes, and other things that are not directly
visible to users do not generally need news fragments.

File names
----------

News fragments are stored in ``newsfragments/<section>/<issue>.<type>.rst``;
a link to the issue will automatically be included in the release notes.

For changes that do not have an associated issue, use
``newsfragments/<section>/+mr<merge-request>.<type>.rst``; this requires
creating the merge request first in order to get its ID.  (In fact, any
first segment starting with ``+`` is fine, but the ``+mr<merge-request>``
convention helps us to avoid collisions and makes tracking easier.)

News fragment sections
----------------------

We have the following sections for news fragments, each of which corresponds
to a section in the assembled release notes:

* Server (``newsfragments/server/``): Backend code on the Debusine
  server, especially changes to ``debusine/db/`` and ``debusine/server/``.
  This includes ``Server``, ``Internal``, and ``Wait`` tasks.

* Web UI (``newsfragments/web/``): The Debusine web user interface,
  especially changes to ``debusine/web/``.

* Client (``newsfragments/client/``): The ``debusine`` client,
  especially changes to ``debusine/client/``.

* Workflows (``newsfragments/workflows/``): :ref:`workflow-reference`,
  especially changes to ``debusine/server/workflows/``.

* Tasks (``newsfragments/tasks/``): :ref:`task-reference`, especially
  changes to ``debusine/tasks/``, ``debusine/server/tasks/``, and
  ``debusine/signing/tasks``.

* Worker (``newsfragments/worker/``): The Debusine worker,
  especially changes to ``debusine/worker/``.

* Signing (``newsfragments/signing/``): The Debusine signing
  worker, especially changes to ``debusine/signing/``.

* General (``newsfragments/``): Anything that does not fit into the above
  categories, including significant codebase-wide changes.

News fragment types
-------------------

We use the following types to categorize news fragments:

* ``incompatible``: An incompatible change.
* ``feature``: A new feature.
* ``bugfix``: A bug fix.
* ``doc``: A documentation improvement.
* ``misc``: An issue has been closed, but it is not of interest to users.
  (This also includes issues about problems with other changes that were
  introduced since the last release; it doesn't normally make sense to
  document these separately.)

Writing style
-------------

News fragments should be in the imperative mood (i.e. "Fix something", not
"Fixes something" or "Fixed something").

They should be complete sentences, ending with a full stop.

They may use reStructuredText features, such as ``:ref:`` for cross-references.

Each news fragment should generally be on a single line.  ``towncrier`` will
wrap them as necessary when assembling the release notes.  If a single issue
needs more than one entry with the same section and type, then put each one
on a separate line.

If an issue is relevant to multiple sections or types, it may have multiple
news fragments to cover them all.

Examples
--------

If you made a breaking change to a workflow in response to issue #42, then
you would put a news fragment in
``newsfragments/workflows/42.incompatible.rst``.

If you made an important bug fix to a server database model in merge request
!999 with no associated issue, then you would put a news fragment in
``newsfragments/server/+mr999.bugfix.rst``.

If you added a new feature to the web UI in response to issue #123, then you
would put a news fragment in ``newsfragments/web/123.feature.rst``.

At release time
===============

The release manager runs ``towncrier build --version <new-version>`` to
assemble the new release notes.  They may edit the result to improve
readability (for example, to consolidate items that relate to the same
overall topic), or to add significant changes that did not come with news
fragments.
