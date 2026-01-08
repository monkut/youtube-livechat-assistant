"""JWT authentication utilities for the API."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import TYPE_CHECKING

import jwt
from flask import Response, jsonify, request

from jawed.database import get_api_user_by_id, get_api_user_by_username
from jawed.definitions import JWT_ALGORITHM, JWT_TOKEN_EXPIRE_HOURS

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def get_jwt_secret() -> str:
    """Get the JWT secret from environment or raise an error."""
    secret = os.getenv("JWT_SECRET")
    if not secret:
        msg = "JWT_SECRET environment variable must be set"
        raise ValueError(msg)
    return secret


def hash_password(password: str) -> str:
    """Hash a password using SHA-256 with a salt."""
    salt = secrets.token_hex(16)
    password_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{password_hash}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    try:
        salt, stored_hash = password_hash.split(":")
        computed_hash = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return secrets.compare_digest(computed_hash, stored_hash)
    except ValueError:
        return False


def create_access_token(user_id: str, username: str, is_admin: bool = False) -> str:
    """Create a JWT access token."""
    payload = {
        "sub": user_id,
        "username": username,
        "is_admin": is_admin,
        "exp": datetime.now(UTC) + timedelta(hours=JWT_TOKEN_EXPIRE_HOURS),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        return None
    else:
        return payload


def authenticate_user(username: str, password: str) -> dict | None:
    """Authenticate a user by username and password."""
    user = get_api_user_by_username(username)
    if not user:
        return None

    if not verify_password(password, user["password_hash"]):
        return None

    return user


def jwt_required(f: Callable):  # type: ignore[type-arg]
    """Decorator to require JWT authentication."""

    @wraps(f)
    def decorated(*args, **kwargs) -> tuple[Response, int] | object:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return jsonify({"error": "Missing Authorization header"}), 401

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":  # noqa: PLR2004
            return jsonify({"error": "Invalid Authorization header format. Use 'Bearer <token>'"}), 401

        token = parts[1]
        payload = decode_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        # Verify user still exists
        user = get_api_user_by_id(payload["sub"])
        if not user:
            return jsonify({"error": "User not found"}), 401

        # Add user info to request context
        request.current_user = payload  # type: ignore[attr-defined]
        return f(*args, **kwargs)

    return decorated


def admin_required(f: Callable):  # type: ignore[type-arg]
    """Decorator to require admin privileges."""

    @wraps(f)
    def decorated(*args, **kwargs) -> tuple[Response, int] | object:
        # jwt_required must be called first
        if not hasattr(request, "current_user"):
            return jsonify({"error": "Authentication required"}), 401

        if not request.current_user.get("is_admin"):  # type: ignore[attr-defined]
            return jsonify({"error": "Admin privileges required"}), 403

        return f(*args, **kwargs)

    return decorated
