========
UI views
========

Base layouts
============

Debusine associates a base view in ``debusine.web.views.base*`` to each base
template in ``debusine/web/templates/web/_base*``, so that the context
expectations of the template can be fulfilled by functions in the view.

Context elements are defined in ``get_NAME`` methods, sometimes cached in
corresponding ``NAME`` cached properties.

base
----

Defined in ``debusine.web.views.base.BaseUIView``.

Since it is used as a base view for more specialised base templates, it has
also a ``get_base_template`` methods that returns the name of the base template
to load.

base_rightbar
-------------

Defined in ``debusine.web.views.base_rightbar.RightbarUIView``.

This view defines a sidebar with UI shortcuts and contextual information.


UI widgets
==========

UI widgets are small classes that encapsulate an item of data to be presented
to the user and its associated snippet of view code.

The base interface is a class with a ``render(context: BaseContext) -> str``
method that returns the HTML rendering of the information it contains.

Widgets can be passed to templates as part of the view context, and can be
rendered after ``{% load debusine %}`` using the ``{% widget varname %}`` tag.

Using widgets guarantees a consistent visual language across different pages.

Using widgets also allows to write more semantic view tests, where the test can
check the data in unrendered widgets in the request context instead of pattern
matching HTML.


UI shortcuts
------------

UI shortcuts are widgets representing active button-like elements that can be
added to `Bootstrap button groups`_ to perform actions about a related UI
element.

View support is currently implemented in the
``debusine.web.views.base_rightbar.RightbarUIView`` base view.

They can currently be defined at a page-wide scope using the
``get_main_ui_shortcuts`` method, and at a model object scope using the
``add_object_ui_shortcuts`` method.

The ``debusine.web.views.ui_shortcuts`` module defines various factory methods
to instantiate commonly used UI shortcuts.

.. _`Bootstrap button groups`: https://getbootstrap.com/docs/5.0/components/button-group/



Sidebar items
-------------

A sidebar item is a row in the context information present in the sidebar.

It can display:

* an icon
* a label, describing what the field is about
* a raw value
* a user-friendly value

Sidebar items can be defined using the ``get_sidebar_items`` method.

The ``debusine.web.views.sidebar`` module defines various factory methods
to instantiate commonly used items.
