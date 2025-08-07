==================
Build instructions
==================

This page discusses various ways to control the package building process
in Debusine. The idea is to achieve at a minimum some level of parity with
features proposed by buildd and wanna-build. But ideally we want to go
further than that.

Many of the features rely on the management of a collection of
:ref:`task-configuration`. We refer to this as the *task-configuration
mechanism* in the text below.

Desired features
================

Control of architectures
------------------------

We want to be able to control the set of architectures being built for a
package.

This can be handled through ``architectures_allowlist`` and
``architectures_denylist`` options on the ``debian-pipeline`` workflow
and the task-configuration mechanism.

Host architecture for arch-all builds
-------------------------------------

We want to be able to control the architecture of the worker used to build
``Architecture: all`` packages. Currently, amd64 is hardcoded.

At some point, we will want the packages to be able to provide the value
directly. But for now, we want to be able to control that architecture
through the task-configuration mechanism.

The sbuild task doesn't require any change. We can already feed the
architecture of our choice in ``host_architecture`` and set
``build_components`` to ``all``.

Thus it's at the level of the debian-pipeline and sbuild workflows that we
need a new ``arch_all_host_architecture`` field.

Control the build backend and the associated build environment
--------------------------------------------------------------

We want to be able to control the build backend used by the worker, and
possibly also use an alternative build environment.

The debian-pipeline and sbuild workflows respectively have the
``sbuild_backend`` and ``backend`` options already. The sbuild workflow
also has an ``environment_variant`` option to select a specific
variant of the build environment. All those can be controlled through
the task-configuration mechanism.

We should add the same parameter at the debian-pipeline level for
convenience.

Control DEB_BUILD_PROFILES and DEB_BUILD_OPTIONS
------------------------------------------------

Those are very specific options that do not deserve to be controlled from
the debian-pipeline workflow level. However they should be usable at the
sbuild workflow and worker task level, so that the task-configuration
mechanism can be used to control them.

The sbuild workflow supports ``build_profiles`` but not ``build_options``.
The sbuild task supports both parameters, but doesn't implement the
``build_options`` one. ``build_profiles`` is defined as a list, but
``build_options`` as a string.

We should harmonize all this.

Control workers assigned for specific packages/tasks
----------------------------------------------------

Some packages have specific requirements and are known to fail on workers
that do not match those requirements. Typically small workers are not able
to build some heavy packages. Or some packages require a GPU for their
tests.

The current plan to address this is to implement a `tag-based
approach <https://salsa.debian.org/freexian-team/debusine/-/issues/326>`__.

To complement this, we need some way to define some specific requirements
while submitting the worker tasks. The proposal is to add a new
``worker_requirements`` field in ``task_data``. That way users that can run
individual work requests are able to specify their requirements, and for
tasks run through a workflow, it can be controlled through the
task-configuration mechanism.

.. note:: This design offers no way to merge user submitted requirements
   with the requirements provided through task-configuration.

.. note:: This design offers no way to merge worker requirements coming
   from different "configuration contexts". One can override lists from
   one level to the next, but not expand them.

Control packages tested during autopkgtest of reverse dependencies
------------------------------------------------------------------

We want to be able to restrict the set of autopkgtests which are
run as part of the ``reverse_dependencies_autopkgtest`` workflow:

* Some packages have a very large dependency tree, and we might not
  want to execute all of them. Instead we might want to rely on a
  representative subset.

* On the opposite, some packages might have autopkgtest that are
  resource hungry and that we'd rather not run as part of such a workflow
  (the tests would only run when the package itself gets updated, not when
  reverse dependencies get updated).

Both of those could be achieved with the task-configuration mechanism if
the workflow is extended with two new parameters — ``packages_allowlist``
and ``packages_denylist`` — listing respectively the set of source package
names whose tests can be scheduled and the set of source package names
whose tests should not be scheduled.

Proposed changes
================

Changes to the sbuild workflow
------------------------------

* Addition of the ``build_options`` option. Must be passed down to the
  sbuild task.
 
Changes to the sbuild task
--------------------------

* Change the schema of the ``build_options`` parameter to be a list like
  ``build_profiles`` instead of a string.
* Implement support of the ``build_options`` parameter by setting the
  ``DEB_BUILD_OPTIONS`` environment variable before calling sbuild. Note
  that when the ``build_profiles`` parameter is also provided and contains
  either ``nodoc`` or ``nocheck``, those values might be automatically
  added to ``DEB_BUILD_OPTIONS`` on top of the user-provided value.

Changes related to the worker requirements feature
--------------------------------------------------

* Add a new optional ``worker_requirements`` field in ``BaseTaskData`` as
  a list of strings.
* At some point when #326 gets implemented:

  * add a new method ``compute_worker_requirements()`` that can generate
    additional requirements based on the analysis of the task parameters
    (and of the available history if any)
  * expand ``TaskDatabase.configure()`` to transform values coming from
    the ``worker_requirements`` task_data field and from the new
    ``compute_worker_requirements()`` method into real relations with the
    new ``WorkerTag`` model (while respecting the various restrictions).
