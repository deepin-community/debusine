=======================
Customize site branding
=======================

Django template lookup
======================

Django templates are searched in the directory configured in settings as
``DEBUSINE_TEMPLATE_DIRECTORY`` (by default: ``/var/lib/debusine/server/templates``),
which has priority over the packaged ones.

If a template extends a template with the same name, it will be looked up next
in the search path, and this is useful to customize existing templates: for
example, one can create a ``/var/lib/debusine/server/templates/web/_base.html``
template that uses ``{% extends "web/_base.html" %}`` to customize the general
site layout.

Django uses caching for templates, and in production a server reload is needed
for changes in template files to take effect.


Examples
========

Customize the footer
--------------------

Create a ``/var/lib/debusine/server/templates/web/footer.html`` template that
either provides your own footer, or ``{% extends "web/footer.html" %}``.

For example, to add an extra status link to the existing footer::

    {% extends "web/footer.html" %}
    {% block status %}
    {{ block.super }} • <a href="https://example.org/dashboard">Infrastructure</a>
    {% endblock %}

Customize the homepage
----------------------

Create a ``/var/lib/debusine/server/templates/web/homepage.html`` template that
``{% extends "web/homepage.html" %}``.

For example, to change the introduction::

    {% extends "web/homepage.html" %}
    {% block introduction %}
        <h1 class="mb-3">Welcome to a custom instance Debusine!</h1>
        <div class="row">
            <div class="col-10 col-xxl-8">
                <p>
                    You can find more information about this instance at <a
                    href="https://example.org/support/debusine">our Debusine
                    support page</a>.
                </p>
            </div>
        </div>
    {% endblock %}
