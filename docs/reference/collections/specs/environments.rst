.. collection:: debian:environments

Category ``debian:environments``
--------------------------------

.. todo::

   The definition of this category is not yet fully agreed.  We'll revisit
   it when we're closer to being able to try out an implementation so that
   we can see how the lookup mechanisms will work.

This collection represents a group of :artifact:`debian:system-tarball`
and/or :artifact:`debian:system-image` artifacts, such as the tarballs used
by build daemons across each suite and architecture.

In the short term, there will be one ``debian:environments`` collection per
distribution vendor with the collection name set to the name of the vendor
(e.g. "debian"), so that it can be looked up by the vendor's name.  This is
subject to change.

* Variables when adding items:

  * ``codename`` (optional): set the distribution version codename for this
    environment (defaults to the codename that the artifact was built for)
  * ``variant`` (optional): identifier indicating what kind of tarball or
    image this is; for example, an image optimized for use with autopkgtest
    might have its variant set to "autopkgtest"
  * ``backend`` (optional): name of the Debusine backend that this tarball
    or image is intended to be used by

* Data: none

* Valid items:

  * :artifact:`debian:system-tarball` artifacts
  * :artifact:`debian:system-image` artifacts

* Per-item data:

  * ``codename``: codename of the distribution version (copied from
    underlying artifact for ease of lookup and to preserve history, but may
    be overridden to reuse the same tarball for another distribution
    version)
  * ``architecture``: architecture name (copied from underlying artifact for
    ease of lookup and to preserve history)
  * ``variant``: an optional identifier indicating what kind of tarball or
    image this is; for example, an image optimized for use with autopkgtest
    might have its variant set to "autopkgtest"
  * ``backend``: optional name of the Debusine backend that this tarball or
    image is intended to be used by

* Lookup names:

  * Names beginning with ``match:`` look up current artifacts based on
    various properties; if more than one matching item is found then the
    most recently-added one is returned.  The remainder of the name is a
    colon-separated list of filters on per-item data, as follows:

    * ``format=tarball``: return only :artifact:`debian:system-tarball`
      artifacts
    * ``format=image``: return only :artifact:`debian:system-image`
      artifacts
    * ``codename=CODENAME``
    * ``architecture=ARCHITECTURE``
    * ``variant=VARIANT`` (``variant=`` without an argument matches items
      with no variant)
    * ``backend=BACKEND``

* Constraints:

  * there may be at most one active tarball or image respectively with a
    given vendor, codename, variant and architecture at a given time
