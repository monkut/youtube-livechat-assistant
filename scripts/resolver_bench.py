"""Phase 0: LLM resolver accuracy benchmark.

Pulls SuperChat messages from a VOD, runs an LLM resolver, and prints a
markdown table of results for hand-scoring.

Usage:
    uv run scripts/resolver_bench.py <VOD_URL> [--limit N] [--model MODEL]

Environment:
    ANTHROPIC_API_KEY — required for claude-haiku-4-5 (default model)
    YOUTUBE_API_KEY   — required for YouTube search candidates
    OPENAI_API_KEY    — required for gpt-4o-mini model
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic.types import ToolParam
from chat_downloader import ChatDownloader  # type: ignore[import-untyped]
from googleapiclient.discovery import build

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CANDIDATE_TITLE_MAX_LEN = 30
GPT_MODEL = "gpt-4o-mini"
YOUTUBE_API_VERSION = "v3"
YOUTUBE_SERVICE_NAME = "youtube"

RESOLVE_TOOL_NAME = "resolve_music_request"
RESOLVE_TOOL: ToolParam = {
    "name": RESOLVE_TOOL_NAME,
    "description": (
        "Parse a live-chat message to identify a music track request. "
        "Return the artist, track title, a confidence score, and a "
        "fallback search query."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "artist": {
                "type": "string",
                "description": "Artist name extracted from the message, or empty string if unknown.",
            },
            "track": {
                "type": "string",
                "description": "Track title extracted from the message, or empty string if unknown.",
            },
            "confidence": {
                "type": "number",
                "description": "Resolver confidence, 0.0–1.0.",
            },
            "fallback_query": {
                "type": "string",
                "description": "Plain-text YouTube search query to use when artist/track are ambiguous.",
            },
        },
        "required": ["artist", "track", "confidence", "fallback_query"],
    },
}

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a music-request parser for a YouTube reaction streamer.
    The viewer messages are typically live SuperChat donations asking the
    streamer to react to a specific song.  Extract the artist and track
    title.  If the message does not contain a music request, return empty
    strings for artist and track with confidence 0.
""")


@dataclass
class Resolved:
    artist: str
    track: str
    confidence: float
    fallback_query: str


@dataclass
class Candidate:
    video_id: str
    title: str
    channel: str

    def __str__(self) -> str:
        short = self.title[:CANDIDATE_TITLE_MAX_LEN] + "…" if len(self.title) > CANDIDATE_TITLE_MAX_LEN else self.title
        return f"[{self.video_id}] {short}"


@dataclass
class BenchRow:
    message: str
    resolved: Resolved
    candidates: list[Candidate] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


def _get_message_text(msg: dict[str, Any]) -> str:
    content = msg.get("message", "")
    if isinstance(content, dict):
        return content.get("message", "")
    return content or ""


def fetch_superchats(vod_url: str, limit: int | None) -> list[dict[str, Any]]:
    downloader = ChatDownloader()
    messages = downloader.get_messages(vod_url, message_types=["paid_message"])  # type: ignore[attr-defined]
    collected: list[dict[str, Any]] = []
    for msg in messages:
        text = _get_message_text(msg)
        if not text:
            continue
        collected.append(msg)
        if limit and len(collected) >= limit:
            break
    return collected


def resolve_claude(text: str, client: anthropic.Anthropic) -> tuple[Resolved, int, int]:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=256,
        system=SYSTEM_PROMPT,
        tools=[RESOLVE_TOOL],
        tool_choice={"type": "tool", "name": RESOLVE_TOOL_NAME},
        messages=[{"role": "user", "content": text}],
    )
    tool_block = next(b for b in response.content if b.type == "tool_use")
    args: dict[str, Any] = dict(tool_block.input)
    resolved = Resolved(
        artist=str(args.get("artist", "")),
        track=str(args.get("track", "")),
        confidence=float(args.get("confidence", 0.0)),
        fallback_query=str(args.get("fallback_query", "")),
    )
    in_tok = response.usage.input_tokens
    out_tok = response.usage.output_tokens
    return resolved, in_tok, out_tok


def resolve_gpt(text: str) -> tuple[Resolved, int, int]:
    try:
        import openai  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError:
        print("openai package not installed; skipping GPT resolver", file=sys.stderr)
        return Resolved("", "", 0.0, text), 0, 0

    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": RESOLVE_TOOL_NAME,
                    "description": RESOLVE_TOOL.get("description", ""),  # type: ignore[union-attr]
                    "parameters": RESOLVE_TOOL.get("input_schema", {}),  # type: ignore[union-attr]
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": RESOLVE_TOOL_NAME}},
    )
    choice = response.choices[0]
    import json  # noqa: PLC0415
    args = json.loads(choice.message.tool_calls[0].function.arguments)
    resolved = Resolved(
        artist=args.get("artist", ""),
        track=args.get("track", ""),
        confidence=float(args.get("confidence", 0.0)),
        fallback_query=args.get("fallback_query", ""),
    )
    usage = response.usage
    return resolved, usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0


def search_youtube(resolved: Resolved, yt_service: Any, max_results: int = 3) -> list[Candidate]:
    query = f"{resolved.artist} {resolved.track}".strip() or resolved.fallback_query
    if not query:
        return []
    result = (
        yt_service.search()
        .list(part="snippet", q=query, maxResults=max_results, type="video")
        .execute()
    )
    candidates = []
    for item in result.get("items", []):
        snippet = item.get("snippet", {})
        candidates.append(
            Candidate(
                video_id=item["id"]["videoId"],
                title=snippet.get("title", ""),
                channel=snippet.get("channelTitle", ""),
            )
        )
    return candidates


def print_table(rows: list[BenchRow]) -> None:
    col_w = [40, 30, 35, 35, 35]
    header = ["SuperChat text", "Parsed (artist – track)", "Candidate 1", "Candidate 2", "Candidate 3"]
    sep = "|" + "|".join("-" * (w + 2) for w in col_w) + "|"

    def fmt_row(cells: list[str]) -> str:
        padded = [f" {c[:w]:<{w}} " for c, w in zip(cells, col_w, strict=False)]
        return "|" + "|".join(padded) + "|"

    print(fmt_row(header))
    print(sep)
    for row in rows:
        parsed = f"{row.resolved.track} – {row.resolved.artist}" if row.resolved.track else "(no match)"
        candidates = [str(c) for c in row.candidates] + ["", "", ""]
        print(fmt_row([row.message, parsed, candidates[0], candidates[1], candidates[2]]))


def print_cost_summary(rows: list[BenchRow]) -> None:
    total_in = sum(r.input_tokens for r in rows)
    total_out = sum(r.output_tokens for r in rows)
    if rows and rows[0].model.startswith("claude"):
        cost_per_in = 0.25 / 1_000_000
        cost_per_out = 1.25 / 1_000_000
    else:
        cost_per_in = 0.15 / 1_000_000
        cost_per_out = 0.60 / 1_000_000
    total_cost = total_in * cost_per_in + total_out * cost_per_out
    print(f"\nTokens — input: {total_in:,}  output: {total_out:,}  estimated cost: ${total_cost:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vod_url", help="YouTube VOD URL")
    parser.add_argument("--limit", type=int, default=None, help="Max SuperChats to process (default: all)")
    parser.add_argument(
        "--model",
        choices=["claude", "gpt"],
        default="claude",
        help="LLM resolver to use (default: claude)",
    )
    args = parser.parse_args()

    youtube_api_key = os.getenv("YOUTUBE_API_KEY")
    if not youtube_api_key:
        sys.exit("YOUTUBE_API_KEY environment variable is required")

    yt_service = build(YOUTUBE_SERVICE_NAME, YOUTUBE_API_VERSION, developerKey=youtube_api_key)

    anthropic_client: anthropic.Anthropic | None = None
    if args.model == "claude":
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        if not anthropic_api_key:
            sys.exit("ANTHROPIC_API_KEY environment variable is required")
        anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)

    print(f"Fetching SuperChats from {args.vod_url} …", flush=True)
    superchats = fetch_superchats(args.vod_url, args.limit)
    print(f"Found {len(superchats)} SuperChat messages with text.", flush=True)

    if not superchats:
        print("No SuperChat messages found. Exiting.")
        return

    rows: list[BenchRow] = []
    for i, msg in enumerate(superchats, 1):
        text = _get_message_text(msg)
        print(f"  [{i}/{len(superchats)}] resolving: {text[:60]!r}", flush=True)

        if args.model == "claude" and anthropic_client:
            resolved, in_tok, out_tok = resolve_claude(text, anthropic_client)
            model_label = CLAUDE_MODEL
        else:
            resolved, in_tok, out_tok = resolve_gpt(text)
            model_label = GPT_MODEL

        candidates = search_youtube(resolved, yt_service)
        rows.append(
            BenchRow(
                message=text,
                resolved=resolved,
                candidates=candidates,
                model=model_label,
                input_tokens=in_tok,
                output_tokens=out_tok,
            )
        )

    print("\n")
    print_table(rows)
    print_cost_summary(rows)


if __name__ == "__main__":
    main()
