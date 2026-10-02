"""Routing, authentication flow, and URL layout.

The fault2 merge left three URL modules that must all stay mounted (canonical
``monitoring:`` namespace, ``/api/`` short aliases, ``/monitoring/`` dashboard)
without re-registering a namespace twice -- Django check ``urls.W005``. The
tests below pin both the paths and that every protected route stays protected.
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import resolve, reverse


@pytest.mark.django_db
class TestRootRedirect:
    def test_anonymous_users_are_sent_to_login(self, client):
        response = client.get("/")
        assert response.status_code == 302
        assert response.url.startswith("/accounts/login/")

    def test_authenticated_users_are_sent_home(self, auth_client):
        response = auth_client.get("/")
        assert response.status_code == 302
        assert response.url == "/home/"


@pytest.mark.django_db
class TestLoginFlow:
    def test_login_page_renders(self, client):
        response = client.get("/accounts/login/")
        assert response.status_code == 200
        assert response.context["form"] is not None

    def test_valid_credentials_authenticate_and_redirect(self, client, user):
        user.set_password("pw")
        user.save()

        response = client.post(
            "/accounts/login/",
            {"username": "tester", "password": "pw", "next": "/home/"},
        )

        assert response.status_code == 302
        assert response.url == "/home/"
        assert int(client.session["_auth_user_id"]) == user.pk

    def test_invalid_credentials_are_rejected(self, client, user):
        response = client.post(
            "/accounts/login/", {"username": "tester", "password": "wrong"}
        )
        assert response.status_code == 200  # re-rendered with the form
        assert "_auth_user_id" not in client.session

    def test_login_honours_the_next_parameter(self, client, user):
        user.set_password("pw")
        user.save()
        response = client.post(
            "/accounts/login/",
            {"username": "tester", "password": "pw", "next": "/visualization/"},
        )
        assert response.url == "/visualization/"

    def test_logout_clears_the_session_and_redirects(self, auth_client):
        response = auth_client.get("/accounts/logout/")
        assert response.status_code == 302
        assert response.url == "/accounts/login/"
        assert "_auth_user_id" not in auth_client.session


@pytest.mark.django_db
class TestRegistration:
    def test_registration_page_renders(self, client):
        response = client.get("/accounts/register/")
        assert response.status_code == 200
        assert response.context["form"] is not None

    def test_new_account_is_logged_in(self, client):
        response = client.post(
            "/accounts/register/",
            {
                "username": "newuser",
                "password1": "sTr0ng-passw0rd!",
                "password2": "sTr0ng-passw0rd!",
            },
        )
        assert response.status_code == 302
        assert response.url == "/"
        assert get_user_model().objects.filter(username="newuser").exists()
        assert int(client.session["_auth_user_id"]) > 0


@pytest.mark.django_db
class TestProtectedPages:
    @pytest.mark.parametrize(
        "path",
        [
            "/home/",
            "/welcome/",
            "/visualization/",
            "/prediction/",
            "/prediction/welcome/",
            "/monitoring/dashboard/",
        ],
    )
    def test_page_redirects_anonymous_users(self, client, path):
        response = client.get(path)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    @pytest.mark.parametrize(
        "path",
        [
            "/home/",
            "/visualization/",
            "/prediction/",
            "/monitoring/dashboard/",
        ],
    )
    def test_page_renders_for_a_logged_in_user(self, auth_client, path):
        assert auth_client.get(path).status_code == 200

    def test_api_endpoints_redirect_anonymous_users(self, client):
        for path in ("/api/monitoring/status/", "/api/status/", "/api/events/"):
            assert client.get(path).status_code == 302


class TestUrlLayout:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("root", "/"),
            ("home", "/home/"),
            ("welcome", "/welcome/"),
            ("accounts:login", "/accounts/login/"),
            ("accounts:register", "/accounts/register/"),
            ("accounts:logout", "/accounts/logout/"),
            ("prediction:predict", "/prediction/"),
            ("prediction:welcome", "/prediction/welcome/"),
            ("visualization:index", "/visualization/"),
            ("monitoring:status", "/api/monitoring/status/"),
            ("monitoring:thresholds", "/api/monitoring/thresholds/"),
            ("monitoring:events", "/api/monitoring/events/"),
            ("monitoring:start_simulation", "/api/monitoring/simulate/start/"),
            ("monitoring:stop_simulation", "/api/monitoring/simulate/stop/"),
            # dashboard_urls.py carries no app_name, so this one is global.
            ("monitoring_dashboard", "/monitoring/dashboard/"),
            ("monitoring_dashboard_alias", "/api/dashboard/"),
            ("admin:index", "/admin/"),
        ],
    )
    def test_named_routes(self, name, expected):
        assert reverse(name) == expected

    @pytest.mark.parametrize(
        "path",
        [
            "/home/",
            "/visualization/",
            "/prediction/",
            "/monitoring/dashboard/",
            "/api/monitoring/status/",
            "/api/status/",
            "/api/thresholds/",
            "/api/events/",
        ],
    )
    def test_paths_resolve_to_a_view(self, path):
        assert resolve(path).func is not None

    def test_no_namespace_is_registered_twice(self):
        """Phase 1 split the URLconf into three modules specifically to avoid
        Django check `urls.W005`; the canonical namespace must be unique."""
        from django.urls import get_resolver

        namespaces = [
            key
            for key, _ in get_resolver().namespace_dict.items()
            if key == "monitoring"
        ]
        assert namespaces == ["monitoring"], namespaces

    def test_login_url_is_the_accounts_route(self):
        assert settings.LOGIN_URL == "/accounts/login/"
        assert settings.LOGIN_REDIRECT_URL == "/home/"
        assert settings.LOGOUT_REDIRECT_URL == "/accounts/login/"

    def test_unknown_paths_404(self, auth_client):
        assert auth_client.get("/definitely/not/a/page/").status_code == 404
