.. _url-user-redesign-blueprint:

========================
URL redesign for /-/user
========================

The user-specific URL structure currently hardwire that views can only show
information about the current user.

This is an attempt to change the URL structure so that it contains the
username, and so that some views can show limited information about other
users.

Current URL structure
=====================

This is the current situation for ``/-/user/`` URLs::

  /-/user/logout/
  /-/user/token/
  /-/user/token/<int:pk>/delete/
  /-/user/token/<int:pk>/edit/
  /-/user/token/create/


Proposed URL structure
======================

This is the proposed URL structure::

  /-/logout/
  /-/user/<username>/token/
  /-/user/<username>/token/<int:pk>/delete/
  /-/user/<username>/token/<int:pk>/edit/
  /-/user/<username>/token/create/

Logout would be moved next to ``/-/login``, and it's the only view that both
only makes sense for the currently logged in user, and is delegating to
Django's logout view which assumes that.

The rest of the moves also make space for an upcoming user display view (see
`MR !1486 <https://salsa.debian.org/freexian-team/debusine/-/merge_requests/1486>`)::

  /-/user/<username>/


Permission checking
===================

Existing token views need to do permission checking to see if the currently
logged in user can manage tokens for the selected user.

By default we can mandate that this is only allowed if the currently logged in
user *is* the selected user, and this could leave the possibility open for
having things like admins or other figures be able to manage tokens for other
users.

Possible examples of this:

* Allow an admin to see what scopes (or similar) a user has tokens for, without
  showing any secrets, so that they can debug auth failures.
* Administratively revoke a token.
