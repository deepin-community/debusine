.. _package-repositories:

====================
Package repositories
====================

Debusine is in the process of gaining the ability to host package
repositories.  These work by including the necessary index files in
:collection:`debian:suite` collections.  When the packages in a suite
changes, the :workflow:`update_suites` workflow generates updated indexes
for it.  Debusine can serve all the relevant files in an `APT-compatible
layout <https://wiki.debian.org/DebianRepository/Format>`__.

Snapshots
=========

It is useful to serve historical snapshots as well as the current state of
the archive, as long as Debusine still retains the necessary data under
whatever retention policies are in effect.  The :ref:`collection data model
<collection-data-models>` already includes timestamps for when collection
items were created or removed, and supports :ref:`retaining
<explanation-collection-item-retention>` items even after they are no longer
active in their parent collection; as a result, repository indexes at a
particular timestamp can be found by querying for collection items
containing indexes that were not created after that timestamp and not
removed before that timestamp.

Overrides
=========

Overrides are used by Debian's traditional archive management software to
store the component, section, and (for binary packages) priority of each
package.  (Ubuntu's archive management software also supports `phased update
percentages
<https://wiki.debian.org/DebianRepository/Format#Phased-Update-Percentage>`__,
which are handled by overrides; other extensions are possible.)

While the name "override" might suggest that these are only applied where
the values are something other than the ones supplied by the package, in
fact every package in the archive has overrides even if those are equal to
the ones supplied by the package.  In Debian's traditional archive
management software, uploads of packages without overrides go into the
``NEW`` queue for manual review.

Review workflows of this kind are not yet in scope, but we already have
per-item data for component, section, and priority in the
:collection:`debian:suite` collection which represent the common set of
overrides and are considered when generating ``Packages`` and ``Sources``
files.  Debian also publishes override summaries in an ``indices``
directory; we treat these as just another kind of repository index file,
although they are stored at the archive level rather than at the suite
level.

Access views
============

Domains
-------

Since Debusine's ``Set-Cookie`` header doesn't include a ``Domain``
attribute, its cookies are not available to subdomains.  It should therefore
be safe to run repositories from a subdomain of ``DEBUSINE_FQDN``: content
served from that subdomain will not be able to run session fixation attacks
on its parent domain.

A reasonable default is ``deb.{DEBUSINE_FQDN}``: shorter than
``repositories``, not protocol-specific like ``ftp``, and familiar from
``deb.debian.org``.  However, we include the Django setting
``DEBUSINE_DEBIAN_ARCHIVE_FQDN`` to allow instances to override this.

Repositories will normally be served over HTTPS, but public repositories may
also be served over HTTP.  This is sometimes useful for bootstrapping
minimal environments that lack certificates.

URL paths
---------

The basic path to a repository is ``/{scope}/{workspace}``, relying on the
fact that archive collections are :ref:`singletons <collection-singleton>`.
Suites in the archive are mapped onto subdirectories of ``dists/`` as usual.

.. todo::

    In future, we might also provide the ability to map particular
    repositories onto different URLs.

`by-hash
<https://wiki.debian.org/DebianRepository/Format#indices_acquisition_via_hashsums_.28by-hash.29>`__
paths are handled specially: they are translated into a search for a
repository index in the correct suite with the correct path and checksum.
That repository index need not be the active index for its path in the
suite; it can be served as long as the suite's
``full_history_retention_period`` has not expired.

``/{scope}/{workspace}/{timestamp}``, where ``timestamp`` has the format
``YYYYMMDDTHHMMSSZ``, e.g. ``20250529T133100Z`` for 13:31 UTC on 29 May
2025, addresses the state of the repository as it existed at ``timestamp``.
This is done by looking up collection items that have ``created_at`` and
``removed_at`` fields bracketing the given timestamp, rather than just those
that are active (with ``removed_at`` unset), and can be done as long as the
suite's ``full_history_retention_period`` has not expired.  This URL format
is compatible with snapshot.debian.org and snapshot.ubuntu.com; like those
services, a user can request any timestamp as a snapshot ID, and the server
will provide the indexes and other files that were current at that point in
time.

.. todo::

    Add a `Snapshots
    <https://wiki.debian.org/DebianRepository/Format#Snapshots>`__ field to
    generated ``Release`` files, pointing to the corresponding URL format.

.. todo::

    Once we've defined how :issue:`repository signing <756>` works, we
    should also publish each repository's public keys in some standard
    location.

Authentication and authorization
--------------------------------

APT only handles HTTP Basic Authentication, so we're stuck with that
mechanism, but we can still use our existing user tokens: users may use
their user name with their user token acting as a password.  Downloading
files from a repository requires the "viewer" role on the workspace.

Cache control
-------------

Responses to different URLs in a repository should be cached in different
ways, and Debusine sends the ``Cache-Control`` response header to inform
HTTP caches of this.

``by-hash`` files under ``dists/``, and all files resulting from a
timestamp-specific query are immutable: they may be removed, but they won't
be replaced with different contents at the same URL.  This is also true for
files under ``pool/`` in archives where ``may_reuse_versions`` is false.
For these files, Debusine sends ``Cache-Control: max-age=31536000`` (i.e.
365 days, an arbitrary long period).

For other files, Debusine sends ``Cache-Control: max-age=1800,
proxy-revalidate`` to indicate that shared caches should revalidate
responses before reuse if they are older than half an hour.  (This period is
also arbitrary, but should be a good starting point.)

Debusine also sends ``Vary: Authorization`` for all responses to repository
URLs, since its responses depend on whether the requester has the "viewer"
role on the workspace.

Examples
--------

If we were to create an archive collection in ``debian/base`` on
debusine.debian.net and generate indexes for its suites, then APT
configuration for it might look something like this::

    Types: deb deb-src
    URIs: https://deb.debusine.debian.net/debian/base
    Suites: bookworm
    Components: main contrib non-free non-free-firmware

A snapshot of it from the start of May 2025 might be as follows (in this
case we have to add the snapshot ID to the URL, as we don't control the
``Release`` file and can't add a ``Snapshots`` field to it)::

    Types: deb deb-src
    URIs: https://deb.debusine.debian.net/debian/base/20250501T000000Z
    Suites: bookworm
    Components: main contrib non-free non-free-firmware

An experiment workspace based on ``debian/developers`` might look like
this::

    Types: deb deb-src
    URIs: https://deb.debusine.debian.net/debian/developers-test
    Suites: sid
    Components: main

Or a snapshot, once Debusine's generated ``Release`` files gain a
``Snapshots`` field::

    Types: deb deb-src
    URIs: https://deb.debusine.debian.net/debian/developers-test
    Snapshot: 20250501T000000Z
    Suites: sid
    Components: main
