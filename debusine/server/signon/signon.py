# Copyright 2020-2023 Enrico Zini <enrico@debian.org>
# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Logic to authenticate a request using signon Providers."""
import functools
import logging
from collections.abc import Iterable, Sequence

import django.http
from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import backends, load_backend
from django.contrib.auth.hashers import make_password
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)

from debusine.db.models import Group, Identity, User
from debusine.server.signon import providers
from debusine.server.signon.auth import SignonAuthBackend
from debusine.server.signon.signon_utils import split_full_name

log = logging.getLogger("debusine.server.signon")


class MapIdentityFailed(Exception):
    """Exception raised when unable to map an identity to a user."""


class Signon:
    """
    Backend used to interact with external authentication providers.

    This is setup by SignonMiddleware as request.signon.

    The constructor needs to be as lightweight as possible, as it is called on
    every request. Everything else is loaded only when needed.
    """

    def __init__(self, request: django.http.HttpRequest) -> None:
        """Create a Signon object for a request."""
        self.request = request
        self.providers: Sequence[providers.Provider] = getattr(
            settings, "SIGNON_PROVIDERS", ()
        )

    def status(
        self,
    ) -> Iterable[tuple[providers.BoundProvider, Identity | None]]:
        """
        Query the status of remote authentication providers.

        :returns: an iterable of ``(bound_provider, identity | None)``
        """
        for provider in self.providers:
            bound = provider.bind(self.request)
            identity = self.identities.get(provider.name)
            yield bound, identity

    @functools.cached_property
    def identities(self) -> dict[str, Identity]:
        """Lazily populate self.identities."""
        return self._compute_identities()

    def logout_identities(self) -> None:
        """Deactivate all active external identities."""
        for provider in self.providers:
            bound = provider.bind(self.request)
            bound.logout()
        self._remove_invalid_signon_user()

    def _compute_identities(self) -> dict[str, Identity]:
        """
        Instantiate valid Identity entries for this request.

        Delegate Provider objects with looking up valid Identity objects from
        the current request.
        """
        identities = {}

        for provider in self.providers:
            pk = self.request.session.get(f"signon_identity_{provider.name}")
            if pk is None:
                continue

            try:
                identity = Identity.objects.get(pk=pk, issuer=provider.name)
            except Identity.DoesNotExist:
                # If the session has a broken Identity ID, remove it
                del self.request.session[f"signon_identity_{provider.name}"]
                continue

            identities[provider.name] = identity

        return identities

    def validate_claims(
        self, provider: providers.Provider, identity: "Identity"
    ) -> list[str]:
        """
        Check if the claims in identity are acceptable.

        This is the function that decides whether to accept or reject a remote
        login. It provides a generic default implementation, and can be
        overridden by the class set in ``SIGNON_CLASS`` to implement more
        complex checks.

        By default, it honors the ``restrict`` configuration in ``GitLab``
        providers, for ``email_verified`` and ``group:<groupname>``. For
        example::

            SIGNON_PROVIDERS = [
                providers.ProviderClass(
                    # Restrict local user creation to users with these matching
                    # claims
                    restrict=("email-verified", "group:debian"),
                )
            ]

        :return: a list of error messages, or an empty list if validation passed
        """
        res = []
        if isinstance(provider, providers.OIDCProvider):
            if isinstance(provider, providers.GitlabProvider):
                for restriction in provider.restrict:
                    if restriction == "email-verified":
                        # Work only with verified emails
                        if not identity.claims.get("email_verified", False):
                            res.append(
                                f"identity {identity}"
                                " does not have a verified email"
                            )
                    elif restriction.startswith("group:"):
                        group_name = restriction[6:]
                        if group_name not in identity.claims.get(
                            "groups_direct", ()
                        ):
                            res.append(
                                f"identity {identity}"
                                f" is not in group {group_name!r}"
                            )
                    else:
                        raise ImproperlyConfigured(
                            f"unsupported signon restriction: {restriction!r}"
                        )
        return res

    def identity_bind_to_current_user(self, identity: Identity) -> None:
        """Bind the identity to the current user."""
        # Checked by activate_identity
        assert self.request.user.is_authenticated
        log.info("%s: auto associated to %s", self.request.user, identity)
        identity.user = self.request.user
        identity.save()

    def identity_login_bound_user(self, identity: Identity) -> None:
        """Log in the user already bound to the identity."""
        # Checked by activate_identity
        assert identity.user is not None
        auth_backend_name = "debusine.server.signon.auth.SignonAuthBackend"
        backend = auth.load_backend(auth_backend_name)
        assert isinstance(backend, backends.ModelBackend)
        if not backend.user_can_authenticate(identity.user):
            raise PermissionDenied("user is not allowed to log in")

        log.debug("logging in user %s", identity.user)
        auth.login(
            self.request,
            identity.user,
            backend=auth_backend_name,
        )

    def identity_create_user(self, identity: Identity) -> None:
        """Attempt to create and bind a user for the identity."""
        # Validate identity claims
        if not (provider := self.get_provider_for_identity(identity)):
            raise MapIdentityFailed(
                f"Invalid provider {identity.issuer!r} in identity"
            )
        if claim_errors := self.validate_claims(provider, identity):
            for error in claim_errors:
                log.warning("%s", error)
            raise MapIdentityFailed(
                "Identity claims did not validate: " + "; ".join(claim_errors)
            )

        # First lookup an existing user
        user = self._lookup_user_from_identity(identity)
        if user is not None:
            log.info("%s: user matched to identity %s", user, identity)
        else:
            # Else try to create a new user
            user = self.create_user_from_identity(identity)
            if user is not None:
                log.info("%s: auto created from identity %s", user, identity)
                self.setup_new_user(user, identity)
            else:
                raise MapIdentityFailed(
                    f"Cannot create a local user for {identity}"
                )

        # A user was found or created: bind it to the identity
        identity.user = user
        identity.save()
        log.info("%s: bound to identity %s", user, identity)

    def activate_identity(self, identity: Identity, *options: str) -> None:
        """Activate the given identity and update authentication accordingly."""
        if self.request.user.is_authenticated:
            if "bind" not in options:
                raise PermissionDenied("user is already logged in")
            self.identity_bind_to_current_user(identity)
        elif identity.user is None:
            try:
                self.identity_create_user(identity)
            except MapIdentityFailed as exc:
                messages.add_message(
                    self.request, messages.ERROR, f"Login failed: {exc}"
                )
            else:
                self.identity_login_bound_user(identity)
        else:
            self.identity_login_bound_user(identity)

        self.request.session[f"signon_identity_{identity.issuer}"] = identity.pk

    def _remove_invalid_signon_user(self) -> None:
        """
        Log out an externally authenticated user.

        This is used to invalidate credentials in case a consistency check
        failed between active identities.

        Log out only happens if the user was authenticated via SignonMiddleware
        """
        try:
            stored_backend = load_backend(
                self.request.session.get(auth.BACKEND_SESSION_KEY, '')
            )
        except ImportError:
            # backend failed to load
            auth.logout(self.request)
        else:
            if isinstance(stored_backend, SignonAuthBackend):
                auth.logout(self.request)

    def get_provider_for_identity(
        self, identity: Identity
    ) -> providers.Provider | None:
        """Find the Provider for an identity."""
        for provider in self.providers:
            if provider.name == identity.issuer:
                return provider

        log.warning(
            "identity %s has unknown issuer %s", identity, identity.issuer
        )
        return None

    def _lookup_user_from_identity(self, identity: Identity) -> User | None:
        """Lookup an existing user from claims in an Identity."""
        User = auth.get_user_model()
        try:
            return User.objects.get(email=identity.claims["email"])
        except User.DoesNotExist:
            return None

    def create_user_from_identity(self, identity: Identity) -> User | None:
        """
        Create a user from the data in an Identity.

        This is the function that creates a local user after the first
        successful login with a remote identity, and no matching user is found
        that already exists.

        This is only called if the identity has passed ``validate_claims``
        checks, so those checks can be assumed as invariants.
        """
        User = auth.get_user_model()
        first_name, last_name = split_full_name(identity.claims["name"])

        # Django does not run validators on create_user, so garbage in the
        # claims can either create garbage users, or cause database transaction
        # errors that will invalidate the current transaction.
        #
        # See: https://stackoverflow.com/questions/67442439/why-django-does-not-validate-email-in-customuser-model  # noqa: E501

        # Instead of calling create_user, I instead have to replicate what it
        # does here and call validation explicitly before save.

        # This is the equivalent of the following, with validation:
        # user = User.objects.create_user(
        #     username=identity.claims["email"],
        #     first_name=first_name,
        #     last_name=last_name,
        #     email=identity.claims["email"],
        # )
        email = User.objects.normalize_email(identity.claims["email"])
        username = User.normalize_username(identity.claims["email"])
        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
        )
        user.password = make_password(None)

        try:
            user.clean_fields()
        except ValidationError as e:
            log.warning("%s: cannot create a local user", identity, exc_info=e)
            return None

        user.save()
        return user

    def setup_new_user(self, user: User, identity: Identity) -> None:
        """
        Set up a newly created user.

        This is only called if the identity has passed ``validate_claims``
        checks, and a new user has just been created.

        By default it provides a generic implementation, and can be overridden
        by the class set in ``SIGNON_CLASS`` to implement more complex
        behaviours.

        The default implementation honors the ``add_to_group`` option set in
        the Provider instance. For example::

            SIGNON_PROVIDERS = [
                providers.ProviderClass(
                    options={
                        "add_to_group": {
                            # Map gitlab group names to Debusine groups
                            "gitlab/group": "scopename/groupname",
                            # Map nm.debian.org statuses to Debusine groups
                            "nm:dm": "scopename/groupname",
                            "nm:dm_ga": "scopename/groupname",
                        },
                    }
                )
            ]
        """
        if (provider := self.get_provider_for_identity(identity)) is None:
            return

        if (add_to_group := provider.options.get("add_to_group", None)) is None:
            return

        self.add_user_to_groups(user, identity, add_to_group)

    def add_user_to_groups(
        self, user: User, identity: Identity, add_to_group: dict[str, str]
    ) -> None:
        """Add a user to groups according to ``add_to_group`` rules."""
        gitlab_groups = identity.claims.get("groups_direct", ())
        for remote_group, debusine_group in add_to_group.items():
            if remote_group not in gitlab_groups:
                continue
            self.add_user_to_group(user, debusine_group)

    def add_user_to_group(self, user: User, scoped_name: str) -> None:
        """Add a user to the named ``scope/group`` group."""
        try:
            group = Group.objects.from_scoped_name(scoped_name)
        except Group.DoesNotExist:
            raise ImproperlyConfigured(f"Group {scoped_name!r} not found")
        group.users.add(user)
