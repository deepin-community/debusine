.. _contribute-to-debusine:

======================
Contribute to Debusine
======================

Contributions to Debusine are greatly appreciated!
You can contribute in many ways, be it writing documentation and blog
posts, or fixing bugs and implementing new features.

Join the community
------------------
You can reach us using IRC on the #debusine channel at irc.debian.org.
That channel is also available as a (bridged) `Matrix room
<https://matrix.to/#/#_oftc_#debusine:matrix.org>`__. There's also
a #debusine-notifications channel where you can see all activities
happening in the GitLab project (it's also bridged in a `Matrix
room <https://matrix.to/#/!ugwLXLsYADeccIzBNd:matrix.org>`__).

You can also `file issues
<https://salsa.debian.org/freexian-team/debusine/-/issues>`_ in GitLab's
interface. There are two templates to help you file bug reports and feature
requests.

While Freexian is driving the development forward by paying a few
developers, rest assured that external contributions are welcome.

Where to start?
---------------

Starting small
~~~~~~~~~~~~~~

Before starting to work on bigger projects, it probably makes sense
to get used to our :ref:`development workflows <reference-contributors>`
by tackling a few `small issues
<https://salsa.debian.org/freexian-team/debusine/-/issues/?sort=created_date&state=opened&label_name%5B%5D=Quick%20fix>`_
(they have the *Quick fix* label).

Looking for issues
~~~~~~~~~~~~~~~~~~

If you are looking for bigger issues to tackle, you will find plenty in
the `issue tracker
<https://salsa.debian.org/freexian-team/debusine/-/issues/>`_.

The paid developers are usually focused on all the issues planned in the
`current milestone
<https://salsa.debian.org/freexian-team/debusine/-/milestones>`_, working
through the list by order of priority (from labels P1 to P3). If you want
to help with the current milestone, you should probably start with a
low-priority issue.

Otherwise you can also find `backlogged issues
<https://salsa.debian.org/freexian-team/debusine/-/issues/?sort=created_date&state=opened&label_name%5B%5D=Backlogged&first_page_size=20>`_
that are not part of any milestone and that paid developers will not
handle at this point.

Add a new feature
~~~~~~~~~~~~~~~~~

If you want to work on a new feature that is not yet planned, then you
should first create a new issue using the ``Feature Request`` template
(see :ref:`design-with-issues`).

For larger features that require significant design work, it is
recommended to first write a *development blueprint* (see
:ref:`design-with-blueprints`).

A particular case where we expect and invite contributions is that of new
workflows.  See :ref:`contribute-workflow` for more details of how to go
about doing that.

.. _developer-setup:

How to contribute
-----------------

.. note:: See :ref:`runtime environment <runtime-environment>` to understand
   the runtime environment.

Ready to contribute? Here's how to set up Debusine for local
development:

  #. Create a guest account on `Salsa <https://salsa.debian.org>`_ (a GitLab
     instance run by the Debian project) by visiting this page:
     https://salsa.debian.org/users/sign_up

     Follow all the steps to confirm your email, fill your profile,
     `setup your SSH keys
     <https://salsa.debian.org/help/user/ssh.md>`_.

     You might want to have a look at `Salsa's
     documentation <https://wiki.debian.org/Salsa/Doc>`_ and `GitLab's
     documentation <https://salsa.debian.org/help>`_ if you have doubts
     about something.

     Note that Debian Developers can skip this step as they already have
     an account on this service.

  #. Visit the `project's page <https://salsa.debian.org/freexian-team/debusine>`_
     and fork Debusine in your own account. See `GitLab's
     help on how to fork a project
     <https://salsa.debian.org/help/user/project/repository/forking_workflow.md#user-content-create-a-fork>`_.

  #. Clone Debusine locally:

     .. code-block:: console

       $ git clone git@salsa.debian.org:your-account/debusine.git

     Note that ``your-account`` should be replaced by your Salsa's username.
     If you want to clone the project without creating any account then
     use this command:

     .. code-block:: console

       $ git clone https://salsa.debian.org/freexian-team/debusine.git

  #. For a quick startup, run this command:

     .. code-block:: console

       $ bin/quick-setup.sh

     It will install required packages with apt and put a sample
     configuration file in place. It will also create a user
     and two databases in PostgreSQL. The user will be named
     after your current Unix user and will own the new databases
     so that it can connect to the newly created databases without any
     other authentication.

     Populate the database with:

     .. code-block:: console

       $ ./manage.py migrate

  #. Start a local test server:

     .. code-block:: console

       $ ./manage.py runserver
       [...]
       Starting development server at http://127.0.0.1:8000/
       Quit the server with CONTROL-C.

     Visit the URL returned to have access to the test website.

  #. If you want to run tasks, then start the Celery workers, from separate
     shells:

     .. code-block:: console

       $ python3 -m celery -A debusine.project worker -l INFO -Q scheduler --concurrency 1

     .. code-block:: console

       $ python3 -m celery -A debusine.project worker -l INFO

  #. You'll also need at least one worker, with ``sbin`` dirs in ``PATH``, e.g.:

     .. code-block:: console

       $ PATH="/usr/sbin:/sbin:$PATH" LANG=C.UTF-8 python3 -m debusine.worker

  #. Create a local Django superuser on your test instance:

     .. code-block:: console

        $ ./manage.py createsuperuser

  #. Login as that user using the link in the top right of the initial REST
     framework site.

  #. Set up ``pre-commit`` (optional):

     .. code-block:: console

       $ sudo apt install pre-commit
       $ pre-commit install

     This runs some checks every time you run ``git commit``.  This
     downloads linters from upstream repositories; if you prefer to avoid
     that on your development machine, you can skip this step and rely on CI
     running it for you instead (but with longer latency).  See
     :ref:`pre-commit` for more details.

  #. Switch to a new branch:

     .. code-block:: console

       $ git checkout -b name-of-your-bugfix-or-feature

  #. Develop your new feature, ideally following the rules of :ref:`Test-Driven Development <tdd>`.
     Remember to :ref:`update the release notes <updating-release-notes>` as
     needed.

  #. When you're done, check that all tests are succeeding in all
     supported platforms:

     .. code-block:: console

       $ tox

     This basically runs ``pytest`` with multiple versions of Django and
     Python. It also ensures that you respected our coding conventions. If
     you get errors, make sure to fix them.

     .. note::
        If you get errors like ``OSError: [Errno 38] Function not
        implemented``, then it means that you are lacking /dev/shm
        with proper permissions.

  #. Push your branch to your repository:

     .. code-block:: console

       $ git push -u origin name-of-your-bugfix-or-feature

  #. Submit us your work, ideally by opening a
     `merge request <https://salsa.debian.org/freexian-team/debusine/-/merge_requests/>`_.
     You can do this easily by visiting the Debusine project fork hosted in
     your own account (either through the “Branches” page, or through the
     “Merge requests” page). See `GitLab's help on merge requests
     <https://salsa.debian.org/help/user/project/merge_requests/creating_merge_requests.md>`_
     if needed.

     Make sure to address any issue identified by the continuous
     integration system, the result of its “pipeline” can be directly
     seen in the merge request (and in the commits pushed in your own
     repository).


Test latest ``devel`` branch code
---------------------------------

If you want to test the code in the ``devel`` branch, you can install packages
from a repository that is automatically generated by our GitLab CI
pipeline.

.. include:: /common/add-snapshot-repository.rst

Then run:

.. code-block:: console

   $ sudo apt update
   $ sudo apt install debusine-server debusine-worker debusine-client

And follow the instructions to set up :ref:`set-up-debusine-server`,
:ref:`set-up-debusine-worker` or :ref:`set-up-debusine-client`.

Write access to the git repository
----------------------------------

`Project members
<https://salsa.debian.org/freexian-team/debusine/-/project_members>`_ have
write access to the main git repository. They can thus clone the
repository with this URL:

.. code-block:: console

   $ git clone git@salsa.debian.org:freexian-team/debusine.git

From there they can push their changes directly. They are however free to
use a fork and request review anyway when they prefer.

Django Debug Toolbar
--------------------

`Django Debug Toolbar <https://django-debug-toolbar.readthedocs.io/en/latest/>`_ can be used but only on development branches.
There are commented out lines in
``debusine/project/settings/development.py`` and
``debusine/project/urls.py`` which can be used to add the support.
