.. _url-redesign-blueprint:

============
URL redesign
============

The structure of Debusine URLs has been so far mainly improvised.

As Debusine matures and more of it takes shape, we can do a round of URL
redesign.


Current URL structure
=====================

This is the current situation after removing
the Django admin and API endpoints::

  /
  /<path>
  /<scope>/accounts/accounts/bind_identity/<name>/
  /<scope>/accounts/accounts/oidc_callback/<name>/
  /<scope>/accounts/login/
  /<scope>/accounts/logout/
  /<scope>/artifact/<int:artifact_id>/
  /<scope>/artifact/<int:artifact_id>/download/
  /<scope>/artifact/<int:artifact_id>/download/<path:path>
  /<scope>/artifact/<int:artifact_id>/file/<int:file_in_artifact_id>/<path:path>
  /<scope>/artifact/<int:artifact_id>/raw/<int:file_in_artifact_id>/<path:path>
  /<scope>/artifact/create/
  /<scope>/task-status/
  /<scope>/user/token/
  /<scope>/user/token/<int:pk>/delete/
  /<scope>/user/token/<int:pk>/edit/
  /<scope>/user/token/create/
  /<scope>/work-request/
  /<scope>/work-request/<int:pk>/
  /<scope>/work-request/<int:pk>/retry/
  /<scope>/work-request/<int:pk>/unblock/
  /<scope>/work-request/create/
  /<scope>/workers/
  /<scope>/workspace/
  /<scope>/workspace/<str:wname>/collection/
  /<scope>/workspace/<str:wname>/collection/<str:ccat>/
  /<scope>/workspace/<str:wname>/collection/<str:ccat>/<str:cname>/
  /<scope>/workspace/<str:wname>/collection/<str:ccat>/<str:cname>/item/<str:iid>/<str:iname>/
  /<scope>/workspace/<str:wname>/collection/<str:ccat>/<str:cname>/search/
  /<scope>/workspace/<str:wname>/

Use ``/-/`` as path to non-data URL components
==============================================

Gitlab uses ``/-/`` as a URL component to prefix URL subpaths that are not part
of user-controlled namespaces. For example:

* ``https://salsa.debian.org/-/user_settings/profile``
* ``https://salsa.debian.org/freexian-team/debusine/-/merge_requests``

We can do the same, for example:

* ``/-/status/queues/``
* ``/-/status/workers/``
* ``/-/user/``



Leave ``/api/`` as a toplevel special case
==========================================

It makes sense to keep ``/api/`` at toplevel instead of moving it to
``/-/api/``, and add ``api`` to a word denylist for scopes.

Unless it's technically unfeasible, ``/api-auth`` can be moved to
``/api/auth``.


Remove ``/workspace/``
======================

We can remove ``/workspace/`` to have a more forge-style URL structure.

See merge request !1269.
  

URLs that should not be scoped
==============================

These URL namespaces should not be scoped:

1. ``admin/*``
2. ``task-status/*``
3. ``user/*``
4. ``workers/*``

They should both be moved under ``/-/``:

1. ``/-/admin/*``
2. ``/-/status/queues/*``
3. ``/-/user/*``
4. ``/-/status/workers/*``

Some views, like ``queues`` (former ``task-status``) and ``workers`` need to be
reviewed to avoid disclosing information not accessible by the current user.

In the case of ``workers``, instead of hiding workers that process tasks which
the user does not have rights to display, it makes sense to always show all
workers, possibly busy with tasks that cannot be displayed.


Restructure ``accounts``
========================

``accounts`` contains a mix of authentication related endpoints, which can be
restructured (see also #553):

* ``/<scope>/accounts/accounts/bind_identity/<name>/``: move to ``/-/signon/bind_identity/<name>``
* ``/<scope>/accounts/accounts/oidc_callback/<name>/``:	move to ``/-/signon/oidc_callback/<name>/``
* ``/<scope>/accounts/login/``: move to ``/-/login``
* ``/<scope>/accounts/logout/``: move to ``/-/user/logout``


work-request/
-------------

This moves to a filtered, per-workspace list, as
``/<scopename>/<workspace_name>/status/work-requests/``.

There is currently no clear use case for broader lists of work requests.


Artifact URLs
=============

``/<scope>/artifact/<int:artifact_id>/``
----------------------------------------

This should move to ``/<scope>/<workspace>/artifact/<id>``, which would
simplify its implementation by sharing URL handling with workspace and
collection views, and make sense visually as it shares the same UI components
as the other views that are workspace-scoped.

``/<scope>/artifact/<int:artifact_id>/file/<int:file_in_artifact_id>/<path:path>``
----------------------------------------------------------------------------------

Likewise, move to ``/<scope>/<workspace>/artifact/<id>/file/<file_in_artifact_id>/<path:path>``

``/<scope>/artifact/<int:artifact_id>/raw/<int:file_in_artifact_id>/<path:path>``
---------------------------------------------------------------------------------

Likewise, move to ``/<scope>/<workspace>/artifact/<id>/raw/<file_in_artifact_id>/<path:path>``

``/<scope>/artifact/create/``
-----------------------------

This should move to ``/<scope>/<workspace>/artifact/create/``: that way it can
only show for workspaces that can be written to, get rid of the workspace field
in the form, and only upload to the workspace named in the URL.

``/<scope>/artifact/<int:artifact_id>/download/*``
--------------------------------------------------

This should move to ``/<scope>/<workspace>/artifact/<id>/download/*``

When the worker downloads an artifact, it already queries the API first to get
its download URL, so this can be moved without breaking clients, as long as the
API response is updated: see
``ArtifactSerializerResponse._build_absolute_download_url``.
