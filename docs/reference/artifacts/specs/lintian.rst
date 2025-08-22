.. artifact:: debian:lintian

Category ``debian:lintian``
===========================

* Files:

  * ``lintian.txt``: the raw (unfiltered) lintian output
  * ``analysis.json``: the details about all the tags discovered (in a
    top-level ``tags`` key), some statistics/summary (in a top-level
    ``summary`` key) and a ``version`` key with the value ``1.0`` if the
    content follows the (initial) JSON structure described below.

* Data:

  * ``architecture``: ``source`` for a source package analysis, ``all`` for
    a binary-all analysis, or the architecture of the relevant binary
    package artifacts for a binary-any analysis (the task analyzes all its
    artifacts together, but then creates a separate ``debian:lintian``
    artifact for each architecture)
  * ``summary``: a duplicate of the ``summary`` key in ``analysis.json``

* ``analysis.json`` structure:

  * ``version``: always ``1.0``

  * ``summary``: a dictionary with the following keys:

    * ``tags_count_by_severity``: a dictionary with a sub-key for each of
      the possible severities documenting the number of tags of the
      corresponding severity that have been emitted by lintian
    * ``package_filename``: a dictionary mapping the name of the
      binary or source package to its associated filename (will be a single
      key dictionary for the case of a source package lintian analysis, and a
      multiple keys one for the case of an analysis of binary packages)
    * ``tags_found``: the list of non-overridden tags that have been found
      during the analysis
    * ``overridden_tags_found``: the list of overridden tags that have been
      found during the analysis
    * ``lintian_version``: the lintian version used for the analysis
    * ``distribution``: the distribution in which lintian has been run
 
  * ``tags``: a sorted list of tags where each tag is represented with a
    dictionary. The list is sorted by the following criteria:

    * binary package name in alphabetical order (if relevant)
    * severity (from highest to lowest)
    * tag name (alphabetical order)
    * tag details (alphabetical order)

    Each tag is represented with the following fields:

    * ``tag``: the name of the tag
    * ``severity``: one of the possible severities (see below for full list)
    * ``package``: the name of the binary or source package (there is no risk
      of confusion between a source and a binary of the same name as the artifact
      with the analysis is dedicated either to a source packages or to a set
      of binary packages, but not to both at the same time)
    * ``note``: the details associated to the tag (those are printed after
      the tag name in the lintian output)
    * ``pointer``: the optional part shown between angle brackets that gives a
      specific location for the issue (often a filename and a line number)
    * ``explanation``: the long description shown after a tag with ``--info``,
      aka the lines prefixed with ``N:`` (they always start and end with an
      empty line)
    * ``comment``: the maintainer's comment shown on lines prefixed with ``N:``
      just before a given overridden tag (those lines can be identified by the
      lack of an empty line between them and the tag)

.. note::

   Here's the ordered list of all the possible severities (from highest
   to lowest):

   * ``error``
   * ``warning``
   * ``info``
   * ``pedantic``
   * ``experimental``
   * ``overridden``
   * ``classification``

   Note that ``experimental`` and ``overridden`` are not true tag
   severities, but lintian's output replaces the usual severity field
   for those tags with ``X`` or ``O`` and it is thus not easily possible
   to capture the original severity.

   And while ``classification`` is implemented like a low-severity issue,
   those tags do not represent real issues, they are just a convenient way
   to export data generated while doing the analysis.


* Relationships:

  * ``relates-to``: the corresponding artifacts that have been analyzed.
    They can be of type :artifact:`debian:source-package`,
    :artifact:`debian:binary-package`, :artifact:`debian:binary-packages`,
    or :artifact:`debian:upload`.
