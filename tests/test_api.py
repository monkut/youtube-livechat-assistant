"""Tests for the Flask API."""

import os
from datetime import UTC, datetime, timedelta
from unittest import TestCase
from unittest.mock import MagicMock, patch

from jawed.api import app
from jawed.auth import create_access_token, hash_password
from jawed.database import (
    create_api_user,
    init_channel_db,
    init_master_db,
    register_channel_in_master,
    save_channel_config,
)
from tests.utils import MOCK_YOUTUBE_CHAT_POST_RESPONSE, temp_data_dir

TEST_JWT_SECRET = "test-secret-key"  # noqa: S105
TEST_REFRESH_TOKEN = "test-refresh-token"  # noqa: S105


class TestHealthCheck(TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_health_check(self):
        with temp_data_dir():
            init_master_db()
            response = self.app.get("/health")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["status"], "healthy")


class TestOpenAPISpec(TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_openapi_yaml_endpoint(self):
        with temp_data_dir():
            init_master_db()
            response = self.app.get("/openapi/spec/yaml/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content_type, "application/x-yaml")
            # Check that response contains OpenAPI spec structure
            content = response.data.decode("utf-8")
            self.assertIn("openapi:", content)
            self.assertIn("info:", content)
            self.assertIn("paths:", content)

    def test_openapi_json_endpoint(self):
        with temp_data_dir():
            init_master_db()
            response = self.app.get("/openapi/spec/json")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn("openapi", data)
            self.assertIn("info", data)
            self.assertEqual(data["info"]["title"], "YouTube Live Chat Assistant API")

    def test_openapi_swagger_ui_endpoint(self):
        with temp_data_dir():
            init_master_db()
            response = self.app.get("/openapi/spec/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/html", response.content_type)
            content = response.data.decode("utf-8")
            self.assertIn("swagger-ui", content)


class TestAuthEndpoints(TestCase):
    def setUp(self):
        os.environ["JWT_SECRET"] = TEST_JWT_SECRET
        self.app = app.test_client()
        self.app.testing = True

    def tearDown(self):
        if "JWT_SECRET" in os.environ:
            del os.environ["JWT_SECRET"]

    def test_register_user(self):
        with temp_data_dir():
            init_master_db()
            response = self.app.post(
                "/auth/register",
                json={"username": "newuser", "password": "password123"},
            )
            self.assertEqual(response.status_code, 201)
            data = response.get_json()
            self.assertEqual(data["user"]["username"], "newuser")
            self.assertIn("access_token", data)

    def test_register_user_duplicate(self):
        with temp_data_dir():
            init_master_db()
            self.app.post(
                "/auth/register",
                json={"username": "newuser", "password": "password123"},
            )
            response = self.app.post(
                "/auth/register",
                json={"username": "newuser", "password": "password123"},
            )
            self.assertEqual(response.status_code, 409)

    def test_login_success(self):
        with temp_data_dir():
            init_master_db()
            password_hash = hash_password("password123")
            create_api_user("user-123", "testuser", password_hash)

            response = self.app.post(
                "/auth/login",
                json={"username": "testuser", "password": "password123"},
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn("access_token", data)

    def test_login_invalid_credentials(self):
        with temp_data_dir():
            init_master_db()
            password_hash = hash_password("password123")
            create_api_user("user-123", "testuser", password_hash)

            response = self.app.post(
                "/auth/login",
                json={"username": "testuser", "password": "wrongpassword"},
            )
            self.assertEqual(response.status_code, 401)


class TestChannelEndpoints(TestCase):
    def setUp(self):
        os.environ["JWT_SECRET"] = TEST_JWT_SECRET
        self.app = app.test_client()
        self.app.testing = True

    def tearDown(self):
        if "JWT_SECRET" in os.environ:
            del os.environ["JWT_SECRET"]

    def _get_admin_token(self) -> str:
        return create_access_token("admin-123", "admin", is_admin=True)

    def _get_user_token(self) -> str:
        return create_access_token("user-123", "user", is_admin=False)

    def test_list_channels_requires_auth(self):
        with temp_data_dir():
            init_master_db()
            response = self.app.get("/channels/")
            self.assertEqual(response.status_code, 401)

    def test_list_channels_with_auth(self):
        with temp_data_dir():
            init_master_db()
            create_api_user("admin-123", "admin", hash_password("pass"), is_admin=True)
            token = self._get_admin_token()

            response = self.app.get(
                "/channels/",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 200)

    def test_register_channel_requires_admin(self):
        with temp_data_dir():
            init_master_db()
            create_api_user("user-123", "user", hash_password("pass"), is_admin=False)
            token = self._get_user_token()

            response = self.app.post(
                "/channels/",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel_id": "UC123", "channel_name": "Test Channel"},
            )
            self.assertEqual(response.status_code, 403)

    def test_register_channel_success(self):
        with temp_data_dir():
            init_master_db()
            create_api_user("admin-123", "admin", hash_password("pass"), is_admin=True)
            token = self._get_admin_token()

            response = self.app.post(
                "/channels/",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel_id": "UC123", "channel_name": "Test Channel"},
            )
            self.assertEqual(response.status_code, 201)
            data = response.get_json()
            self.assertEqual(data["channel"]["channel_id"], "UC123")

    def test_get_channel(self):
        with temp_data_dir():
            init_master_db()
            create_api_user("admin-123", "admin", hash_password("pass"), is_admin=True)
            register_channel_in_master("UC123", "Test Channel")
            init_channel_db("UC123")
            save_channel_config("UC123", "Test Channel")

            token = self._get_admin_token()
            response = self.app.get(
                "/channels/UC123",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 200)

    def test_check_accepting_requests_public(self):
        with temp_data_dir():
            init_master_db()
            register_channel_in_master("UC123", "Test Channel")
            init_channel_db("UC123")

            past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            save_channel_config(
                "UC123",
                "Test Channel",
                accepting_requests_start_datetime=past,
            )

            # This endpoint is public - no auth required
            response = self.app.get("/channels/UC123/accepting-requests")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["accepting_requests"])


class TestRequestEndpoints(TestCase):
    def setUp(self):
        os.environ["JWT_SECRET"] = TEST_JWT_SECRET
        self.app = app.test_client()
        self.app.testing = True

    def tearDown(self):
        if "JWT_SECRET" in os.environ:
            del os.environ["JWT_SECRET"]

    def _get_admin_token(self) -> str:
        return create_access_token("admin-123", "admin", is_admin=True)

    def _setup_channel_accepting_requests(self) -> None:
        init_master_db()
        register_channel_in_master("UC123", "Test Channel")
        init_channel_db("UC123")
        create_api_user("admin-123", "admin", hash_password("pass"), is_admin=True)

        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        save_channel_config(
            "UC123",
            "Test Channel",
            live_chat_id="LC123",
            accepting_requests_start_datetime=past,
            refresh_token=TEST_REFRESH_TOKEN,
        )

    def test_create_request_channel_not_accepting(self):
        with temp_data_dir():
            init_master_db()
            register_channel_in_master("UC123", "Test Channel")
            init_channel_db("UC123")
            save_channel_config("UC123", "Test Channel")  # No start datetime

            response = self.app.post(
                "/channels/UC123/requests/",
                json={
                    "requesting_username": "testuser",
                    "youtube_link": "https://youtube.com/watch?v=test123",
                },
            )
            self.assertEqual(response.status_code, 400)
            data = response.get_json()
            self.assertIn("not currently accepting", data["message"])

    @patch("jawed.api.post_request_to_youtube_chat")
    def test_create_request_success(self, mock_post: MagicMock):
        mock_post.return_value = MOCK_YOUTUBE_CHAT_POST_RESPONSE

        with temp_data_dir():
            self._setup_channel_accepting_requests()

            response = self.app.post(
                "/channels/UC123/requests/",
                json={
                    "requesting_username": "testuser",
                    "youtube_link": "https://youtube.com/watch?v=test123",
                    "youtube_link_title": "Test Video",
                    "user_message": "Please play this!",
                },
            )
            self.assertEqual(response.status_code, 201)
            data = response.get_json()
            self.assertEqual(data["request"]["requesting_username"], "testuser")
            self.assertEqual(data["request"]["status"], "posted")

    @patch("jawed.api.post_request_to_youtube_chat")
    def test_create_request_no_live_chat(self, mock_post: MagicMock):
        mock_post.return_value = None  # No active live chat

        with temp_data_dir():
            self._setup_channel_accepting_requests()

            response = self.app.post(
                "/channels/UC123/requests/",
                json={
                    "requesting_username": "testuser",
                    "youtube_link": "https://youtube.com/watch?v=test123",
                },
            )
            self.assertEqual(response.status_code, 201)
            data = response.get_json()
            self.assertEqual(data["request"]["status"], "pending")
            self.assertIn("chat_error", data)

    def test_list_requests_requires_auth(self):
        with temp_data_dir():
            init_master_db()
            register_channel_in_master("UC123", "Test Channel")

            response = self.app.get("/channels/UC123/requests/")
            self.assertEqual(response.status_code, 401)

    @patch("jawed.api.post_request_to_youtube_chat")
    def test_list_requests_with_auth(self, mock_post: MagicMock):
        mock_post.return_value = MOCK_YOUTUBE_CHAT_POST_RESPONSE

        with temp_data_dir():
            self._setup_channel_accepting_requests()

            # Create a request first
            self.app.post(
                "/channels/UC123/requests/",
                json={
                    "requesting_username": "testuser",
                    "youtube_link": "https://youtube.com/watch?v=test123",
                },
            )

            token = self._get_admin_token()
            response = self.app.get(
                "/channels/UC123/requests/",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(len(data["requests"]), 1)
