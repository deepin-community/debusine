=======================
Refactoring page titles
=======================

Current page titles are simple ``<h1>`` elements, defaulting to ``{{title}}``,
which take space and do the job. The rendering we have is also often a bit off,
with page content too close to the ``<h1>`` titles.

We currently do not have a standard way to link to pages "above" when there is
a clear hierarchy: for example, a worker view is logically below a worker list
view, or a worker pool view; an artifact is in a workspace; a collection item
is in a collection; and so on.

I notice that GitLab tends not to have page titles in several pages, using
breadcrumbs to provide context instead.


A basic refactoring proposal
============================

Describing a page visually
--------------------------

All pages currently in debusine use a string for title except for artifact
views, which also have an icon.

Pages could also distinguish from a shorter title and a more detailed
description, depending on layout tradeoffs between size and detail.

A page could then be described, with all attributes needed to represent it
visually, by a structure like this::

    class PageInfo(NamedTuple):
        title: str
        long_title: str | None = None
        url: str | None = None
        icon: str | None = None


Move titles to the base template
--------------------------------

A simple first step is to move ``<h1>`` elements to the base template. This
makes individual page templates simpler, and allows to fix the spacing by
intervening in the base template.

It also allows tweaking rendering in alternative page layouts: for example
``_base_rightbar.html`` may decide whether to have the title span the whole
page or only the main content.

We can extend ``BaseUIView.get_title`` to return ``str | PageInfo`` to allow it
to also provide an icon where appropriate.


Introduce the concept of parent pages
-------------------------------------

We can have the base template generate a static hierarchy of breadcrumbs from a
list of ``PageInfo`` records.

To do so, we can extend ``BaseUIView.get_title`` to return ``str |
list[PageInfo]``, with one entry per parent, and the last entry representing
the current page.

This can be used by views to provide parent links. It can also be implemented
by intermediate view mixins: for example, ``WorkspaceView`` can return a link
to the workspace detail view as the default top parent of all views inheriting
from it.


Pages that may have multiple parents
------------------------------------

Artifact views are an example of views that can appear in workspaces, in
collections or in work requests, meaning that their generated parent lists have
2 in 3 chances to lose context in navigation.

This can be worked around by making artifact views appear multiple times in the
URL namespace, and generate different parent lists depending on where they are.


Experiment with layouts
-----------------------

At this point the base template has all the information it needs to render
breadcrumbs and titles, and we are then free to experiment with doing away from
``<h1>`` elements altogether, incorporating context into breadcrumbs, or with
providing breadcrumbs and visible page titles, as we see fit and as site usage
evolves.

There is enough information to be creative: for example, short titles could be
used for all steps except the last one, which can use the long title to give
more details about the current page. Some pages may indeed benefit from
retaining a big title.


A more comprehensive proposal
=============================

A more comprehensive version of the above is to introduce the concept of a Place::

    class Place:
        """Describe a page in Debusine's web space."""

        #: Short title
        title: str
        #: Long title (defaulting to short)
        long_title: str | None = None
        #: URL
        url: str | None = None
        #: Icon
        icon: str | None = None

We can create collections of ``Place`` factories as we have for ``sidebar`` and
``ui_shortcut``::

    debusine.web.views.places:

    def create_workspace(workspace: Workspace) -> Place:
        return Place(
            workspace.name, f"{workspace.name} workspace",
            workspace.get_absolute_url(), Icons.ICON_WORKSPACE
        )

``Place`` objects can then be used by ``BaseUIView.get_title`` as well as by ui
shortcuts and sidebar items.

We can experiment with ways for ``Place`` objects to render themselves, either
with methods similar to Django's ``Form``, (``as_a``, ``as_a_long``, ...) or
using the Debusine widget template, possibly augmented with a parameter to
select rendering variants, such as ``{% widget my_place long %}``.
