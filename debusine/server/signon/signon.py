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
from django.contrib import auth
from django.contrib.auth import load_backend
from django.contrib.auth.hashers import make_password
from django.core.exceptions import PermissionDenied, ValidationError

from debusine.db.models import Identity, User
from debusine.server.signon import providers
from debusine.server.signon.auth import SignonAuthBackend
from debusine.server.signon.signon_utils import split_full_name

log = logging.getLogger("debusine.server.signon")


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

    def activate_identity(self, identity: Identity, *options: str) -> None:
        """Activate the given identity and update authentication accordingly."""
        if self.request.user.is_authenticated:
            if "bind" not in options:
                raise PermissionDenied("user is already logged in")
            # Bind the current user to the identity
            log.info("%s: auto associated to %s", self.request.user, identity)
            identity.user = self.request.user
            identity.save()
        elif identity.user is None:
            if (user := self._map_identity_to_user(identity)) is not None:
                log.debug("logging in autocreated user %s", user)
                self.request.user = user
                auth.login(
                    self.request,
                    user,
                    backend="debusine.server.signon.auth.SignonAuthBackend",
                )
        else:
            log.debug("logging in user %s", identity.user)
            auth.login(
                self.request,
                identity.user,
                backend="debusine.server.signon.auth.SignonAuthBackend",
            )

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

    def _map_identity_to_user(self, identity: Identity) -> User | None:
        """Create a user model from the given identity, and bind it to it."""
        # Validate identity claims
        if not (provider := self.get_provider_for_identity(identity)):
            return None
        if claim_errors := provider.validate_claims(identity):
            for error in claim_errors:
                log.warning("%s", error)
            return None

        # First lookup an existing user
        user = self._lookup_user_from_identity(identity)
        if user is not None:
            log.info("%s: user matched to identity %s", user, identity)
        else:
            # Else try to create a new user
            user = self.create_user_from_identity(identity)
            if user is not None:
                log.info("%s: auto created from identity %s", user, identity)
            else:
                return None

        # A user was found or created: bind it to the identity
        identity.user = user
        identity.save()
        log.info("%s: bound to identity %s", user, identity)
        return user

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
        Lookup or create a user from the data in an Identity.

        This is a default implementation. It can be customized by subclassing
        Signon and using settings.SIGNON_CLASS
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
