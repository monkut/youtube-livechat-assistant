"""Tests for authentication module."""

import os
from datetime import UTC, datetime, timedelta
from unittest import TestCase
from unittest.mock import patch

from jawed.auth import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)

TEST_PASSWORD = "secretpassword123"  # noqa: S105
TEST_JWT_SECRET = "test-secret-key-for-testing"  # noqa: S105


class TestPasswordHashing(TestCase):
    def test_hash_password(self):
        hashed = hash_password(TEST_PASSWORD)

        # Hash should contain salt and hash separated by colon
        self.assertIn(":", hashed)
        parts = hashed.split(":")
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0]), 32)  # Salt is 16 bytes hex = 32 chars

    def test_verify_password_correct(self):
        hashed = hash_password(TEST_PASSWORD)

        self.assertTrue(verify_password(TEST_PASSWORD, hashed))

    def test_verify_password_incorrect(self):
        hashed = hash_password(TEST_PASSWORD)

        self.assertFalse(verify_password("wrongpassword", hashed))

    def test_verify_password_invalid_hash(self):
        self.assertFalse(verify_password("password", "invalidhash"))


class TestJWT(TestCase):
    def setUp(self):
        os.environ["JWT_SECRET"] = TEST_JWT_SECRET

    def tearDown(self):
        if "JWT_SECRET" in os.environ:
            del os.environ["JWT_SECRET"]

    def test_create_access_token(self):
        token = create_access_token("user-123", "testuser", is_admin=False)
        self.assertIsInstance(token, str)
        self.assertTrue(len(token) > 0)

    def test_decode_token_valid(self):
        token = create_access_token("user-123", "testuser", is_admin=True)
        payload = decode_token(token)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "user-123")
        self.assertEqual(payload["username"], "testuser")
        self.assertTrue(payload["is_admin"])

    def test_decode_token_invalid(self):
        payload = decode_token("invalid.token.here")
        self.assertIsNone(payload)

    def test_decode_token_expired(self):
        # Create a token that's already expired
        with patch("jawed.auth.datetime") as mock_datetime:
            # Set time in the past for token creation
            past_time = datetime.now(UTC) - timedelta(hours=48)
            mock_datetime.now.return_value = past_time

            token = create_access_token("user-123", "testuser")

        # Token should be expired now
        payload = decode_token(token)
        self.assertIsNone(payload)
