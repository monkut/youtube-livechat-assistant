r"""Phase 0: Capture liveChatMessages.list payload for live API diff.

Fetches one or more pages of liveChatMessages.list from an active YouTube
live stream and writes the raw JSON response(s) to disk for field-by-field
comparison against the VOD yt-dlp subtitle shape used by resolver_bench.py.

Usage:
    # Via video ID (resolves liveChatId automatically)
    uv run scripts/capture_live_payload.py --video-id <VIDEO_ID>

    # Via direct liveChatId
    uv run scripts/capture_live_payload.py --live-chat-id <LIVE_CHAT_ID>

    # Multi-page capture
    uv run scripts/capture_live_payload.py --video-id <VIDEO_ID> --max-pages 5

    # Override output location
    uv run scripts/capture_live_payload.py --video-id <VIDEO_ID> \\
        --output-dir /tmp/captures --output-file my-capture.json

Environment:
    YOUTUBE_API_KEY — YouTube Data API v3 key (required unless --api-key is passed)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build

from jawed.definitions import YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION

DEFAULT_OUTPUT_DIR = "docs/phase0"
LIVE_CHAT_MESSAGES_PARTS = ["snippet", "authorDetails"]


def resolve_live_chat_id(youtube: Any, video_id: str) -> str:
    """Return the liveChatId for an active live stream video."""
    response = (
        youtube.videos()
        .list(
            part="liveStreamingDetails",
            id=video_id,
        )
        .execute()
    )

    items = response.get("items", [])
    if not items:
        print(f"ERROR: video '{video_id}' not found or not accessible.", file=sys.stderr)
        sys.exit(1)

    streaming_details = items[0].get("liveStreamingDetails", {})
    live_chat_id = streaming_details.get("activeLiveChatId")
    if not live_chat_id:
        print(
            f"ERROR: video '{video_id}' has no activeLiveChatId. "
            "The stream may not be live or the liveChatId has expired.",
            file=sys.stderr,
        )
        sys.exit(1)

    return live_chat_id


def fetch_pages(youtube: Any, live_chat_id: str, max_pages: int) -> list[dict[str, Any]]:
    """Fetch up to max_pages of liveChatMessages.list and return all responses."""
    pages: list[dict[str, Any]] = []
    page_token: str | None = None

    for page_num in range(1, max_pages + 1):
        kwargs: dict[str, Any] = {
            "liveChatId": live_chat_id,
            "part": LIVE_CHAT_MESSAGES_PARTS,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        response = youtube.liveChatMessages().list(**kwargs).execute()
        pages.append(response)
        print(f"  Page {page_num}: {len(response.get('items', []))} messages fetched")

        page_token = response.get("nextPageToken")
        if not page_token:
            print(f"  No further pages after page {page_num}.")
            break

    return pages


def count_superchats(pages: list[dict[str, Any]]) -> int:
    """Count liveChatPaidMessageRenderer (SuperChat) items across all pages."""
    total = 0
    for page in pages:
        for item in page.get("items", []):
            snippet = item.get("snippet", {})
            if snippet.get("type") == "superChatEvent":
                total += 1
    return total


def build_output_path(output_dir: str, output_file: str | None) -> Path:
    """Resolve the output file path, creating parent directories as needed."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    today = datetime.now(tz=UTC).date()
    filename = output_file if output_file else f"live-payload-{today.isoformat()}.json"
    return directory / filename


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture liveChatMessages.list payload for Phase 0 live API diff",
    )

    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument(
        "--live-chat-id",
        metavar="LIVE_CHAT_ID",
        help="Direct liveChatId of an active stream",
    )
    id_group.add_argument(
        "--video-id",
        metavar="VIDEO_ID",
        help="YouTube video ID — liveChatId is resolved via liveStreamingDetails",
    )

    parser.add_argument(
        "--api-key",
        default=os.getenv("YOUTUBE_API_KEY"),
        help="YouTube Data API v3 key (default: $YOUTUBE_API_KEY)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        metavar="N",
        help="Maximum number of liveChatMessages.list pages to fetch (default: 1)",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the output JSON file (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="Override output filename (default: live-payload-YYYY-MM-DD.json)",
    )

    args = parser.parse_args()

    if not args.api_key:
        print(
            "ERROR: YouTube API key required. Pass --api-key or set $YOUTUBE_API_KEY.",
            file=sys.stderr,
        )
        sys.exit(1)

    youtube = build(
        YOUTUBE_API_SERVICE_NAME,
        YOUTUBE_API_VERSION,
        developerKey=args.api_key,
    )

    # Resolve liveChatId
    if args.live_chat_id:
        live_chat_id = args.live_chat_id
        print(f"Using liveChatId: {live_chat_id}")
    else:
        print(f"Resolving liveChatId from video '{args.video_id}' …")
        live_chat_id = resolve_live_chat_id(youtube, args.video_id)
        print(f"Resolved liveChatId: {live_chat_id}")

    # Fetch pages
    print(f"Fetching up to {args.max_pages} page(s) …")
    pages = fetch_pages(youtube, live_chat_id, args.max_pages)

    # Build output
    output_path = build_output_path(args.output_dir, args.output_file)
    payload = pages[0] if len(pages) == 1 else pages
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    # Summary
    total_messages = sum(len(p.get("items", [])) for p in pages)
    superchat_count = count_superchats(pages)

    print()
    print("=== Capture summary ===")
    print(f"  Pages fetched : {len(pages)}")
    print(f"  Total messages: {total_messages}")
    print(f"  SuperChats    : {superchat_count}")
    print(f"  Output        : {output_path}")


if __name__ == "__main__":
    main()
