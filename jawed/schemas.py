"""Marshmallow schemas for API request/response validation."""

from marshmallow import Schema, fields, validate


# Error schemas
class ErrorSchema(Schema):
    """Schema for error responses."""

    error = fields.String(required=True, metadata={"description": "Error message"})


# Auth schemas
class UserRegistrationSchema(Schema):
    """Schema for user registration request."""

    username = fields.String(
        required=True,
        validate=validate.Length(min=3, max=50),
        metadata={"description": "Username for the new account"},
    )
    password = fields.String(
        required=True,
        validate=validate.Length(min=8),
        load_only=True,
        metadata={"description": "Password for the new account"},
    )
    is_admin = fields.Boolean(
        load_default=False,
        metadata={"description": "Whether the user should have admin privileges"},
    )


class UserLoginSchema(Schema):
    """Schema for user login request."""

    username = fields.String(required=True, metadata={"description": "Username"})
    password = fields.String(required=True, load_only=True, metadata={"description": "Password"})


class UserSchema(Schema):
    """Schema for user data in responses."""

    user_id = fields.String(metadata={"description": "Unique user identifier"})
    username = fields.String(metadata={"description": "Username"})
    is_admin = fields.Boolean(metadata={"description": "Whether user has admin privileges"})
    created_at = fields.String(metadata={"description": "Account creation timestamp"})


class AuthResponseSchema(Schema):
    """Schema for authentication response."""

    access_token = fields.String(required=True, metadata={"description": "JWT access token"})
    user = fields.Nested(UserSchema, metadata={"description": "User information"})


# Channel schemas
class ChannelRegistrationSchema(Schema):
    """Schema for channel registration request."""

    channel_id = fields.String(
        required=True,
        metadata={"description": "YouTube channel ID"},
    )
    channel_name = fields.String(
        required=True,
        validate=validate.Length(min=1, max=200),
        metadata={"description": "Display name for the channel"},
    )


class ChannelSchema(Schema):
    """Schema for channel data."""

    channel_id = fields.String(metadata={"description": "YouTube channel ID"})
    channel_name = fields.String(metadata={"description": "Channel display name"})
    is_active = fields.Boolean(metadata={"description": "Whether the channel is active"})
    created_at = fields.String(metadata={"description": "Registration timestamp"})
    updated_at = fields.String(metadata={"description": "Last update timestamp"})


class ChannelConfigSchema(Schema):
    """Schema for channel configuration."""

    channel_id = fields.String(metadata={"description": "YouTube channel ID"})
    channel_name = fields.String(metadata={"description": "Channel display name"})
    live_chat_id = fields.String(allow_none=True, metadata={"description": "Active live chat ID"})
    accepting_requests_start_datetime = fields.String(
        allow_none=True,
        metadata={"description": "ISO datetime when channel started accepting requests"},
    )
    accepting_requests_end_datetime = fields.String(
        allow_none=True,
        metadata={"description": "ISO datetime when channel stopped accepting requests (NULL if still accepting)"},
    )
    created_at = fields.String(metadata={"description": "Configuration creation timestamp"})
    updated_at = fields.String(metadata={"description": "Last update timestamp"})


class ChannelConfigUpdateSchema(Schema):
    """Schema for updating channel configuration."""

    channel_name = fields.String(
        validate=validate.Length(min=1, max=200),
        metadata={"description": "Channel display name"},
    )
    live_chat_id = fields.String(
        allow_none=True,
        metadata={"description": "Active live chat ID"},
    )
    accepting_requests_start_datetime = fields.String(
        allow_none=True,
        metadata={"description": "ISO datetime to start accepting requests"},
    )
    accepting_requests_end_datetime = fields.String(
        allow_none=True,
        metadata={"description": "ISO datetime to stop accepting requests"},
    )
    access_token = fields.String(
        load_only=True,
        metadata={"description": "YouTube OAuth access token"},
    )
    refresh_token = fields.String(
        load_only=True,
        metadata={"description": "YouTube OAuth refresh token"},
    )
    token_expiry = fields.String(
        metadata={"description": "OAuth token expiry timestamp"},
    )


class ChannelOAuthUpdateSchema(Schema):
    """Schema for updating channel OAuth tokens."""

    access_token = fields.String(
        load_only=True,
        metadata={"description": "YouTube OAuth access token"},
    )
    refresh_token = fields.String(
        required=True,
        load_only=True,
        metadata={"description": "YouTube OAuth refresh token"},
    )
    token_expiry = fields.String(
        metadata={"description": "OAuth token expiry timestamp"},
    )


class ChannelResponseSchema(Schema):
    """Schema for channel response."""

    channel = fields.Nested(ChannelSchema, metadata={"description": "Channel data"})


class ChannelDetailResponseSchema(Schema):
    """Schema for channel detail response."""

    channel = fields.Nested(ChannelSchema, metadata={"description": "Channel data"})
    config = fields.Nested(
        ChannelConfigSchema,
        allow_none=True,
        metadata={"description": "Channel configuration"},
    )


class ChannelConfigResponseSchema(Schema):
    """Schema for channel config response."""

    config = fields.Nested(ChannelConfigSchema, metadata={"description": "Channel configuration"})


class ChannelsListResponseSchema(Schema):
    """Schema for channels list response."""

    channels = fields.List(
        fields.Nested(ChannelSchema),
        metadata={"description": "List of channels"},
    )


class AcceptingRequestsResponseSchema(Schema):
    """Schema for accepting requests check response."""

    channel_id = fields.String(metadata={"description": "YouTube channel ID"})
    accepting_requests = fields.Boolean(
        metadata={"description": "Whether the channel is currently accepting requests"},
    )


# Request schemas
class RequestCreateSchema(Schema):
    """Schema for creating a new request."""

    requesting_username = fields.String(
        required=True,
        validate=validate.Length(min=1, max=100),
        metadata={"description": "Username of the person making the request"},
    )
    youtube_link = fields.String(
        required=True,
        validate=validate.URL(),
        metadata={"description": "YouTube video URL"},
    )
    youtube_link_title = fields.String(
        validate=validate.Length(max=200),
        metadata={"description": "Title of the YouTube video"},
    )
    user_message = fields.String(
        validate=validate.Length(max=500),
        metadata={"description": "Optional message from the requester"},
    )


class RequestSchema(Schema):
    """Schema for request data."""

    request_id = fields.String(metadata={"description": "Unique request identifier"})
    requesting_username = fields.String(metadata={"description": "Username of the requester"})
    youtube_link = fields.String(metadata={"description": "YouTube video URL"})
    youtube_link_title = fields.String(allow_none=True, metadata={"description": "Video title"})
    user_message = fields.String(allow_none=True, metadata={"description": "Requester's message"})
    status = fields.String(metadata={"description": "Request status (pending, posted, played, skipped, failed)"})
    chat_message_id = fields.String(allow_none=True, metadata={"description": "YouTube chat message ID if posted"})
    created_at = fields.String(metadata={"description": "Request creation timestamp"})
    processed_at = fields.String(allow_none=True, metadata={"description": "Request processing timestamp"})


class RequestResponseSchema(Schema):
    """Schema for request response."""

    request = fields.Nested(RequestSchema, metadata={"description": "Request data"})
    chat_error = fields.String(
        metadata={"description": "Error message if posting to chat failed"},
    )


class RequestsListResponseSchema(Schema):
    """Schema for requests list response."""

    requests = fields.List(
        fields.Nested(RequestSchema),
        metadata={"description": "List of requests"},
    )


class RequestStatusUpdateSchema(Schema):
    """Schema for updating request status."""

    status = fields.String(
        required=True,
        validate=validate.OneOf(["pending", "posted", "played", "skipped", "failed"]),
        metadata={"description": "New status for the request"},
    )


# Health check schema
class HealthResponseSchema(Schema):
    """Schema for health check response."""

    status = fields.String(metadata={"description": "Service status"})
    service = fields.String(metadata={"description": "Service name"})
