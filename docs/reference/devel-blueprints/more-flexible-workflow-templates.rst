More flexible workflow templates
================================

:ref:`Workflow templates <explanation-workflows>` can specify a subset of
the input parameters for their workflow that cannot be overridden by the
user starting the workflow; the parameters not set in the workflow template
are called run-time parameters, and can be provided by the user.

This is adequate for some use cases, and it is a good fit for situations
where some of the input parameters are security-sensitive.  However, it is
not enough for others.  For example:

* Create a :workflow:`debian_pipeline` workflow template that defaults to
  uploading the package to an upload target, but allow the user to skip the
  upload step
* Default ``autopkgtest_backend`` to ``unshare``, but allow the user to
  select another backend, perhaps with some restrictions (compare
  :issue:`803`)
* Forbid setting a particular parameter, while relying on the workflow's
  default value rather than needing to specify one
* Ensure that changes to the implementation of a workflow don't unexpectedly
  make new parameters available

Explicit runtime parameters
---------------------------

Most of these requirements can be satisfied by adding a new
``runtime_parameters`` field to ``WorkflowTemplate``.  This contains a
dictionary mapping ``task_data`` parameter names to lists of allowed choices
for each parameter, or to None.  When starting a workflow, a user may only
set a ``task_data`` parameter if that parameter appears in
``runtime_parameters``, and if the value is one of the allowed choices for
that parameter.

Setting the allowed choices for a parameter to the string ``any`` (as
opposed to the normal list of strings) allows users to set that parameter to
any value; similarly, setting the entire ``runtime_parameters`` field to the
string ``any`` (as opposed to the normal dictionary) allows users to set any
parameter to any value.

Since this weakens the connection between ``WorkflowTemplate.task_data`` and
``WorkRequest.task_data``, we rename ``WorkflowTemplate.task_data`` to
``WorkflowTemplate.static_parameters``.

A data migration sets ``runtime_parameters`` for each workflow template to
``field: None`` for each field in the given workflow's task data model that
is not set in the template's ``static_parameters``.  This preserves the
previous behaviour of workflow templates.

After this change, workflow templates will need to be explicitly adjusted to
make new parameters available, unless they use ``runtime_parameters: None``.

Examples
~~~~~~~~

A template with an overridable ``enable_upload`` parameter, providing a
default but also allowing the user to set it when starting a workflow:

.. code-block:: yaml

    static_parameters:
      enable_upload: true
    runtime_parameters:
      enable_upload: "any"

For a task with ``vendor``, ``codename``, and ``backend`` parameters, a
template that sets ``vendor`` without allowing the user to override it,
allows ``codename`` to be set by the user to one of a limited set of values,
and relies on the workflow's default value for ``backend`` without allowing
the user to set it:

.. code-block:: yaml

    static_parameters:
      vendor: "debian"
    runtime_parameters:
      codename: ["bookworm", "trixie"]

A template that allows setting ``boring_parameter``, but if an
``amazing_parameter`` option is later added to its target workflow then it
will not allow setting it unless ``runtime_parameters`` is changed:

.. code-block:: yaml

    runtime_parameters:
      boring_parameter: "any"

Possible future work
--------------------

Nested structures
~~~~~~~~~~~~~~~~~

Counterintuitively, only top-level keys are considered while merging
workflow template and user data: lists and dictionaries are not merged, and
if a list or a dictionary exists in both the workflow template and the user
data, the version in the user data is ignored.  Most of our current
workflows have been designed to take this into account, but that requires a
flat structure for workflow parameters that can be a little overwhelming.

However, changing this would have other implications.  ``dput-ng`` profiles
have similar nesting limitations, which caused us to unnest
Debusine-specific entries there in :mr:`1769`, and adding nesting within
``debusine_workflow_data`` would reintroduce that difficulty.

Also, the current flat structure suggests the possibility of a web UI to
start a workflow that offers each of the available parameters as options and
introspects the corresponding Pydantic models to pick the appropriate
widgets; it becomes less obvious how to do this if we increase nesting.

Additional variable templating
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some workflow task data entries are naturally constructed from other
entries: for example, collection names can be constructed based on
vendor/codename.  A possible direction would be to add a template language
that can build template data based on some kind of variable expansion pass,
in order that only a minimal set of variables needs to be presented as
options to the user.

However, adding templates to generate templates carries intrinsic risk of
confusion, and many of the same benefits can be gained by more thoughtful
workflow design: workflow orchestrators are free to do their own
computations when building values for child work requests, and they can do
so in ways that would be difficult to express in a template language, such
as looking up state in the database.  For now, it seems best to defer this
until (if ever) we can design a domain-specific language powerful enough to
express everything we do in workflow orchestrators, at which point something
like this might be a good fit for it.

autopkgtest parameter choice restrictions
-----------------------------------------

A workspace administrator might want to allow setting
``autopkgtest_backend`` to ``unshare`` or ``incus-lxc`` but not to
``incus-vm`` or ``qemu``, for resource consumption reasons.

However, this particular case cannot be handled solely at the workflow
template level.  ``autopkgtest_backend: auto`` is currently synonymous with
``autopkgtest_backend: unshare``, but for :issue:`803` it would be useful to
have it be sensitive to whether the autopkgtest in question declares the
``isolation-container`` or ``isolation-machine`` restriction and use the
simplest possible backend that will work.

To make that work, the task itself would need to be responsible for deciding
what ``auto`` means.  Merging template and user task data happens before the
task runs, so neither new ``WorkflowTemplate`` fields nor the current
:ref:`task configuration <task-configuration>` mechanism can easily express
this restriction.

A new ``allow_backends`` parameter seems tempting, but there are some
problems.  The configuration context for the :task:`Autopkgtest` task is
currently just the codename, so we can't use that for architecture-specific
configuration and would need to complicate the task with explicit
by-architecture parameters.  This may be a reason to add multiple
configuration contexts.

We also need to handle this sort of backend selection at the workflow level
rather than the task level, since the decision of which worker to dispatch
the task to may depend on which backend is selected.  That introduces the
difficulty that the workflow doesn't currently have access to the
``Restrictions`` declared by a given source package's tests, and `that
information isn't in the .dsc <https://bugs.debian.org/847926>`__; it would
have to unpack the source package to find those, which would have to be done
on a worker since running ``dpkg-source -x`` on untrusted source packages
isn't safe, and then cache the result somewhere.
