# Copyright © The Debusine Developers
# See the AUTHORS file at the top-level directory of this distribution
#
# This file is part of Debusine. It is subject to the license terms
# in the LICENSE file found in the top-level directory of this
# distribution. No part of Debusine, including this file, may be copied,
# modified, propagated, or distributed except according to the terms
# contained in the LICENSE file.

"""Tests for the auth views."""
from datetime import timedelta
from textwrap import dedent
from typing import Any, ClassVar
from urllib.parse import urlencode

import lxml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.response import SimpleTemplateResponse
from django.test import override_settings
from django.urls import reverse
from rest_framework import status

from debusine.db.context import context
from debusine.db.models import Group, Token, User, auth
from debusine.db.models.auth import GroupAuditLog, GroupMembership, Identity
from debusine.db.playground import scenarios
from debusine.server.signon.providers import Provider
from debusine.test.django import (
    AllowAll,
    DenyAll,
    TestCase,
    TestResponseType,
    override_permission,
)
from debusine.web.views.auth import UserDetailView
from debusine.web.views.tests.utils import (
    ViewTestMixin,
    date_format,
    html_check_icon,
)


class LoginViewTests(TestCase):
    """Tests for the LoginView class."""

    def test_get(self) -> None:
        """Test plain get, no next= argument."""
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.session["sso_callback_next_url"], "/")

    def test_get_valid_next(self) -> None:
        """Test plain get, valid next= argument."""
        response = self.client.get(reverse("login") + "?next=/foo")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.session["sso_callback_next_url"], "/foo")

    def test_get_invalid_next(self) -> None:
        """Test plain get, invalid next= argument."""
        response = self.client.get(
            reverse("login") + "?next=https://example.com"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.session["sso_callback_next_url"], "/")

    def test_successful_login(self) -> None:
        """Successful login redirect to the homepage with authenticated user."""
        username = "testuser"
        password = "testpassword"

        get_user_model().objects.create_user(
            username=username, password=password
        )

        response = self.client.post(
            reverse("login"),
            data={"username": username, "password": password},
        )

        self.assertRedirects(response, reverse("homepage:homepage"))
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_nexturl_in_session(self) -> None:
        """Login leaves the next url in the session."""
        response = self.client.get(
            reverse("login") + "?next=foo",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.session["sso_callback_next_url"], "foo")


class LogoutViewTests(TestCase):
    """Tests for the logout view."""

    def test_successful_render(self) -> None:
        """
        Logout template is rendered.

        The view is implemented (and tested) as part of Django. The template
        is implemented by Debusine, and it's rendered here to assert that
        no missing template variables and contains the expected strings.
        """
        response = self.client.post(reverse("logout"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class UserDetailViewTests(ViewTestMixin, TestCase):
    """Test for UserDetailView class."""

    scenario = scenarios.DefaultScopeUser()

    def test_get_title(self) -> None:
        """Test get_title method."""
        view = UserDetailView()
        view.object = self.scenario.user
        self.assertEqual(view.get_title(), self.scenario.user.get_full_name())

    def test_anonymous(self) -> None:
        """Test accessing as anonymous user."""
        response = self.client.get(self.scenario.user.get_absolute_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"], "No User matches the given query."
        )

    def test_detail(self) -> None:
        """Test viewing the default user."""
        self.client.force_login(self.scenario.user)
        response = self.client.get(self.scenario.user.get_absolute_url())
        tree = self.assertResponseHTML(response)

        dl = self.assertHasElement(tree, "//dl[@id='person-info']")
        self.assertTextContentEqual(
            dl.dd[0], self.scenario.user.get_full_name()
        )
        self.assertTextContentEqual(dl.dd[1], self.scenario.user.username)
        self.assertTextContentEqual(
            dl.dd[2], str(self.scenario.user.token_set.count())
        )

        self.assertFalse(tree.xpath("//table[@id='person-groups']"))
        self.assertFalse(tree.xpath("//table[@id='person-identities']"))

    def test_detail_groups(self) -> None:
        """Test viewing a user with groups."""
        self.client.force_login(self.scenario.user)
        debusine_groups = ("foo", "bar", "baz")
        another_groups = ("wobble", "wibble", "wabble")

        for name in debusine_groups:
            self.playground.create_group(
                name=name, scope=self.scenario.scope, users=[self.scenario.user]
            )

        scope = self.playground.get_or_create_scope("another")
        for name in another_groups:
            self.playground.create_group(
                name=name, scope=scope, users=[self.scenario.user]
            )

        response = self.client.get(self.scenario.user.get_absolute_url())
        tree = self.assertResponseHTML(response)

        table = self.assertHasElement(tree, "//table[@id='person-groups']")
        tbody = self.assertHasElement(table, "tbody")
        self.assertTextContentEqual(tbody.tr[0].td[0], "another")
        for li, name in zip(tbody.tr[0].td[1].ul.li, sorted(another_groups)):
            self.assertTextContentEqual(li, name)
        self.assertTextContentEqual(tbody.tr[1].td[0], "debusine")
        for li, name in zip(tbody.tr[1].td[1].ul.li, sorted(debusine_groups)):
            self.assertTextContentEqual(li, name)

    @override_settings(SIGNON_PROVIDERS=[Provider(name="salsa", label="Salsa")])
    def test_detail_identities(self) -> None:
        """Test viewing a user with SSO identities."""
        self.client.force_login(self.scenario.user)

        identity = Identity.objects.create(
            user=self.scenario.user,
            issuer="salsa",
            subject="playground@debian.org",
        )

        response = self.client.get(self.scenario.user.get_absolute_url())
        tree = self.assertResponseHTML(response)

        table = self.assertHasElement(tree, "//table[@id='person-identities']")
        tr = self.assertHasElement(table, "tbody/tr")
        self.assertTextContentEqual(tr.td[0], "Salsa")
        self.assertTextContentEqual(tr.td[1], "playground@debian.org")
        self.assertTextContentEqual(
            tr.td[2], identity.last_used.strftime("%Y-%m-%d %H:%M")
        )

    @override_settings(SIGNON_PROVIDERS=[Provider(name="salsa", label="Salsa")])
    def test_detail_identities_cannot_manage(self) -> None:
        """Test viewing another user with SSO identities."""
        self.client.force_login(self.scenario.user)

        user = self.playground.create_user("test")
        Identity.objects.create(
            user=user,
            issuer="salsa",
            subject="playground@debian.org",
        )

        response = self.client.get(user.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath("//table[@id='person-identities']"))

    def test_detail_identities_without_provider(self) -> None:
        """Test showing a user with identities without a SSO provider."""
        self.client.force_login(self.scenario.user)

        Identity.objects.create(
            user=self.scenario.user,
            issuer="salsa",
            subject="playground@debian.org",
        )
        response = self.client.get(self.scenario.user.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath("//table[@id='person-identities']"))

    @override_settings(SIGNON_PROVIDERS=[Provider(name="salsa", label="Salsa")])
    def test_detail_identities_with_wrong_provider(self) -> None:
        """Test showing a user with identities of nonexisting SSO provider."""
        self.client.force_login(self.scenario.user)

        Identity.objects.create(
            user=self.scenario.user,
            issuer="nachos",
            subject="playground@debian.org",
        )
        response = self.client.get(self.scenario.user.get_absolute_url())
        tree = self.assertResponseHTML(response)
        self.assertFalse(tree.xpath("//table[@id='person-identities']"))


class UserTokenListViewTests(ViewTestMixin, TestCase):
    """Tests for UserTokenListView class."""

    scenario = scenarios.DefaultScopeUser()

    def test_only_one_scope(self) -> None:
        """Test hiding the scopes list if there is only one."""
        self.client.force_login(self.scenario.user)
        response = self.client.get(
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            )
        )
        tree = self.assertResponseHTML(response)
        sample_cfg = self.assertHasElement(tree, "//pre[@id='example-config']")
        fqdn = settings.DEBUSINE_FQDN
        self.assertTextContentEqual(
            sample_cfg,
            dedent(
                f"""
                [General]
                default-server = {fqdn}

                [server:{fqdn}]
                api-url = https://{fqdn}/api
                scope = {self.scenario.scope}
                token = generated-token-goes-here
                """
            ),
        )
        self.assertFalse(tree.xpath("ul[@id='scope-list']"))

    def test_two_scopes(self) -> None:
        """Test when there is more than one scope."""
        self.playground.get_or_create_scope("test")
        self.client.force_login(self.scenario.user)
        response = self.client.get(
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            )
        )
        tree = self.assertResponseHTML(response)
        sample_cfg = self.assertHasElement(tree, "//pre[@id='example-config']")
        fqdn = settings.DEBUSINE_FQDN
        scope_list = " or ".join(str(s) for s in response.context["scopes"])
        self.assertTextContentEqual(
            sample_cfg,
            dedent(
                f"""
                [General]
                default-server = {fqdn}

                [server:{fqdn}]
                api-url = https://{fqdn}/api
                scope = {scope_list}
                token = generated-token-goes-here
                """
            ),
        )

    def test_logged_no_tokens(self) -> None:
        """Logged in user with no tokens."""
        another_user = get_user_model().objects.create_user(
            username="notme", password="testpassword", email="test2@example.com"
        )
        another_token = Token.objects.create(user=another_user)

        self.client.force_login(self.scenario.user)
        response = self.client.get(
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            )
        )
        tree = self.assertResponseHTML(response)
        self.assertNavHasUser(tree, self.scenario.user)

        self.assertContains(
            response,
            "You do not currently have any tokens",
        )

        create_token_url = reverse(
            "user:token-create",
            kwargs={"username": self.scenario.user.username},
        )
        self.assertContains(
            response,
            f'<a href="{create_token_url}">create a new token</a>',
            html=True,
        )

        # Ensure that a token for another user is not displayed
        self.assertNotContains(response, another_token.key)

    def row_for_token(self, token: Token) -> str:
        """Return HTML for the row of the table for token."""
        edit_url = reverse(
            "user:token-edit",
            kwargs={"pk": token.pk, "username": self.scenario.user.username},
        )
        delete_url = reverse(
            "user:token-delete",
            kwargs={"pk": token.pk, "username": self.scenario.user.username},
        )

        return (
            "<tr>"
            f"<td>{html_check_icon(token.enabled)}</td>"
            f"<td>{date_format(token.created_at)}</td>"
            f"<td>{token.comment}</td>"
            f'<td><a href="{delete_url}">Delete</a>&nbsp;|&nbsp;'
            f'<a href="{edit_url}">Edit</a></td>'
            "</tr>"
        )

    def test_logged_two_tokens(self) -> None:
        """
        Logged user with two tokens: both are displayed.

        Assert tokens are ordered by created_at.
        """
        token1 = Token.objects.create(
            user=self.scenario.user, comment="Token 1"
        )
        token2 = Token.objects.create(
            user=self.scenario.user, comment="Token 2"
        )

        self.client.force_login(self.scenario.user)
        response = self.client.get(
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            )
        )

        self.assertContains(response, self.row_for_token(token1), html=True)
        self.assertContains(response, self.row_for_token(token2), html=True)

        # Check tokens are ordered_by created_

        # token1 is displayed earlier than token2 (was created before)
        self.assertLess(
            response.content.index(token1.comment.encode("utf-8")),
            response.content.index(token2.comment.encode("utf-8")),
        )

        # Make token1 to be created_at after token2 created_at
        token1.created_at = token2.created_at + timedelta(minutes=1)
        token1.save()

        response = self.client.get(
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            )
        )

        # Tokens appear in the opposite order than before
        self.assertGreater(
            response.content.index(token1.comment.encode("utf-8")),
            response.content.index(token2.comment.encode("utf-8")),
        )

    def test_no_logged(self) -> None:
        """Request with a non-logged user: does not return any token."""
        response = self.client.get(
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            )
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"], "No User matches the given query."
        )


class UserTokenCreateViewTests(ViewTestMixin, TestCase):
    """Tests for UserTokenCreateView."""

    user: ClassVar[User]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.user = get_user_model().objects.create_user(
            username="testuser", password="testpass"
        )

    def test_create_token(self) -> None:
        """Post to "user:token-create" to create a token."""
        self.client.force_login(self.user)
        comment = "Test 1"
        response = self.client.post(
            reverse(
                "user:token-create",
                kwargs={"username": self.user.username},
            ),
            {"comment": comment, "enabled": "true"},
        )
        # This post, even if successful, renders a template and does not
        # redirect
        tree = self.assertResponseHTML(response)

        # The token got created, enabled, with user assigned
        token = Token.objects.get(comment=comment)
        self.assertEqual(token.user, self.user)
        self.assertTrue(token.enabled)

        # No expiration, no use yet
        self.assertIsNone(token.expire_at)
        self.assertIsNone(token.last_seen_at)

        # Ensure token key shown matches the one of the new token
        key_element = self.assertHasElement(tree, "//code[@id='token-key']")
        key = self.get_node_text_normalized(key_element)
        self.assertEqual(token, Token.objects.get_token_or_none(key))

    def test_no_logged(self) -> None:
        """Request with a non-logged user: cannot create a token."""
        response = self.client.get(
            reverse(
                "user:token-create",
                kwargs={"username": self.user.username},
            )
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"], "No User matches the given query."
        )

    def test_initial_form(self) -> None:
        """Get request to ensure the form is displayed."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse(
                "user:token-create",
                kwargs={"username": self.user.username},
            )
        )

        self.assertContains(
            response, "<title>Debusine - Create token</title>", html=True
        )
        self.assertContains(response, "<h1>Create token</h1>", html=True)
        self.assertContains(
            response,
            '<input class="btn btn-primary btn-sm" '
            'type="submit" value="Create">',
            html=True,
        )


class UserTokenUpdateViewTests(TestCase):
    """Tests for UserTokenCreateView when editing a token."""

    scenario = scenarios.DefaultScopeUserAPI()

    def test_edit_token(self) -> None:
        """Get "user:token-edit" to edit a token."""
        token = self.scenario.user_token
        self.client.force_login(self.scenario.user)

        enabled = True
        comment = "This is a token"

        response = self.client.post(
            reverse(
                "user:token-edit",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            ),
            {"comment": comment, "enabled": enabled},
        )

        self.assertRedirects(
            response,
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            ),
        )

        token.refresh_from_db()

        self.assertEqual(token.comment, comment)
        self.assertEqual(token.enabled, enabled)

    def test_get_404_not_found(self) -> None:
        """Get of token/PK/ that exist but belongs to another user: 404."""
        token = self.scenario.user_token
        user2 = get_user_model().objects.create(
            username="testuser2",
            password="testpass2",
            email="user2@example.com",
        )

        self.client.force_login(user2)
        response = self.client.get(
            reverse(
                "user:token-edit",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            )
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.context["exception"],
            "testuser2 cannot manage user playground",
        )
        self.assertNotContains(
            response, token.hash, status_code=status.HTTP_403_FORBIDDEN
        )

    def test_put_404_not_found(self) -> None:
        """Put of token/PK/ that exist but belongs to another user: 404."""
        token = self.scenario.user_token
        user2 = get_user_model().objects.create(
            username="testuser2",
            password="testpass2",
            email="user2@example.com",
        )

        self.client.force_login(user2)
        comment = "This is the new comment"
        enabled = False
        response = self.client.put(
            reverse(
                "user:token-edit",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            ),
            {"comment": comment, "enabled": enabled},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.context["exception"],
            "testuser2 cannot manage user playground",
        )
        self.assertNotContains(
            response, token.hash, status_code=status.HTTP_403_FORBIDDEN
        )

    def test_initial_form(self) -> None:
        """Get request to ensure the form is displayed."""
        token = self.scenario.user_token
        self.client.force_login(self.scenario.user)

        response = self.client.get(
            reverse(
                "user:token-edit",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            )
        )

        self.assertContains(
            response, "<title>Debusine - Edit token</title>", html=True
        )
        self.assertContains(response, "<h1>Edit token</h1>", html=True)
        self.assertContains(
            response,
            '<input class="btn btn-primary btn-sm" type="submit" value="Edit">',
            html=True,
        )
        assert isinstance(response, SimpleTemplateResponse)
        self.assertEqual(
            response.context_data["form"]["comment"].initial,
            token.comment,
        )


class UserTokenDeleteViewTests(TestCase):
    """Tests for UserTokenDeleteView."""

    scenario = scenarios.DefaultScopeUserAPI()

    def test_get_delete_token_authenticated_user(self) -> None:
        """Get request to delete a token. Token information is returned."""
        token = self.scenario.user_token
        self.client.force_login(self.scenario.user)

        response = self.client.get(
            reverse(
                "user:token-delete",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            )
        )

        expected_contents = (
            "<ul>"
            f"<li>Comment: {token.comment}</li>"
            f"<li>Enabled: {html_check_icon(token.enabled)}</li>"
            f"<li>Created at: {date_format(token.created_at)}</li>"
            "</ul>"
        )

        self.assertContains(response, expected_contents, html=True)

    def test_post_delete_token_authenticated_user(self) -> None:
        """Post request to delete a token. The token is deleted."""
        token = self.scenario.user_token
        self.client.force_login(self.scenario.user)

        response = self.client.post(
            reverse(
                "user:token-delete",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            )
        )

        self.assertRedirects(
            response,
            reverse(
                "user:token-list",
                kwargs={"username": self.scenario.user.username},
            ),
        )

        # The token was deleted
        self.assertEqual(Token.objects.filter(pk=token.pk).count(), 0)

    def test_get_delete_token_non_authenticated(self) -> None:
        """
        Non authenticated Get and Post request. Return 403.

        The token is not displayed.
        """
        token = self.scenario.user_token
        response = self.client.get(
            reverse(
                "user:token-delete",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            )
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"], "No User matches the given query."
        )
        self.assertNotContains(
            response, token.hash, status_code=status.HTTP_404_NOT_FOUND
        )

        response = self.client.post(
            reverse(
                "user:token-delete",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            )
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            response.context["exception"], "No User matches the given query."
        )
        self.assertNotContains(
            response, token.hash, status_code=status.HTTP_404_NOT_FOUND
        )

    def test_get_post_delete_token_another_user(self) -> None:
        """
        Get and Post request delete token from another user. Return 404.

        The token is not deleted neither displayed.
        """
        token = self.scenario.user_token
        user2 = get_user_model().objects.create_user(
            username="testuser2",
            password="testpass2",
            email="user2@example.com",
        )

        self.client.force_login(user2)

        response = self.client.get(
            reverse(
                "user:token-delete",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            )
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.context["exception"],
            "testuser2 cannot manage user playground",
        )
        self.assertNotContains(
            response, token.hash, status_code=status.HTTP_403_FORBIDDEN
        )

        response = self.client.post(
            reverse(
                "user:token-delete",
                kwargs={
                    "pk": token.pk,
                    "username": self.scenario.user.username,
                },
            )
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.context["exception"],
            "testuser2 cannot manage user playground",
        )
        self.assertNotContains(
            response, token.hash, status_code=status.HTTP_403_FORBIDDEN
        )

        self.assertEqual(Token.objects.filter(pk=token.pk).count(), 1)


class UserGroupListViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`UserGroupListView`."""

    scenario = scenarios.DefaultContext()
    group: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.group = cls.playground.create_group("testgroup")

    def assertGetSucceeds(
        self, user: User, visitor: User | None = None
    ) -> TestResponseType:
        """GET the page and ensure the request succeeds."""
        if visitor is None:
            visitor = user
        self.client.force_login(visitor)
        response = self.client.get(
            reverse("user:groups", kwargs={"username": user.username})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def test_get_self_no_groups(self) -> None:
        """Get one's own empty group list."""
        response = self.assertGetSucceeds(self.scenario.user)
        tree = self.assertResponseHTML(response)
        intro = self.assertHasElement(tree, "//p[@id='no-groups-message']")
        self.assertTextContentEqual(
            intro, "You are not currently a member of any group."
        )

    def test_get_visitor_no_groups(self) -> None:
        """Get another person's empty group list."""
        manager = User.objects.create_user("manager")
        with override_permission(User, "can_manage", AllowAll):
            response = self.assertGetSucceeds(
                self.scenario.user, visitor=manager
            )
        tree = self.assertResponseHTML(response)
        intro = self.assertHasElement(tree, "//p[@id='no-groups-message']")
        self.assertTextContentEqual(
            intro,
            f"{self.scenario.user.get_full_name()}"
            f" <{self.scenario.user.username}>"
            " is not currently a member of any group.",
        )

    def test_get_self(self) -> None:
        """Get one's own group list."""
        self.playground.add_user(self.group, self.scenario.user)
        response = self.assertGetSucceeds(self.scenario.user)
        tree = self.assertResponseHTML(response)
        intro = self.assertHasElement(tree, "//p[@id='intro']")
        self.assertTextContentEqual(
            intro, "This page lists all groups of which you are a member."
        )
        table = self.assertHasElement(tree, "//table[@id='group-list']")
        self.assertEqual(len(table.tbody.tr), 1)
        tr = table.tbody.tr[0]
        self.assertTextContentEqual(tr.td[0].a, self.group.name)
        self.assertEqual(tr.td[0].a.get("href"), self.group.get_absolute_url())
        self.assertTextContentEqual(tr.td[1], "1")
        self.assertTextContentEqual(tr.td[2], "no")
        self.assertTextContentEqual(tr.td[3], "member")

    def test_get_self_hidden_groups(self) -> None:
        """Get one's own group list."""
        self.playground.add_user(self.group, self.scenario.user)
        with override_permission(Group, "can_display", DenyAll):
            response = self.assertGetSucceeds(self.scenario.user)
        tree = self.assertResponseHTML(response)
        self.assertHasElement(tree, "//p[@id='no-groups-message']")

    def test_get_visitor(self) -> None:
        """Get another person's group list."""
        self.playground.add_user(self.group, self.scenario.user)
        manager = User.objects.create_user("manager")
        with override_permission(User, "can_manage", AllowAll):
            response = self.assertGetSucceeds(
                self.scenario.user, visitor=manager
            )
        tree = self.assertResponseHTML(response)
        intro = self.assertHasElement(tree, "//p[@id='intro']")
        self.assertTextContentEqual(
            intro,
            "This page lists all groups of which"
            f" {self.scenario.user.get_full_name()} "
            f" <{self.scenario.user.username}> is a member.",
        )
        table = self.assertHasElement(tree, "//table[@id='group-list']")
        self.assertEqual(len(table.tbody.tr), 1)
        tr = table.tbody.tr[0]
        self.assertTextContentEqual(tr.td[0].a, self.group.name)
        self.assertEqual(tr.td[0].a.get("href"), self.group.get_absolute_url())
        self.assertTextContentEqual(tr.td[1], "1")
        self.assertTextContentEqual(tr.td[2], "no")
        self.assertTextContentEqual(tr.td[3], "member")


class GroupDetailViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`GroupDetailView`."""

    scenario = scenarios.DefaultContext()
    group: ClassVar[Group]
    other_user: ClassVar[User]
    group_queried: Group | None

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.group = cls.playground.create_group("testgroup")
        cls.other_user = cls.playground.create_user("other")

    def setUp(self) -> None:
        """Reset the group_queried cache."""
        super().setUp()
        self.group_queried = None

    def assertGetSucceeds(self, group: Group) -> TestResponseType:
        """GET the page and ensure the request succeeds."""
        self.group_queried = group
        self.client.force_login(self.scenario.user)
        response = self.client.get(group.get_absolute_url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def assertEphemeral(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        expected: bool = True,
    ) -> lxml.objectify.ObjectifiedElement | None:
        """Check that the page reports the group as ephemeral."""
        path = "//p[@id='info-ephemeral']"
        if expected:
            return self.assertHasElement(tree, path)
        else:
            self.assertFalse(tree.xpath(path))
            return None

    def assertFormErrors(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        expected: bool = True,
    ) -> lxml.objectify.ObjectifiedElement | None:
        path = "//ul[@class='errorlist']"
        if expected:
            return self.assertHasElement(tree, path)
        else:
            self.assertFalse(tree.xpath(path))
            return None

    def assertGroupTable(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        contents: list[tuple[User, Group.Roles]],
        is_admin: bool = False,
    ) -> None:
        """Check the contents of the group members table."""
        assert self.group_queried
        members = self.assertHasElement(tree, "//table[@id='members']")
        self.assertEqual(len(members.tbody.tr), len(contents))
        for idx, (user, role) in enumerate(contents):
            tr = members.tbody.tr[idx]
            suffix = " (you)" if user == self.scenario.user else ""
            self.assertTextContentEqual(tr.td[0], user.username + suffix)
            self.assertTextContentEqual(tr.td[1], user.get_full_name())
            self.assertTextContentEqual(tr.td[2], role)
            if is_admin:
                self.assertEqual(
                    tr.td[2].a.get("href"),
                    reverse(
                        "groups:update-member",
                        kwargs={
                            "group": self.group_queried.name,
                            "user": user.username,
                        },
                    ),
                )

                self.assertTextContentEqual(tr.td[3].a, "Remove")
                self.assertEqual(
                    tr.td[3].a.get("href"),
                    reverse(
                        "groups:remove-member",
                        kwargs={
                            "group": self.group_queried.name,
                            "user": user.username,
                        },
                    ),
                )
            else:
                self.assertEqual(len(tr.td), 3)
        if is_admin:
            self.assertHasElement(tree, "//form[@id='add-user-form']")
        else:
            self.assertFalse(tree.xpath("//form[@id='add-user-form']"))

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        url = reverse("groups:detail", kwargs={"group": self.group.name})
        self.client.force_login(self.scenario.user)

        self.assertEnforcesPermission(
            self.group.can_display,
            url,
            "get_context_data",
            method="get",
            error_code=status.HTTP_404_NOT_FOUND,
        )

        self.assertEnforcesPermission(
            self.group.can_manage,
            url,
            "form_valid",
            method="post",
            data={"username": "other"},
        )

    def test_get_as_anonymous(self) -> None:
        """Test that the right permissions are enforced."""
        response = self.client.get(
            reverse("groups:detail", kwargs={"group": self.group.name})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_as_nonmember(self) -> None:
        """View the page as a group member."""
        self.playground.add_user(self.group, self.other_user)
        response = self.assertGetSucceeds(self.group)
        tree = self.assertResponseHTML(response)
        info_role = self.assertHasElement(tree, "//p[@id='info-role']")
        self.assertTextContentEqual(
            info_role, "You are not a member of this group."
        )
        self.assertEphemeral(tree, False)
        self.assertFormErrors(tree, False)
        self.assertGroupTable(tree, [(self.other_user, Group.Roles.MEMBER)])

    def test_get_ephemeral(self) -> None:
        """View the page as a group member."""
        with context.disable_permission_checks():
            self.group.ephemeral = True
            self.group.save()
        response = self.assertGetSucceeds(self.group)
        tree = self.assertResponseHTML(response)
        self.assertEphemeral(tree, True)

    def test_get_as_member(self) -> None:
        """View the page as a group member."""
        self.playground.add_user(self.group, self.scenario.user)
        self.playground.add_user(self.group, self.other_user)
        response = self.assertGetSucceeds(self.group)
        tree = self.assertResponseHTML(response)
        info_role = self.assertHasElement(tree, "//p[@id='info-role']")
        self.assertTextContentEqual(info_role, "You have role member.")
        self.assertEphemeral(tree, False)
        self.assertFormErrors(tree, False)
        self.assertGroupTable(
            tree,
            [
                (self.other_user, Group.Roles.MEMBER),
                (self.scenario.user, Group.Roles.MEMBER),
            ],
        )

    def test_get_as_admin(self) -> None:
        """View the page as a group member."""
        self.playground.add_user(
            self.group, self.scenario.user, Group.Roles.ADMIN
        )
        self.playground.add_user(self.group, self.other_user)
        response = self.assertGetSucceeds(self.group)
        tree = self.assertResponseHTML(response)
        info_role = self.assertHasElement(tree, "//p[@id='info-role']")
        self.assertTextContentEqual(info_role, "You have role admin.")
        self.assertEphemeral(tree, False)
        self.assertFormErrors(tree, False)
        self.assertGroupTable(
            tree,
            [
                (self.scenario.user, Group.Roles.ADMIN),
                (self.other_user, Group.Roles.MEMBER),
            ],
            is_admin=True,
        )

    def test_post_add_user(self) -> None:
        """Add a user to the group."""
        self.playground.add_user(
            self.group, self.scenario.user, Group.Roles.ADMIN
        )
        self.client.force_login(self.scenario.user)
        response = self.client.post(
            self.group.get_absolute_url(),
            data={"username": self.other_user.username},
        )
        self.assertRedirects(response, self.group.get_absolute_url())
        membership = GroupMembership.objects.get(
            group=self.group, user=self.other_user
        )
        self.assertEqual(membership.role, Group.Roles.MEMBER)

    def test_post_add_user_validation_failed(self) -> None:
        """Adding a user to the group failed validation."""
        self.playground.add_user(
            self.group, self.scenario.user, Group.Roles.ADMIN
        )
        self.client.force_login(self.scenario.user)
        response = self.client.post(
            self.group.get_absolute_url(), data={"username": "does-not-exist"}
        )
        tree = self.assertResponseHTML(response)
        self.assertEphemeral(tree, False)
        self.assertFormErrors(tree, True)


class GroupDetailViewMemberPaginationTests(ViewTestMixin, TestCase):
    """Tests pagination for :py:class:`GroupDetailView`."""

    scenario = scenarios.DefaultContext()
    group: ClassVar[Group]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.group = cls.playground.create_group("testgroup")
        # Add enough users to trigger pagination
        for idx in range(20):
            user = cls.playground.create_user(f"user{idx:02d}")
            GroupMembership.objects.create(
                group=cls.group,
                user=user,
                # Admins are the odd ones
                role=Group.Roles.ADMIN if idx % 2 == 1 else Group.Roles.MEMBER,
            )

    def _get(self, **kwargs: Any) -> TestResponseType:
        """Fetch the page."""
        self.client.force_login(self.scenario.user)
        response = self.client.get(
            self.group.get_absolute_url() + "?" + urlencode(kwargs)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def assertGroupTable(
        self,
        tree: lxml.objectify.ObjectifiedElement,
        expected: list[tuple[str, Group.Roles]],
    ) -> None:
        """Check the contents of the group members table."""
        members = self.assertHasElement(tree, "//table[@id='members']")
        actual: list[tuple[str, Group.Roles]] = []
        for tr in members.tbody.tr:
            username = self.get_node_text_normalized(tr.td[0])
            role = Group.Roles(self.get_node_text_normalized(tr.td[2]))
            actual.append((username, role))

        self.assertEqual(actual, expected)

    def test_default(self) -> None:
        """Test default view with no page or ordering selected."""
        response = self._get()
        tree = self.assertResponseHTML(response)
        ctx = response.context
        self.assertEqual(ctx["asc"], "1")
        self.assertIsNone(ctx["order"])
        self.assertGroupTable(
            tree,
            [
                ("user01", Group.Roles.ADMIN),
                ("user03", Group.Roles.ADMIN),
                ("user05", Group.Roles.ADMIN),
                ("user07", Group.Roles.ADMIN),
                ("user09", Group.Roles.ADMIN),
                ("user11", Group.Roles.ADMIN),
                ("user13", Group.Roles.ADMIN),
                ("user15", Group.Roles.ADMIN),
                ("user17", Group.Roles.ADMIN),
                ("user19", Group.Roles.ADMIN),
            ],
        )

    def test_page(self) -> None:
        """Test selecting the page number."""
        response = self._get(page=2)
        tree = self.assertResponseHTML(response)
        ctx = response.context
        self.assertEqual(ctx["asc"], "1")
        self.assertIsNone(ctx["order"])
        self.assertGroupTable(
            tree,
            [
                ("user00", Group.Roles.MEMBER),
                ("user02", Group.Roles.MEMBER),
                ("user04", Group.Roles.MEMBER),
                ("user06", Group.Roles.MEMBER),
                ("user08", Group.Roles.MEMBER),
                ("user10", Group.Roles.MEMBER),
                ("user12", Group.Roles.MEMBER),
                ("user14", Group.Roles.MEMBER),
                ("user16", Group.Roles.MEMBER),
                ("user18", Group.Roles.MEMBER),
            ],
        )

    def test_order_username(self) -> None:
        """Test ordering by username."""
        response = self._get(order="name")
        tree = self.assertResponseHTML(response)
        ctx = response.context
        self.assertEqual(ctx["order"], "name")
        self.assertGroupTable(
            tree,
            [
                ("user00", Group.Roles.MEMBER),
                ("user01", Group.Roles.ADMIN),
                ("user02", Group.Roles.MEMBER),
                ("user03", Group.Roles.ADMIN),
                ("user04", Group.Roles.MEMBER),
                ("user05", Group.Roles.ADMIN),
                ("user06", Group.Roles.MEMBER),
                ("user07", Group.Roles.ADMIN),
                ("user08", Group.Roles.MEMBER),
                ("user09", Group.Roles.ADMIN),
            ],
        )

        response = self._get(order="name.desc")
        tree = self.assertResponseHTML(response)
        ctx = response.context
        self.assertEqual(ctx["order"], "name.desc")
        self.assertGroupTable(
            tree,
            [
                ("user19", Group.Roles.ADMIN),
                ("user18", Group.Roles.MEMBER),
                ("user17", Group.Roles.ADMIN),
                ("user16", Group.Roles.MEMBER),
                ("user15", Group.Roles.ADMIN),
                ("user14", Group.Roles.MEMBER),
                ("user13", Group.Roles.ADMIN),
                ("user12", Group.Roles.MEMBER),
                ("user11", Group.Roles.ADMIN),
                ("user10", Group.Roles.MEMBER),
            ],
        )

    def test_order_role(self) -> None:
        """Test ordering by role."""
        response = self._get(order="role")
        tree = self.assertResponseHTML(response)
        ctx = response.context
        self.assertEqual(ctx["order"], "role")
        self.assertGroupTable(
            tree,
            [
                ('user01', Group.Roles.ADMIN),
                ('user03', Group.Roles.ADMIN),
                ('user05', Group.Roles.ADMIN),
                ('user07', Group.Roles.ADMIN),
                ('user09', Group.Roles.ADMIN),
                ('user11', Group.Roles.ADMIN),
                ('user13', Group.Roles.ADMIN),
                ('user15', Group.Roles.ADMIN),
                ('user17', Group.Roles.ADMIN),
                ('user19', Group.Roles.ADMIN),
            ],
        )

        response = self._get(order="role.desc")
        tree = self.assertResponseHTML(response)
        ctx = response.context
        self.assertEqual(ctx["order"], "role.desc")
        self.assertGroupTable(
            tree,
            [
                ('user00', Group.Roles.MEMBER),
                ('user02', Group.Roles.MEMBER),
                ('user04', Group.Roles.MEMBER),
                ('user06', Group.Roles.MEMBER),
                ('user08', Group.Roles.MEMBER),
                ('user10', Group.Roles.MEMBER),
                ('user12', Group.Roles.MEMBER),
                ('user14', Group.Roles.MEMBER),
                ('user16', Group.Roles.MEMBER),
                ('user18', Group.Roles.MEMBER),
            ],
        )


class MembershipUpdateViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`MembershipUpdateView`."""

    scenario = scenarios.DefaultContext()
    group: ClassVar[Group]
    other_user: ClassVar[User]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.group = cls.playground.create_group("testgroup")
        cls.playground.add_user(cls.group, cls.scenario.user, Group.Roles.ADMIN)
        cls.other_user = cls.playground.create_user("other")
        cls.playground.add_user(cls.group, cls.other_user)

    def _get(self, user: User) -> TestResponseType:
        """GET to the view for the given user."""
        self.client.force_login(self.scenario.user)
        url = reverse(
            "groups:update-member",
            kwargs={
                "group": self.group.name,
                "user": user.username,
            },
        )
        return self.client.get(url)

    def _post(self, user: User, data: dict[str, Any]) -> TestResponseType:
        """POST to the view for the given user."""
        self.client.force_login(self.scenario.user)
        url = reverse(
            "groups:update-member",
            kwargs={
                "group": self.group.name,
                "user": user.username,
            },
        )
        return self.client.post(url, data=data)

    def assertRole(self, user: User, role: Group.Roles) -> None:
        """Ensure the user has the given role."""
        membership = GroupMembership.objects.get(user=user, group=self.group)
        self.assertEqual(membership.role, role)

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        url = reverse(
            "groups:update-member",
            kwargs={
                "group": self.group.name,
                "user": self.scenario.user.username,
            },
        )
        self.client.force_login(self.scenario.user)

        self.assertEnforcesPermission(
            self.group.can_manage,
            url,
            "get_context_data",
            method="get",
            error_code=status.HTTP_404_NOT_FOUND,
        )

        self.assertEnforcesPermission(
            self.group.can_manage,
            url,
            "form_valid",
            method="post",
            data={"role": "admin"},
            error_code=status.HTTP_404_NOT_FOUND,
        )

    def test_role_validation(self) -> None:
        """Test role validation."""
        response = self._post(
            self.scenario.user, data={"role": "does-not-exist"}
        )
        tree = self.assertResponseHTML(response)
        errorlist = self.assertHasElement(tree, "//ul[@class='errorlist']")
        self.assertEqual(len(errorlist.li), 1)
        self.assertTextContentEqual(
            errorlist.li,
            "Select a valid choice. does-not-exist"
            " is not one of the available choices.",
        )

    def test_intro_self(self) -> None:
        """Check page introduction for oneself."""
        response = self._get(self.scenario.user)
        tree = self.assertResponseHTML(response)
        intro = self.assertHasElement(tree, "//p[@id='intro']")
        self.assertTextContentEqual(
            intro,
            f"Update your role in group {self.group}.",
        )

    def test_intro_other(self) -> None:
        """Check page introduction for another user."""
        response = self._get(self.other_user)
        tree = self.assertResponseHTML(response)
        intro = self.assertHasElement(tree, "//p[@id='intro']")
        self.assertTextContentEqual(
            intro,
            f"Update role of user {self.other_user.get_full_name()}"
            f" <{self.other_user.username}>"
            f" in group {self.group}.",
        )

    def test_change_other(self) -> None:
        """Test changing the role of another user."""
        # Can set another person as admin
        response = self._post(self.other_user, data={"role": Group.Roles.ADMIN})
        self.assertRedirects(response, self.group.get_absolute_url())
        self.assertRole(self.other_user, Group.Roles.ADMIN)

        # Can revoke another ADMIN role
        response = self._post(
            self.other_user, data={"role": Group.Roles.MEMBER}
        )
        self.assertRedirects(response, self.group.get_absolute_url())
        self.assertRole(self.other_user, Group.Roles.MEMBER)

    def test_change_self(self) -> None:
        """Test changing the role of oneself."""
        # Can set oneself as admin, only if already admin
        response = self._post(
            self.scenario.user, data={"role": Group.Roles.ADMIN}
        )
        self.assertRedirects(response, self.group.get_absolute_url())
        self.assertRole(self.scenario.user, Group.Roles.ADMIN)

        # Can revoke one's own ADMIN role
        response = self._post(
            self.scenario.user, data={"role": Group.Roles.MEMBER}
        )
        self.assertRedirects(response, self.group.get_absolute_url())
        self.assertRole(self.scenario.user, Group.Roles.MEMBER)

        # This locks oneself out
        response = self._post(
            self.scenario.user, data={"role": Group.Roles.ADMIN}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class MembershipDeleteViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`MembershipDeleteView`."""

    scenario = scenarios.DefaultContext()
    group: ClassVar[Group]
    other_user: ClassVar[User]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.group = cls.playground.create_group("testgroup")
        cls.playground.add_user(cls.group, cls.scenario.user, Group.Roles.ADMIN)
        cls.other_user = cls.playground.create_user("other")
        cls.playground.add_user(cls.group, cls.other_user)

    def assertIsMember(self, user: User) -> None:
        """Ensure the user is a member of self.group."""
        self.assertTrue(
            GroupMembership.objects.filter(group=self.group, user=user).exists()
        )

    def assertIsNotMember(self, user: User) -> None:
        """Ensure the user is a member of self.group."""
        self.assertFalse(
            GroupMembership.objects.filter(group=self.group, user=user).exists()
        )

    def _get(self, user: User) -> TestResponseType:
        """POST to the view for the given user."""
        self.client.force_login(self.scenario.user)
        url = reverse(
            "groups:remove-member",
            kwargs={
                "group": self.group.name,
                "user": user.username,
            },
        )
        return self.client.get(url)

    def _post(self, user: User) -> TestResponseType:
        """POST to the view for the given user."""
        self.client.force_login(self.scenario.user)
        url = reverse(
            "groups:remove-member",
            kwargs={
                "group": self.group.name,
                "user": user.username,
            },
        )
        return self.client.post(url)

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        url = reverse(
            "groups:remove-member",
            kwargs={
                "group": self.group.name,
                "user": self.scenario.user.username,
            },
        )
        self.client.force_login(self.scenario.user)

        self.assertEnforcesPermission(
            self.group.can_manage,
            url,
            "get_context_data",
            method="get",
            error_code=status.HTTP_404_NOT_FOUND,
        )

        self.assertEnforcesPermission(
            self.group.can_manage,
            url,
            "form_valid",
            method="post",
            data={},
            error_code=status.HTTP_404_NOT_FOUND,
        )

    def test_intro_self(self) -> None:
        """Check page introduction for oneself."""
        response = self._get(self.scenario.user)
        tree = self.assertResponseHTML(response)
        intro = self.assertHasElement(tree, "//p[@id='intro']")
        self.assertTextContentEqual(
            intro,
            f"Are you sure you want to remove yourself"
            f" from group {self.group.name} in scope debusine?",
        )

    def test_intro_other(self) -> None:
        """Check page introduction for another user."""
        response = self._get(self.other_user)
        tree = self.assertResponseHTML(response)
        intro = self.assertHasElement(tree, "//p[@id='intro']")
        self.assertTextContentEqual(
            intro,
            "Are you sure you want to remove user "
            f" {self.other_user.get_full_name()} "
            f" <{self.other_user.username}>"
            f" from group {self.group.name} in scope debusine?",
        )

    def test_remove_other(self) -> None:
        """Test removing another user."""
        response = self._post(self.other_user)
        self.assertRedirects(response, self.group.get_absolute_url())
        self.assertIsMember(self.scenario.user)
        self.assertIsNotMember(self.other_user)

    def test_remove_self(self) -> None:
        """Test removing oneself."""
        response = self._post(self.scenario.user)
        self.assertRedirects(response, self.group.get_absolute_url())
        self.assertIsNotMember(self.scenario.user)
        self.assertIsMember(self.other_user)


class GroupAuditLogViewTests(ViewTestMixin, TestCase):
    """Tests for :py:class:`GroupAuditLogView`."""

    scenario = scenarios.DefaultContext()
    group: ClassVar[Group]
    other_user: ClassVar[User]

    @classmethod
    def setUpTestData(cls) -> None:
        """Initialize class data."""
        super().setUpTestData()
        cls.other_user = cls.playground.create_user("other")
        cls.group = cls.playground.create_group("testgroup")
        changes = auth.GroupAuditLogMemberRemoved(user="…")
        for n in range(0, 6, 2):
            changes.user = f"{n}"
            GroupAuditLog.objects.create(
                group=cls.group, actor=cls.scenario.user, changes=changes.dict()
            )
            changes.user = f"{n + 1}"
            GroupAuditLog.objects.create(
                group=cls.group, actor=cls.other_user, changes=changes.dict()
            )

    def assertGetSucceeds(self) -> TestResponseType:
        """GET the page and ensure the request succeeds."""
        self.client.force_login(self.scenario.user)
        response = self.client.get(
            reverse("groups:audit-log", kwargs={"group": self.group.name})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def assertHasRows(
        self, tree: lxml.objectify.ObjectifiedElement
    ) -> list[int]:
        """Ensure the table has rows and return the list of messages."""
        res: list[int] = []
        table = self.assertHasElement(tree, "//table[@id='audit-log']")
        for tr in table.tbody.tr:
            message = self.get_node_text_normalized(tr.td[2])
            res.append(int(message.removeprefix("removed ")))
        self.assertTrue(res)
        return res

    def test_permissions(self) -> None:
        """Test that the right permissions are enforced."""
        url = reverse("groups:audit-log", kwargs={"group": self.group.name})
        self.client.force_login(self.scenario.user)

        self.assertEnforcesPermission(
            self.group.can_display,
            url,
            "get_context_data",
            method="get",
            error_code=status.HTTP_404_NOT_FOUND,
        )

    def test_get_as_anonymous(self) -> None:
        """Test that the right permissions are enforced."""
        response = self.client.get(
            reverse("groups:audit-log", kwargs={"group": self.group.name})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_as_nonmember(self) -> None:
        """View the page as a group member."""
        response = self.assertGetSucceeds()
        tree = self.assertResponseHTML(response)
        rows = self.assertHasRows(tree)
        self.assertEqual(rows, [5, 4, 3, 2, 1, 0])

    def test_get_as_member(self) -> None:
        """View the page as a group member."""
        self.playground.add_user(self.group, self.scenario.user)
        response = self.assertGetSucceeds()
        tree = self.assertResponseHTML(response)
        rows = self.assertHasRows(tree)
        self.assertEqual(rows, [5, 4, 3, 2, 1, 0])

    def test_get_as_admin(self) -> None:
        """View the page as a group member."""
        self.playground.add_user(
            self.group, self.scenario.user, Group.Roles.ADMIN
        )
        response = self.assertGetSucceeds()
        tree = self.assertResponseHTML(response)
        rows = self.assertHasRows(tree)
        self.assertEqual(rows, [5, 4, 3, 2, 1, 0])
