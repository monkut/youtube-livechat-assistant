"""Test utilities and fixtures."""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from jawed import database, definitions

# Mock YouTube API response for posting a chat message
MOCK_YOUTUBE_CHAT_POST_RESPONSE = {
    "kind": "youtube#liveChatMessage",
    "etag": "test-etag-123",
    "id": "mock-chat-message-id-123",
    "snippet": {
        "type": "textMessageEvent",
        "liveChatId": "test-live-chat-id",
        "authorChannelId": "test-author-channel-id",
        "publishedAt": "2024-01-01T00:00:00.000Z",
        "hasDisplayContent": True,
        "displayMessage": "Request from testuser: Test Video | https://youtube.com/watch?v=test123",
        "textMessageDetails": {
            "messageText": "Request from testuser: Test Video | https://youtube.com/watch?v=test123"
        },
    },
}

# Mock video list response with live chat ID
MOCK_YOUTUBE_VIDEO_LIST_RESPONSE = {
    "kind": "youtube#videoListResponse",
    "etag": "test-etag-456",
    "items": [
        {
            "kind": "youtube#video",
            "etag": "test-video-etag",
            "id": "test-video-id",
            "liveStreamingDetails": {
                "activeLiveChatId": "test-live-chat-id-from-video",
                "actualStartTime": "2024-01-01T00:00:00.000Z",
                "concurrentViewers": "100",
            },
        }
    ],
}

# Mock empty video response (no live chat)
MOCK_YOUTUBE_VIDEO_NO_CHAT_RESPONSE = {
    "kind": "youtube#videoListResponse",
    "etag": "test-etag-789",
    "items": [
        {
            "kind": "youtube#video",
            "etag": "test-video-etag",
            "id": "test-video-id",
            "liveStreamingDetails": {},
        }
    ],
}


@contextmanager
def temp_data_dir():
    """Create a temporary data directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        old_data_dir = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = tmpdir

        # Monkey-patch the DATA_DIR in definitions
        old_def_data_dir = definitions.DATA_DIR
        definitions.DATA_DIR = tmpdir
        database.DATA_DIR = tmpdir

        try:
            yield Path(tmpdir)
        finally:
            definitions.DATA_DIR = old_def_data_dir
            database.DATA_DIR = old_def_data_dir
            if old_data_dir:
                os.environ["DATA_DIR"] = old_data_dir
            elif "DATA_DIR" in os.environ:
                del os.environ["DATA_DIR"]
