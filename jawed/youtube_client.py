"""YouTube API client for live chat operations."""

import logging
from datetime import UTC, datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from jawed.database import get_channel_config, save_channel_config
from jawed.definitions import YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, YOUTUBE_SCOPES

logger = logging.getLogger(__name__)

# Google OAuth2 token endpoint
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105


class YouTubeClientError(Exception):
    """Exception for YouTube client errors."""


class YouTubeClient:
    """Client for interacting with YouTube Live Chat API."""

    def __init__(self, channel_id: str) -> None:
        self.channel_id = channel_id
        self._service = None

    def _get_credentials(self) -> Credentials | None:
        """Get OAuth credentials from the channel database."""
        config = get_channel_config(self.channel_id)
        if not config:
            logger.error(f"No configuration found for channel {self.channel_id}")
            return None

        access_token = config.get("access_token")
        refresh_token = config.get("refresh_token")
        token_expiry = config.get("token_expiry")

        if not refresh_token:
            logger.error(f"No refresh token found for channel {self.channel_id}")
            return None

        expiry = None
        if token_expiry:
            expiry = datetime.fromisoformat(token_expiry)

        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=GOOGLE_TOKEN_URI,
            scopes=YOUTUBE_SCOPES,
            expiry=expiry,
        )

        # Refresh token if expired or about to expire
        if credentials.expired or (
            credentials.expiry and credentials.expiry < datetime.now(UTC) + timedelta(minutes=5)
        ):
            logger.info(f"Refreshing access token for channel {self.channel_id}")
            credentials.refresh(Request())

            # Save the new tokens
            config = get_channel_config(self.channel_id)
            if config:
                save_channel_config(
                    channel_id=self.channel_id,
                    channel_name=config.get("channel_name", ""),
                    access_token=credentials.token,
                    token_expiry=credentials.expiry.isoformat() if credentials.expiry else None,
                )

        return credentials

    def _get_service(self):  # noqa: ANN202
        """Get an authenticated YouTube API service."""
        if self._service:
            return self._service

        credentials = self._get_credentials()
        if not credentials:
            msg = f"Failed to get credentials for channel {self.channel_id}"
            raise YouTubeClientError(msg)

        self._service = build(
            YOUTUBE_API_SERVICE_NAME,
            YOUTUBE_API_VERSION,
            credentials=credentials,
        )
        return self._service

    def get_live_chat_id(self, video_id: str) -> str | None:
        """Get the live chat ID for a video."""
        service = self._get_service()

        try:
            response = service.videos().list(part="liveStreamingDetails", id=video_id).execute()
        except HttpError as e:
            logger.exception("Error getting live chat ID")
            raise YouTubeClientError(f"Failed to get live chat ID: {e}") from e

        items = response.get("items", [])
        if not items:
            logger.warning(f"Video not found: {video_id}")
            return None

        video = items[0]
        live_details = video.get("liveStreamingDetails", {})
        live_chat_id = live_details.get("activeLiveChatId")

        if not live_chat_id:
            logger.warning(f"No active live chat found for video: {video_id}")
            return None

        return live_chat_id

    def post_chat_message(self, live_chat_id: str, message: str) -> dict:
        """Post a message to the live chat.

        Args:
            live_chat_id: The ID of the live chat
            message: The message text to post

        Returns:
            The API response containing the posted message details
        """
        service = self._get_service()

        body = {
            "snippet": {
                "liveChatId": live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": message},
            }
        }

        try:
            response = service.liveChatMessages().insert(part="snippet", body=body).execute()
        except HttpError as e:
            logger.exception("Error posting chat message")
            raise YouTubeClientError(f"Failed to post chat message: {e}") from e

        logger.info(f"Posted message to live chat {live_chat_id}")
        return response

    def post_request_to_chat(
        self,
        live_chat_id: str,
        requesting_username: str,
        youtube_link_title: str,
        youtube_link: str,
        user_message: str | None = None,
    ) -> dict:
        """Post a formatted request message to the live chat.

        Args:
            live_chat_id: The ID of the live chat
            requesting_username: The username of the person making the request
            youtube_link_title: The title of the requested video
            youtube_link: The YouTube link
            user_message: Optional message from the user

        Returns:
            The API response containing the posted message details
        """
        # Build the message
        message_parts = [
            f"Request from {requesting_username}:",
            f"{youtube_link_title}",
            youtube_link,
        ]

        if user_message:
            message_parts.append(f'"{user_message}"')

        message = " | ".join(message_parts)

        return self.post_chat_message(live_chat_id, message)


def post_request_to_youtube_chat(
    channel_id: str,
    requesting_username: str,
    youtube_link: str,
    youtube_link_title: str | None = None,
    user_message: str | None = None,
) -> dict | None:
    """Post a request to a channel's YouTube live chat.

    Args:
        channel_id: The YouTube channel ID
        requesting_username: The username of the requester
        youtube_link: The YouTube video link
        youtube_link_title: The title of the video (optional)
        user_message: Additional message from the user (optional)

    Returns:
        The API response if successful, None if no live chat is active
    """
    config = get_channel_config(channel_id)
    if not config:
        logger.error(f"No configuration found for channel {channel_id}")
        return None

    live_chat_id = config.get("live_chat_id")
    if not live_chat_id:
        logger.warning(f"No active live chat ID for channel {channel_id}")
        return None

    client = YouTubeClient(channel_id)
    return client.post_request_to_chat(
        live_chat_id=live_chat_id,
        requesting_username=requesting_username,
        youtube_link_title=youtube_link_title or youtube_link,
        youtube_link=youtube_link,
        user_message=user_message,
    )
