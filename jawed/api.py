"""Flask API for YouTube Live Chat Assistant."""

import logging
import uuid
from typing import Any

import yaml
from flask import Flask, Response, current_app, request as flask_request
from flask.views import MethodView
from flask_smorest import Api, Blueprint, abort

from jawed.auth import (
    admin_required,
    authenticate_user,
    create_access_token,
    hash_password,
    jwt_required,
)
from jawed.database import (
    create_api_user,
    get_all_active_channels,
    get_api_user_by_username,
    get_channel_config,
    get_channel_from_master,
    get_channel_requests,
    get_request,
    init_channel_db,
    init_master_db,
    is_channel_accepting_requests,
    register_channel_in_master,
    save_channel_config,
    save_request,
    update_request_status,
)
from jawed.schemas import (
    AcceptingRequestsResponseSchema,
    AuthResponseSchema,
    ChannelConfigResponseSchema,
    ChannelConfigUpdateSchema,
    ChannelDetailResponseSchema,
    ChannelOAuthUpdateSchema,
    ChannelRegistrationSchema,
    ChannelResponseSchema,
    ChannelsListResponseSchema,
    ErrorSchema,
    HealthResponseSchema,
    RequestCreateSchema,
    RequestResponseSchema,
    RequestSchema,
    RequestsListResponseSchema,
    RequestStatusUpdateSchema,
    UserLoginSchema,
    UserRegistrationSchema,
)
from jawed.youtube_client import YouTubeClientError, post_request_to_youtube_chat

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    # Flask-Smorest configuration
    app.config["API_TITLE"] = "YouTube Live Chat Assistant API"
    app.config["API_VERSION"] = "v1"
    app.config["OPENAPI_VERSION"] = "3.0.3"
    app.config["OPENAPI_URL_PREFIX"] = "/openapi"
    app.config["OPENAPI_JSON_PATH"] = "spec/json"
    app.config["OPENAPI_SWAGGER_UI_PATH"] = "spec/"
    app.config["OPENAPI_SWAGGER_UI_URL"] = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"

    # Initialize the API
    api = Api(app)

    # Register blueprints
    api.register_blueprint(health_blp)
    api.register_blueprint(auth_blp)
    api.register_blueprint(channels_blp)
    api.register_blueprint(requests_blp)
    api.register_blueprint(openapi_blp)

    # Initialize databases
    with app.app_context():
        init_master_db()

    # Error handlers
    @app.errorhandler(500)
    def internal_error(_e: Exception) -> tuple[dict[str, str], int]:
        logger.exception("Internal server error")
        return {"error": "Internal server error"}, 500

    return app


# Health Blueprint
health_blp = Blueprint("health", __name__, url_prefix="/", description="Health check operations")


@health_blp.route("/health")
class Health(MethodView):
    @health_blp.response(200, HealthResponseSchema)
    def get(self):
        """Health check endpoint."""
        return {"status": "healthy", "service": "youtube-livechat-assistant"}


# Auth Blueprint
auth_blp = Blueprint("auth", __name__, url_prefix="/auth", description="Authentication operations")


@auth_blp.route("/register")
class UserRegistration(MethodView):
    @auth_blp.arguments(UserRegistrationSchema)
    @auth_blp.response(201, AuthResponseSchema)
    @auth_blp.alt_response(409, schema=ErrorSchema, description="Username already exists")
    def post(self, data: dict):
        """Register a new API user."""
        username = data["username"]
        password = data["password"]

        existing_user = get_api_user_by_username(username)
        if existing_user:
            abort(409, message="Username already exists")

        # First user becomes admin
        is_first_user = True  # TODO: implement proper check
        is_admin = data.get("is_admin", False) if not is_first_user else True

        user_id = str(uuid.uuid4())
        password_hash = hash_password(password)

        try:
            user = create_api_user(user_id, username, password_hash, is_admin)
            token = create_access_token(user_id, username, is_admin)
        except Exception:
            logger.exception("Failed to create user")
            abort(500, message="Failed to create user")
        else:
            return {"user": user, "access_token": token}


@auth_blp.route("/login")
class UserLogin(MethodView):
    @auth_blp.arguments(UserLoginSchema)
    @auth_blp.response(200, AuthResponseSchema)
    @auth_blp.alt_response(401, schema=ErrorSchema, description="Invalid credentials")
    def post(self, data: dict):
        """Authenticate and get a JWT token."""
        username = data["username"]
        password = data["password"]

        user = authenticate_user(username, password)
        if not user:
            abort(401, message="Invalid credentials")

        token = create_access_token(user["user_id"], user["username"], bool(user["is_admin"]))
        return {
            "access_token": token,
            "user": {
                "user_id": user["user_id"],
                "username": user["username"],
                "is_admin": bool(user["is_admin"]),
            },
        }


# Channels Blueprint
channels_blp = Blueprint("channels", __name__, url_prefix="/channels", description="Channel management operations")


@channels_blp.route("/")
class ChannelsList(MethodView):
    @channels_blp.response(200, ChannelsListResponseSchema)
    @channels_blp.alt_response(401, schema=ErrorSchema, description="Authentication required")
    @jwt_required
    def get(self):
        """List all active channels."""
        channels = get_all_active_channels()
        return {"channels": channels}

    @channels_blp.arguments(ChannelRegistrationSchema)
    @channels_blp.response(201, ChannelResponseSchema)
    @channels_blp.alt_response(401, schema=ErrorSchema, description="Authentication required")
    @channels_blp.alt_response(403, schema=ErrorSchema, description="Admin privileges required")
    @channels_blp.alt_response(409, schema=ErrorSchema, description="Channel already registered")
    @jwt_required
    @admin_required
    def post(self, data: dict):
        """Register a new YouTube channel."""
        channel_id = data["channel_id"]
        channel_name = data["channel_name"]

        existing = get_channel_from_master(channel_id)
        if existing:
            abort(409, message="Channel already registered")

        channel = register_channel_in_master(channel_id, channel_name)
        init_channel_db(channel_id)
        save_channel_config(channel_id=channel_id, channel_name=channel_name)

        logger.info(f"Registered channel: {channel_id}")
        return {"channel": channel}


@channels_blp.route("/<channel_id>")
class ChannelDetail(MethodView):
    @channels_blp.response(200, ChannelDetailResponseSchema)
    @channels_blp.alt_response(401, schema=ErrorSchema, description="Authentication required")
    @channels_blp.alt_response(404, schema=ErrorSchema, description="Channel not found")
    @jwt_required
    def get(self, channel_id: str):
        """Get channel details."""
        channel = get_channel_from_master(channel_id)
        if not channel:
            abort(404, message="Channel not found")

        config = get_channel_config(channel_id)
        return {"channel": channel, "config": config}


@channels_blp.route("/<channel_id>/config")
class ChannelConfig(MethodView):
    @channels_blp.arguments(ChannelConfigUpdateSchema)
    @channels_blp.response(200, ChannelConfigResponseSchema)
    @channels_blp.alt_response(401, schema=ErrorSchema, description="Authentication required")
    @channels_blp.alt_response(403, schema=ErrorSchema, description="Admin privileges required")
    @channels_blp.alt_response(404, schema=ErrorSchema, description="Channel not found")
    @jwt_required
    @admin_required
    def put(self, data: dict, channel_id: str):
        """Update channel configuration."""
        channel = get_channel_from_master(channel_id)
        if not channel:
            abort(404, message="Channel not found")

        config = save_channel_config(
            channel_id=channel_id,
            channel_name=data.get("channel_name", channel["channel_name"]),
            live_chat_id=data.get("live_chat_id"),
            accepting_requests_start_datetime=data.get("accepting_requests_start_datetime"),
            accepting_requests_end_datetime=data.get("accepting_requests_end_datetime"),
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            token_expiry=data.get("token_expiry"),
        )
        return {"config": config}


@channels_blp.route("/<channel_id>/oauth")
class ChannelOAuth(MethodView):
    @channels_blp.arguments(ChannelOAuthUpdateSchema)
    @channels_blp.response(200, ChannelConfigResponseSchema)
    @channels_blp.alt_response(401, schema=ErrorSchema, description="Authentication required")
    @channels_blp.alt_response(403, schema=ErrorSchema, description="Admin privileges required")
    @channels_blp.alt_response(404, schema=ErrorSchema, description="Channel not found")
    @jwt_required
    @admin_required
    def put(self, data: dict, channel_id: str):
        """Update channel OAuth tokens."""
        channel = get_channel_from_master(channel_id)
        if not channel:
            abort(404, message="Channel not found")

        config = save_channel_config(
            channel_id=channel_id,
            channel_name=channel["channel_name"],
            access_token=data.get("access_token"),
            refresh_token=data["refresh_token"],
            token_expiry=data.get("token_expiry"),
        )
        return {"config": config}


@channels_blp.route("/<channel_id>/accepting-requests")
class ChannelAcceptingRequests(MethodView):
    @channels_blp.response(200, AcceptingRequestsResponseSchema)
    @channels_blp.alt_response(404, schema=ErrorSchema, description="Channel not found")
    def get(self, channel_id: str):
        """Check if a channel is currently accepting requests (public endpoint)."""
        channel = get_channel_from_master(channel_id)
        if not channel:
            abort(404, message="Channel not found")

        accepting = is_channel_accepting_requests(channel_id)
        return {"channel_id": channel_id, "accepting_requests": accepting}


# Requests Blueprint
requests_blp = Blueprint(
    "requests",
    __name__,
    url_prefix="/channels/<channel_id>/requests",
    description="Request management operations",
)


@requests_blp.route("/")
class RequestsList(MethodView):
    @requests_blp.response(200, RequestsListResponseSchema)
    @requests_blp.alt_response(401, schema=ErrorSchema, description="Authentication required")
    @requests_blp.alt_response(404, schema=ErrorSchema, description="Channel not found")
    @jwt_required
    def get(self, channel_id: str):
        """List requests for a channel."""
        channel = get_channel_from_master(channel_id)
        if not channel:
            abort(404, message="Channel not found")

        status = flask_request.args.get("status")
        limit = flask_request.args.get("limit", 100, type=int)

        requests_list = get_channel_requests(channel_id, status=status, limit=limit)
        return {"requests": requests_list}

    @requests_blp.arguments(RequestCreateSchema)
    @requests_blp.response(201, RequestResponseSchema)
    @requests_blp.alt_response(400, schema=ErrorSchema, description="Channel not accepting requests")
    @requests_blp.alt_response(404, schema=ErrorSchema, description="Channel not found")
    def post(self, data: dict, channel_id: str):
        """Register a new request for a channel.

        This endpoint is public - anyone can submit a request if the channel
        is currently accepting requests.
        """
        channel = get_channel_from_master(channel_id)
        if not channel:
            abort(404, message="Channel not found")

        if not is_channel_accepting_requests(channel_id):
            abort(400, message="Channel is not currently accepting requests")

        requesting_username = data["requesting_username"]
        youtube_link = data["youtube_link"]
        youtube_link_title = data.get("youtube_link_title")
        user_message = data.get("user_message")

        request_id = str(uuid.uuid4())

        new_request = save_request(
            channel_id=channel_id,
            request_id=request_id,
            requesting_username=requesting_username,
            youtube_link=youtube_link,
            youtube_link_title=youtube_link_title,
            user_message=user_message,
        )

        chat_error = None
        try:
            response = post_request_to_youtube_chat(
                channel_id=channel_id,
                requesting_username=requesting_username,
                youtube_link=youtube_link,
                youtube_link_title=youtube_link_title,
                user_message=user_message,
            )
            if response:
                chat_message_id = response.get("id")
                update_request_status(channel_id, request_id, "posted", chat_message_id)
                new_request["status"] = "posted"
                new_request["chat_message_id"] = chat_message_id
            else:
                update_request_status(channel_id, request_id, "pending")
                chat_error = "No active live chat found"

        except YouTubeClientError as e:
            logger.exception("Failed to post to YouTube chat")
            update_request_status(channel_id, request_id, "failed")
            new_request["status"] = "failed"
            chat_error = str(e)

        response_data: dict[str, Any] = {"request": new_request}
        if chat_error:
            response_data["chat_error"] = chat_error

        return response_data


@requests_blp.route("/<request_id>")
class RequestDetail(MethodView):
    @requests_blp.response(200, RequestResponseSchema)
    @requests_blp.alt_response(401, schema=ErrorSchema, description="Authentication required")
    @requests_blp.alt_response(404, schema=ErrorSchema, description="Request not found")
    @jwt_required
    def get(self, channel_id: str, request_id: str):
        """Get details of a specific request."""
        channel = get_channel_from_master(channel_id)
        if not channel:
            abort(404, message="Channel not found")

        req = get_request(channel_id, request_id)
        if not req:
            abort(404, message="Request not found")

        return {"request": req}


@requests_blp.route("/<request_id>/status")
class RequestStatus(MethodView):
    @requests_blp.arguments(RequestStatusUpdateSchema)
    @requests_blp.response(200, RequestSchema)
    @requests_blp.alt_response(401, schema=ErrorSchema, description="Authentication required")
    @requests_blp.alt_response(403, schema=ErrorSchema, description="Admin privileges required")
    @requests_blp.alt_response(404, schema=ErrorSchema, description="Request not found")
    @jwt_required
    @admin_required
    def put(self, data: dict, channel_id: str, request_id: str):
        """Update the status of a request."""
        channel = get_channel_from_master(channel_id)
        if not channel:
            abort(404, message="Channel not found")

        req = get_request(channel_id, request_id)
        if not req:
            abort(404, message="Request not found")

        status = data["status"]
        updated = update_request_status(channel_id, request_id, status)
        return updated


# OpenAPI YAML Blueprint
openapi_blp = Blueprint("openapi_yaml", __name__, url_prefix="/openapi", description="OpenAPI specification")


@openapi_blp.route("/spec/yaml/")
class OpenAPIYaml(MethodView):
    def get(self):
        """Get the OpenAPI specification as YAML."""
        # Get the Api object from flask-smorest extension
        api_obj = current_app.extensions.get("flask-smorest", {}).get("apis", {}).get("", {}).get("ext_obj")
        if api_obj is None:
            return Response("OpenAPI spec not available", status=500, mimetype="text/plain")

        spec_dict = api_obj.spec.to_dict()
        yaml_content = yaml.dump(spec_dict, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return Response(yaml_content, mimetype="application/x-yaml")


# Create the app instance for Zappa
app = create_app()


if __name__ == "__main__":
    import os

    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, port=5000)
