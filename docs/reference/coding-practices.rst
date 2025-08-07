.. _coding-practices:

================
Coding practices
================

Please refer to :ref:`contribute-to-debusine` for general contributing
guidelines. Once you are setup to contribute and know what you want to
work on, you should read this document to learn our expectations on the
code that you will submit as well as on the process leading to integrate
your contribution.

.. _tdd:

Introduction to Test Driven Development
---------------------------------------

We expect you to follow this methodology when developing new code so
if you are not familiar with it, have a look at `Test-Driven Web
Development with Python <https://www.obeythetestinggoat.com/>`_.

The suggested workflow looks like this:

  1. Add a functional test that covers the new feature from the point of
     view of the user. This test will fail since the feature doesn't exist
     yet.

  2. Think about what's the next step to let the functional test go
     further (i.e. fail later).

  3. Write a failing unit test for the new code that you want to write.

  4. Write the minimal code to make your unit test pass. You will
     typically run this very often:

     .. code-block:: console

        $ ./manage.py test path-to-the-testing-folder

  5. Refactor (if needed). You might have introduced some duplication in
     your code or in your tests. Clean that up now before it's too late.

  6. Commit (optional). Commit together the (working) unit tests and the
     new code.

  7. If you made progress from the functional tests point of view, go back
     to point 2, otherwise go back to point 3. If the functional test
     passes, continue.

  8. Commit. The functional tests are committed at this point to ensure
     that they are committed in a working state:

     .. code-block:: console

        $ git add .
        $ git commit

When you don't develop a new feature, your workflow is restricted to steps
3 to 6.

Git commit messages
-------------------

Please invest some time to write good commit messages. Just like your code,
you write it once but it will be read many times by different persons
looking to understand why you made the change. So make it pleasant to
read.

The first line is the “summary” (or title) and describes briefly what the
commit changes. It's followed by an empty line and a long description. The
long description can be as long as you want and should explain why you
implemented the change seen in the commit.

The long description can also be used to close GitLab issues by putting
some ``Fixes: #XXX`` pseudo-fields at the end of the description.

.. _pre-commit:

Pre-commit hooks
----------------

The :ref:`setup instructions for developers <developer-setup>` recommend
setting up `pre-commit <https://pre-commit.com/>`__.  This runs a variety of
linters on the files changed by the commit to catch some problems that can
be found by static analysis.  These are defined as a collection of "hooks"
in ``.pre-commit-config.yaml``.

To run linters over all files rather than just changed files, run
``pre-commit run -a``.  This is particularly useful after a rebase, since
the git hooks that ``pre-commit`` uses by default don't run on rebase (and
the ones that do run on rebase don't operate on specific files anyway).

``pre-commit run -a`` is also useful if you want to check unstaged files,
since ``pre-commit`` normally only checks committed and staged files, and
temporarily stashes unstaged changes.  Things will normally work better if
you ``git add`` your changes first.

To skip a particular pre-commit hook, you can set the ``SKIP`` environment
variable to a comma-separated list of hook IDs: for example, ``SKIP=mypy``.

To bypass pre-commit hooks temporarily for a single commit, use ``git commit
-n``.

To upgrade hooks, run ``pre-commit autoupdate``, and then ``pre-commit run
-a`` to check for new errors.  Note that some hooks may be deliberately
pinned to older versions, in which case you will need to revert changes to
their versions afterwards.

About merge requests
--------------------

Size of merge requests
~~~~~~~~~~~~~~~~~~~~~~

Submitted merge requests should have a reasonable size: that is no more
than 1000 lines of changes (total of added and removed lines, excluding
test data and other auto-generated files) for large changes. We expect the
majority of merge requests to be smaller than that.

If you are working on a large feature, we expect you to submit it
progressively in multiple independent steps. If the separate merge of
those individual steps would break Debusine, then ask us to create a
staging branch for your new feature and target that separate branch in
your merge requests.

If you figure out that some refactoring is needed before you can
reasonably implement your feature, consider doing that refactoring in a
separate merge request.

A merge request can have multiple independent commits to make it easier
to review.

.. _updates-to-merge-requests:

Updates to merge requests
~~~~~~~~~~~~~~~~~~~~~~~~~

When a merge request has multiple independent commits, we want to preserve
that split and not squash all the commits together. This means that
fixes and improvements done following the review have to be incorporated
in their respective commit.

With regular rebase -i
**********************

Assuming that the merge request targets the ``devel`` branch, you can do
that locally with::

    $ git commit --fixup=$COMMIT
    $ git rebase -i --autosquash $(git merge-base HEAD devel)
    $ git push --force-with-lease origin $BRANCH

The rebase command above ensures that the merge request is not rebased
against newer version of the ``devel`` branch: this is important for big
merge requests that have already been reviewed, so that the diff
associated to the push does not include changes coming from the ``devel``
branch.

.. note::

   If you need to rebase such a branch against the latest version of the
   ``devel`` branch, please do that in a push where that is the only
   change. You can do that with::
  
        $ git fetch origin
        $ git rebase origin/devel
        $ git push --force-with-lease origin $BRANCH

For small merge requests, this is not so important and you can rebase
against ``devel`` or ``origin/devel`` directly::

    $ git rebase -i --autosquash origin/devel

With push of fixup commits
**************************
Alternatively, you can simply push fixup commits to the remote branch
while review is in progress, and let the final ``git rebase -i
--autosquash`` be done by whoever gets to merge the branch. Note however
that the presence of fixup commits in the history will change the status
of the merge request back to "draft".

.. note::

   If the merge request has multiple commits, and if you use GitLab's "Add
   suggestion" feature, please input a commit message ``fixup!
   $TARGET_COMMIT_TITLE`` so that it can be easily autosquashed by the
   person that will perform the final merge.

Finally, if the merge request has a single commit (and is expected to
stay that way), you should be free to select "Squash commits when merge
request is accepted" so that the final merge automatically squashes any
fixup commit.

Discussions in merge requests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Discussions within merge requests should be limited to code reviews:
pointing out mistakes and inconsistencies with the associated issue,
suggesting improvements, etc. If there are architecture or design issues
that need to be addressed, or if there are disagreements between the coder
and the reviewer, then those discussions should be moved to a separate
issue and be resolved there before getting back to the merge request.

Work in progress merge requests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In order to benefit from the full CI during development, developers can
open merge requests early while they are still working on the feature.

Those merge requests should have the draft status and the ``MR: Work in
progress`` label. They can be freely rebased, refactored and can be
subject to large changes. They also don't have any assignee or reviewer
set.

Such merge requests are not open for detailed review (unless the
submitter explicitly asks for early review).

Once the ``MR: Work in progress`` label is dropped, the merge request is
open for review and subsequent changes should follow the rules described
in :ref:`updates-to-merge-requests`.

.. note::

   We rely on the label rather than only on the "draft" status, because
   push of fixup commits might bring back the status of a good merge
   request back to draft. Hence you can't rely solely on the presence of
   the draft flag to decide to not review a merge request.

Merge requests labels
~~~~~~~~~~~~~~~~~~~~~

To help keep track of the status of the various merge requests, we have
a few labels all starting with "MR:" (for easy auto-completion):

* ``MR: Work in progress``: the work is not yet ready to be reviewed
* ``MR: Needs work``: a reviewer found issues, they have to be addressed.
  This label doesn't need to be immediately set during review, it can be
  set later if we realize that the submitter is not dealing with
  comments in a timely fashion. The label can be dropped by the submitter
  after having handled all the issues that have been identified.
* ``MR: UI review needed``: the merge request contains a prototype UI that
  needs usability feedback (usually via a ``playground-vm`` machine) before
  proceeding with further work

Note that any merge request without any "MR" label is thus implicitly in a
status where the branch is assumed to be ready and where the submitter is
thus seeking reviews.

Usage of 'Assignee' and 'Reviewer'
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

As a submitter of a merge request, you don't have to set those fields,
by default every contributor is encouraged to review open merge requests.
There are two cases where we might set the Reviewer field:

* when the submitter would like to ask someone specific to review the MR
  (due to former experience on the code being modified for example)
* when a reviewer wants to notify other team members that he is currently
  reviewing that merge request

Final merge and approvals
~~~~~~~~~~~~~~~~~~~~~~~~~

The final merge, if not done by a core developer, should only happen
after a :ref:`core developer <team-organization>` has approved the merge
request and after all comments left by the core developer (if any) have
been addressed.

Prior to merge, one should always ensure that all fixup commits have been
properly squashed in their respective commits.

If such an operation is required, you should take the opportunity to
rebase the branch against the latest version of the ``devel`` branch. The
rebase is also recommended if the base of the merge request is far behind
the current tip of the ``devel`` branch: the rebase will trigger a final
pipeline run on a codebase that will be much closer to the result of the
merge, thus limiting the risk of failed pipeline on ``devel`` after the
merge.

Coding style
------------

  1. In regard to coding style, we observe `PEP8
     <https://peps.python.org/pep-0008/>`_ with a few exceptions.
     And we format the code with `black <https://github.com/psf/black>`_
     by running ``make black`` (we use a different line length and don't
     impose the single type of quote for strings).

  2. Functions are documented using docstrings with `Sphinx markup
     <https://www.sphinx-doc.org/en/master/>`_.

  3. Imports are sorted in multiple groups separated by one empty line:
     first a group for ``__future__`` imports, then a single group for all
     the Python standard modules, then one group for each third-party
     module (and groups are sorted between them as well), followed by
     groups for the project modules, and last, one group for
     relative imports.

     Within each group the ``import foo`` statements are grouped and
     sorted at the top, while the ``from foo import bar`` statements
     are grouped and sorted at the end.

     Example:

.. code-block:: python3

   from __future__ import print_function

   import datetime
   import os
   from datetime import timedelta
   from email.utils import getaddresses, parseaddr

   from django.conf import settings
   from django.db import connection, models
   from django.utils.safestring import mark_safe

   import requests
   from requests.structures import CaseInsensitiveDict

   from debusine.artifacts.models import Artifact

Good code, good design
----------------------

This section documents different decisions about implementation,
naming, etc. that happened during merge requests. It is not an
exclusive list of all the discussions and is subject to change.

Those rules are meant to help improve consistency and to obtain
a cleaner overall design.

Models
~~~~~~

Avoid changing fields from outside the model
********************************************

Avoid changing fields in the models from their users. Do not do:

.. code-block:: python3

   worker.connected_at = timezone.now()

Instead, create a method in Worker describing the action that you are doing:

.. code-block:: python3

   worker.mark_connected()

And change the relevant fields from ``mark_connected()``.

This allows the model's fields or logic to change without having to change
the code which accesses it.

Read more in `Push actions to the model layer <https://spookylukey.github.io/django-views-the-right-way/thin-views.html#example-push-actions-to-the-model-layer>`_.

.. _push-filtering-model-layer:

Push filtering to the model layer
*********************************

In order to encapsulate logic for ``filter`` and other queries, add a ModelManager
to the Model and do the filtering there. Do not do:

.. code-block:: python3

   worker.objects.filter(connected_at__isnull=False)

Instead create a ``connected`` method in the Worker's Manager and use it:

.. code-block:: python3

   worker.objects.connected()

This allows the code base to be consistent in the filtering.

Read more in `Push filtering to the model layer <https://spookylukey.github.io/django-views-the-right-way/thin-views.html#example-push-filtering-to-the-model-layer>`_.

Push Model.objects.create() to the model layer
**********************************************

Similar to
:ref:`Push filtering to the model layer <push-filtering-model-layer>`:
avoid using ``Model.objects.create()`` (or ``.get_or_create()``) and add a method
in the ModelManager describing the operation, such as:

.. code-block:: python3

   Worker.objects.create_with_fqdn(fqdn, token)


Naming fields
*************
Add the suffix _at for the fields of type `DateTime`:

.. code-block:: python3

   created_at = models.DateTimeField()

Model method order
******************

Follow the `Code Review Doctor model method order <https://codereview.doctor/features/django/best-practice/model-method-order>`_:

 #. Field choice tuples
 #. Database fields
 #. Custom manager attributes
 #. ``class Meta``
 #. ``def __str__()``
 #. ``def save()``
 #. ``def delete()``
 #. ``def get_absolute_url()``
 #. Any custom methods

The Code Review Doctor method order is compatible with order in the `Django documentation <https://docs.djangoproject.com/en/dev/internals/contributing/writing-code/coding-style/#model-style>`_ making the choice tuples and delete order explicit.

Error handling
**************

Management commands that create model instances should show helpful error
messages for any resulting constraint violations.  PostgreSQL's error
messages are informative but can be difficult for non-experts to read.
Since management commands aren't very performance-sensitive, it is usually
best to ask Django to perform its own constraint validation before inserting
data into the database so that it can produce better error messages.  For
example:

.. code-block:: python3

    try:
    	collection = Collection(...)
        collection.full_clean()
        collection.save()
    except ValidationError as exc:
        raise CommandError(
            "Error creating collection: " + "\n".join(exc.messages),
            returncode=3,
        )

Templates
~~~~~~~~~

Naming templates
****************

If a Django template file is used to render a full page (including the HTML
header, footer, etc.) it should follow a similar structure to
``work_request-list.html``.

If a template file is meant to be included from another template, add a ``_``.
For example: ``_work_request-list.html`` is meant to be included from
another template.

Include templates: specify the context explicitly
*************************************************

When including a template, specify the context made available to it:

.. code-block::

  {% include "web/_workspace-list.html" with workspace_list=workspace_list user=user only %}

It helps the reader to know which context is used by the template and
also avoids using context that might be available from one
``{% include ... %}`` but not from another ``{% include ... %}``.

Tests
~~~~~

Private methods
***************
To facilitate Test-Driven Development and localised tests, it is ok to
call private methods from the tests.

Assert function: order of the parameters
****************************************
In the assert methods, put the "expected" value as second parameter, for example:

.. code-block:: python3

   self.assertEqual(actual, expected)

Reason: some test methods such as ``assertQuerySetEqual`` expect "actual"
to be the first parameter. Always using this order helps the tests to be
more easily read.

Assert functions: assertEqual or specific
*****************************************
When there is a TestCase method with specific semantics, use them:

 * ``self.assertQuerySetEqual()`` for testing querysets
 * ``self.assertTrue()`` or ``self.assertFalse()`` for testing boolean expressions
 * ``self.assertIn()`` or ``self.assertNotIn()``

Using the specific methods such as ``self.assertIn()`` helps to have a better test
output compared with constructions such as ``self.assertTrue('john' in people)``.

When possible (actual and expected are the same type), use ``self.assertEqual()``
instead of methods such as ``self.assertDictEqual()``. ``self.assertEqual()`` will
use the correct underlying method.

Populating the database: Playground
***********************************

If, while writing test code, you end up creating factory methods to create
pydantic or database model objects, for example creating source or binary
packages, check if suitable code already exists in the various Playground
classes.

If you need to create new ones, consider adding them to Playground classes so
they can be reused, both in tests and in creating scenarios for testing UI
prototypes.

Playground integrates well with Django's ``TestCase.setUpTestData``, so if you
are testing a nontrivial scenario you can create it once per test class, and
have it rolled back to its pristine state before every test method. Playground
takes care to ensure this also works for file store changes.

Evaluating UI prototypes
************************

There is a ``bin/playground-vm`` script that will deploy the branch from a
merge request to a newly created VM, explicitly intended to be used to publish
a draft UI prototype for evaluation by other developers.

The script runs ``bin/playground-populate`` during provisioning, which
builds the ``UIPlayground`` scenario. To discuss a UI prototype, you can:

1. Code the prototype views and templates; at this stage your branch doesn't
   need to pass CI or have full code coverage.
2. Make sure :py:meth:`UIPlayground.build()
   <debusine.db.playground.scenarios.UIPlayground.build>` populates the
   database with enough information to show the features you are coding.
   Feel free to extend it otherwise.
3. Push your branch to a merge request. You can push without running the CI by
   using ``git push -o ci.skip``
4. Use ``bin/playground-vm`` to deploy the merge request to a publicly
   accessible VM.

Relaxed docstring requirements for test methods
***********************************************

There are common cases in test code where docstrings would be a trivial
rephrasing of the test method names, and can be omitted. For example::

    def setUpTestData(cls) -> None:
        """Set up common test data."""

    def test_str(self) -> None:
        """Test stringification."""

    def test_get_absolute_url(self) -> None:
        """Test the get_absolute_url method."""

    def test_is_valid_workspace_name(self) -> None:
        """Test is_valid_workspace_name."""

    def test_workspace_name_validation(self) -> None:
        """Test validation for workspace names."""

Do use docstrings, however, when the intention of the test method is not clear
from its name alone. For example::

    def test_workspace_roles_empty(self) -> None:
        """Test the WORKSPACE_ROLES query filter with no args."""

    def test_populate_qa_with_sbuild_subset(self) -> None:
        """`sbuild` may only create a subset of the requested architectures."""

When the test method name is clear enough as a summary and a longer explanation
is needed, you can omit the summary in the docstring::

    def test_populate_use_available_architectures(self) -> None:
        """
        The user didn't specify "architectures", DebianPipelineWorkflow
        checks available architectures and "all" and use them.
        """

flake8 tests for test code have been relaxed accordingly.

For a short guideline for writing docstrings for tests, have a look at `How to
write docstrings for tests <https://jml.io/test-docstrings/>`_.

Unfortunately it is not possible to distinguish between test method names and
other method names in flake8 configuration, so please make sure to have
appropriate docstrings for all ``assert*`` and other helper methods.

Naming data keys
~~~~~~~~~~~~~~~~

Names of data keys in artifact, collection, and task definitions can be
expected to be used in ``pydantic`` models.  As such, they must be valid
Python identifiers: in particular, use underscores rather than hyphens as
separators.

General guidelines
~~~~~~~~~~~~~~~~~~

Constants
*********

If one of our dependencies provides defined public constants, use them instead
of re-defining them or using magic numbers.

.. code-block:: python3

    # Use:
    from rest_framework import status
    code = status.HTTP_501_NOT_IMPLEMENTED

    # Instead of:
    code = 501

This helps readability for readers that might not know all the internal codes,
might avoid typos and if the "constants" depended on versions, environment, etc.
the library will take care of them.

Type hints
**********
If you want to indicate the type of a variable, type hints are preferable to
adding suffixes to the variable.

.. code-block:: python3

    # Use:
    def method(information: dict):
       ...

    # Instead of:
    def method(information_dict):
        ...

This helps (IDEs, mypy) to give hints to the programmer and it keeps
the variable names shorter, avoiding the type repetition.


Early exit
**********

To exit early:

.. code-block:: python3

  # Use:
  raise SystemExit(3)

  # Instead of:
  sys.exit(3)
  exit(3)

It says explicitly what it does and there is no need to import the `sys` module.

If any utility in Debusine must exit early:
 * Use **exit code 3**. Exit code 1 is used by the Python interpreter for
   unhandled exceptions and exit code 2 by the argparse module for invalid
   command syntax.
 * Make sure to **log or print** (depending on the circumstances) why an early
   exit has happened so the user or admin can fix the situation.
