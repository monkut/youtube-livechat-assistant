"""Constants and definitions for the YouTube Live Chat Assistant."""

# Database
MASTER_DB_NAME = "master.db"
CHANNEL_DB_PREFIX = "channel_"
DATA_DIR = "data"

# YouTube API
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# JWT
JWT_ALGORITHM = "HS256"
JWT_TOKEN_EXPIRE_HOURS = 24

# API
API_KEY_HEADER = "X-API-Key"
