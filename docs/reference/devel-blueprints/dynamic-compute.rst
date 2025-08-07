=============================
Dynamic cloud compute scaling
=============================

Image building
--------------

Debusine workers on cloud providers need a base image.  While this could be
a generic image plus some dynamic provisioning code, it's faster and more
flexible to have pre-built images that already contain the worker code and
only need to be given a token and a Debusine server API URL.

The process of building and publishing these images should eventually be a
Debusine task, but to start with it can be an ad-hoc script.  However, the
code should still be in the Debusine repository so that we can develop it
along with the rest of the code.

Image builds will need at least the following options (which might be
command-line options rather than this JSON-style design):

* ``source`` (string, optional): a deb822-style APT source to add; for
  example, this would allow using the `latest debusine-worker development
  build <https://freexian-team.pages.debian.net/debusine/repository/>`__
  rather than the version of ``debusine-worker`` in the base image's default
  repositories
* ``enable_backends`` (list of strings, defaults to ``["unshare"]``):
  install and configure packages needed for the given executors
* ``enable_tasks`` (list of strings, defaults to ``["autopkgtest",
  "sbuild"]``): install packages needed for the given tasks; most tasks do
  not need explicit support during provisioning, but ``autopkgtest``,
  ``mmdebstrap``, ``sbuild``, and ``simplesystemimagebuild`` do

The initial image building code can be derived based on Freexian's current
Ansible setup for the ``debusine_worker`` role.

Scheduler
---------

The scheduler currently checks worker metadata on a per-worker basis to
decide whether a worker can run a given work request.  This was already a
potential optimization target, but it will need to be optimized to support
deciding whether to provision dynamic workers, since the decision will need
to be made in bulk for many workers at once.  Instead of the current
``can_run_on`` hook that runs for a single work request, tasks will need
some way to provide Django query conditions selecting the workers that can
run them, relying on worker tags.

Once we have task statistics, we are likely to want to select workers (or
worker pools) that have a certain minimum amount of disk space or memory.
It may be sufficient to have a small/medium/big classification, but a
clearer approach would be for some tags to have a numerical value so that
work requests can indicate the minimum value they need.  These would be a
natural fit for worker pools: ``WorkerPool.specifications`` will usually
already specify instance sizes in some way, and therefore
``WorkerPool.tags`` can also communicate those sizes to the scheduler.

User interface
--------------

Add a indication to ``/-/status/workers/`` showing each worker's pool.

Make each worker on ``/-/status/workers/`` be a link to a view for that
single worker.  Where available, that view includes information from the
corresponding ``WorkerPool``, excluding secret details of the provider
account.

Exclude inactive dynamic workers from ``/-/status/workers/``, to avoid
flooding users with irrelevant information.
