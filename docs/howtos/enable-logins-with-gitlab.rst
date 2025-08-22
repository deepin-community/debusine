.. _configure-gitlab-sso:

==================================
Enable logins with GitLab accounts
==================================

Introduction
------------

Debusine supports OpenID Connect authentication: these are the instructions for
setting up authentication against a GitLab server.

Set up
------

Configure the provider in your Debusine instance
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In your local settings:

.. code-block:: python3

    from debusine.project.project_utils import read_secret_key
    from debusine.server.signon import providers

    SIGNON_PROVIDERS = [
        # Example using salsa.debian.org
        providers.GitlabProvider(
            # Provider name to use in the Redirect URI
            name="salsa",
            # User-visible name
            label="Salsa",
            # Optional icon (path under the static directory)
            icon="signon/gitlabian.svg",
            # URL to the GitLab instance
            url="https://salsa.debian.org",
            # OIDC parameters
            client_id="<to be filled with GitLab-provided Application ID>",
            client_secret=read_secret_key("/etc/debusine/gitlab-app-secret"),
            scope=("openid", "profile", "email"),
            # Restrict local user creation to users with these matching claims
            restrict=("email-verified", "group:debian"),
        ),
    ]

Create ``/etc/debusine/gitlab-app-secret`` with the application secret provided
by GitLab and make sure the file is only readable by the ``debusine-server`` user::

    echo "<to be filled with Gitlab-provided Secret>" > /etc/debusine/gitlab-app-secret
    chmod 0600 /etc/debusine/gitlab-app-secret
    chown debusine-server:debusine-server /etc/debusine/gitlab-app-secret

Create the application in GitLab
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Go to your profile preferences, under "Applications"
2. Create a new application:

   1. Tick "Confidential" checkbox
   2. Select scopes: "openid", "profile", "email"
   3. Redirect URI: ``https://your.debusine.server/accounts/accounts/oidc_callback/$PROVIDER_NAME/``
      (note that https is required, and this cannot be deployed over plain http)
   4. Copy the Application ID and the Secret to your Debusine local settings

Restart Debusine
~~~~~~~~~~~~~~~~

When everything is set up, restart ``debusine-server``::

    systemctl restart debusine-server

In the login page you should now see the option to log in using the provider
you configured.

User mapping
------------

Remote users are matched to local users according to their `email` claim; if
there is no matching local user, a new one is created with the remote-provided
account information, as long as it respects the ``restrict`` setting.

Settings
--------

SIGNON_PROVIDERS
~~~~~~~~~~~~~~~~

List of configured remove providers, as instances of
``debusine.server.signon.providers.Provider`` subclasses. Please refer to the
documentation of individual subclasses for details.

GitlabProvider.restrict
~~~~~~~~~~~~~~~~~~~~~~~

Configures restrictions for matching remote users to local accounts.

Restrictions match the claims provided by the remote authentication provider,
and a user whose claims do not match the restriction cannot be automatically
mapped to a local user.

``restrict`` can be set to a sequence of strings, each specifying a
restriction:

* ``group:name``: the given group name must be present
* ``email-verified``: the primary email needs to be reported as verified

All listed restrictions must be met (i.e. they are AND-ed together).

Example::

  restrict=["group:debian", "email-verified"],


Provider-specific behaviour
===========================

If the current options available to control external login providers are not
sufficient, one can extend the ``Signon`` class and set it as the
``SIGNON_CLASS`` setting. See ``debusine.server.signon.sites.DebianSignon``
documentation for an example.
