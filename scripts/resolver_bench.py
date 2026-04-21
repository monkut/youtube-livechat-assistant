"""Phase 0: Hybrid resolver accuracy benchmark.

Pulls SuperChat messages from a YouTube VOD via yt-dlp's live_chat subtitle,
runs a hybrid resolver (regex ID extractor first, LLM+search fallback second),
and prints two markdown tables for hand-scoring:

    - Table A: ID-bearing rows (regex path) — scored against Gate A (>=95%).
    - Table B: NL-only rows (LLM path) — scored against Gate B (>=80% top-3).

Usage:
    uv run scripts/resolver_bench.py <VOD_URL> [--limit N] [--model MODEL]

Environment:
    ANTHROPIC_API_KEY — required for claude-haiku-4-5 (default model)
    YOUTUBE_API_KEY   — required for YouTube videos.list / search.list
    OPENAI_API_KEY    — required for gpt-4o-mini model
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

import anthropic
from anthropic.types import ToolParam
from googleapiclient.discovery import build

from jawed.definitions import YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION

try:
    import openai  # type: ignore[import-untyped]
except ImportError:
    openai = None  # type: ignore[assignment]

ModelChoice = Literal["claude", "gpt"]

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
GPT_MODEL = "gpt-4o-mini"
CANDIDATE_TITLE_MAX_LEN = 30
VIDEOS_LIST_BATCH_SIZE = 50
SEARCH_LIST_QUOTA_UNITS = 100

# Input/output USD cost per token, keyed by model ID.
PRICING: dict[str, tuple[float, float]] = {
    CLAUDE_MODEL: (0.25 / 1_000_000, 1.25 / 1_000_000),
    GPT_MODEL: (0.15 / 1_000_000, 0.60 / 1_000_000),
}

URL_ID_PATTERN = re.compile(
    r"(?:v=|youtu\.be/|youtube\.com/watch\?v=|youtube\.com/shorts/|youtube\.com/embed/)"
    r"([A-Za-z0-9_-]{11})"
)
STANDALONE_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")

# Restrict VOD URLs to known YouTube hosts before passing to a yt-dlp subprocess.
VOD_URL_PATTERN = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?|live/|shorts/)|youtu\.be/)[A-Za-z0-9_\-?&=/.]+$"
)

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
                "description": "Resolver confidence, 0.0-1.0.",
            },
            "fallback_query": {
                "type": "string",
                "description": "Plain-text YouTube search query to use when artist/track are ambiguous.",
            },
        },
        "required": ["artist", "track", "confidence", "fallback_query"],
    },
}

OPENAI_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": RESOLVE_TOOL_NAME,
        "description": RESOLVE_TOOL["description"],
        "parameters": RESOLVE_TOOL["input_schema"],
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
class SuperChat:
    text: str
    author: str
    amount: str
    timestamp_usec: str
    video_offset_ms: str


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
        if len(self.title) <= CANDIDATE_TITLE_MAX_LEN:
            return f"[{self.video_id}] {self.title}"
        return f"[{self.video_id}] {self.title[:CANDIDATE_TITLE_MAX_LEN]}..."


@dataclass
class BenchRow:
    superchat: SuperChat
    resolver_path: Literal["regex", "llm"]
    extracted_id: str | None = None
    resolved: Resolved | None = None
    candidates: list[Candidate] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


def _resolved_from_args(args: dict[str, Any]) -> Resolved:
    return Resolved(
        artist=str(args.get("artist", "")),
        track=str(args.get("track", "")),
        confidence=float(args.get("confidence", 0.0)),
        fallback_query=str(args.get("fallback_query", "")),
    )


def run_yt_dlp(vod_url: str, out_dir: Path) -> Path:
    if not VOD_URL_PATTERN.match(vod_url):
        raise ValueError(f"vod_url must be a youtube.com / youtu.be URL: {vod_url!r}")
    output_template = str(out_dir / "chat.%(ext)s")
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--sub-langs",
        "live_chat",
        "--sub-format",
        "json",
        "-o",
        output_template,
        vod_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise RuntimeError(f"yt-dlp exited {result.returncode}")
    matches = list(out_dir.glob("*.live_chat.json"))
    if not matches:
        raise RuntimeError("yt-dlp produced no live_chat.json file")
    return matches[0]


def _extract_superchat(entry: dict[str, Any]) -> SuperChat | None:
    replay = entry.get("replayChatItemAction")
    if not replay:
        return None
    offset_ms = str(replay.get("videoOffsetTimeMsec", ""))
    for action_item in replay.get("actions", []):
        renderer = (
            action_item.get("addChatItemAction", {}).get("item", {}).get("liveChatPaidMessageRenderer")
        )
        if not renderer:
            continue
        runs = renderer.get("message", {}).get("runs") or []
        text = "".join(run.get("text", "") for run in runs).strip()
        if not text:
            continue
        return SuperChat(
            text=text,
            author=renderer.get("authorName", {}).get("simpleText", ""),
            amount=renderer.get("purchaseAmountText", {}).get("simpleText", ""),
            timestamp_usec=str(renderer.get("timestampUsec", "")),
            video_offset_ms=offset_ms,
        )
    return None


def fetch_superchats(vod_url: str, limit: int | None) -> list[SuperChat]:
    with tempfile.TemporaryDirectory() as tmpdir:
        sub_file = run_yt_dlp(vod_url, Path(tmpdir))
        collected: list[SuperChat] = []
        with sub_file.open() as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sc = _extract_superchat(entry)
                if sc is None:
                    continue
                collected.append(sc)
                if limit and len(collected) >= limit:
                    break
        return collected


def extract_video_id(text: str) -> str | None:
    url_match = URL_ID_PATTERN.search(text)
    if url_match:
        return url_match.group(1)
    # Require >=1 digit/underscore/dash: filters out English words or channel
    # names that happen to be 11 alpha chars (e.g. "Scenarioart"). Misses ~11% of
    # pure-alpha IDs, which fall through to the LLM path — correct trade.
    for match in STANDALONE_ID_PATTERN.finditer(text):
        token = match.group(1)
        if any(c.isdigit() or c in "_-" for c in token):
            return token
    return None


def batch_hydrate_videos(video_ids: list[str], yt_service: Any) -> dict[str, Candidate]:
    """Fetch multiple videos in one `videos.list` call per 50-id chunk (1 quota unit per chunk)."""
    out: dict[str, Candidate] = {}
    unique_ids = list(dict.fromkeys(video_ids))
    for i in range(0, len(unique_ids), VIDEOS_LIST_BATCH_SIZE):
        chunk = unique_ids[i : i + VIDEOS_LIST_BATCH_SIZE]
        result = yt_service.videos().list(part="snippet", id=",".join(chunk)).execute()
        for item in result.get("items", []):
            video_id = item.get("id", "")
            snippet = item.get("snippet", {})
            out[video_id] = Candidate(
                video_id=video_id,
                title=snippet.get("title", ""),
                channel=snippet.get("channelTitle", ""),
            )
    return out


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
    resolved = _resolved_from_args(dict(tool_block.input))
    return resolved, response.usage.input_tokens, response.usage.output_tokens


def resolve_gpt(text: str, client: Any) -> tuple[Resolved, int, int]:
    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        tools=[OPENAI_TOOL],
        tool_choice={"type": "function", "function": {"name": RESOLVE_TOOL_NAME}},
    )
    choice = response.choices[0]
    resolved = _resolved_from_args(json.loads(choice.message.tool_calls[0].function.arguments))
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


def _format_row(cells: list[str], widths: list[int]) -> str:
    padded = [f" {c[:w]:<{w}} " for c, w in zip(cells, widths, strict=False)]
    return "|" + "|".join(padded) + "|"


def _separator(widths: list[int]) -> str:
    return "|" + "|".join("-" * (w + 2) for w in widths) + "|"


def print_markdown_table(
    title: str,
    headers: list[str],
    widths: list[int],
    rows: Iterable[list[str]],
) -> None:
    print(f"\n### {title}\n")
    print(_format_row(headers, widths))
    print(_separator(widths))
    for cells in rows:
        print(_format_row(cells, widths))


def print_regex_table(rows: list[BenchRow]) -> None:
    widths = [40, 13, 40, 25]
    headers = ["SuperChat text", "Extracted ID", "Hydrated title", "Channel"]

    def cells(row: BenchRow) -> list[str]:
        cand = row.candidates[0] if row.candidates else None
        return [
            row.superchat.text,
            row.extracted_id or "",
            cand.title if cand else "(videos.list miss)",
            cand.channel if cand else "",
        ]

    print_markdown_table(
        "Table A -- ID-bearing rows (regex path, Gate A >=95%)",
        headers,
        widths,
        (cells(r) for r in rows),
    )


def print_llm_table(rows: list[BenchRow]) -> None:
    widths = [40, 30, 32, 32, 32]
    headers = ["SuperChat text", "Parsed (artist - track)", "Candidate 1", "Candidate 2", "Candidate 3"]

    def cells(row: BenchRow) -> list[str]:
        r = row.resolved
        parsed = f"{r.artist} - {r.track}" if r and r.track else "(no match)"
        cands = [str(c) for c in row.candidates] + ["", "", ""]
        return [row.superchat.text, parsed, cands[0], cands[1], cands[2]]

    print_markdown_table(
        "Table B -- NL-only rows (LLM path, Gate B >=80% top-3)",
        headers,
        widths,
        (cells(r) for r in rows),
    )


def print_summary(regex_rows: list[BenchRow], llm_rows: list[BenchRow]) -> None:
    total = len(regex_rows) + len(llm_rows)
    pct_regex = (len(regex_rows) / total * 100) if total else 0.0
    pct_llm = (len(llm_rows) / total * 100) if total else 0.0

    total_in = sum(r.input_tokens for r in llm_rows)
    total_out = sum(r.output_tokens for r in llm_rows)
    model = llm_rows[0].model if llm_rows else ""
    rate_in, rate_out = PRICING.get(model, (0.0, 0.0))
    total_cost = total_in * rate_in + total_out * rate_out

    regex_miss = sum(1 for r in regex_rows if not r.candidates)
    videos_list_calls = -(-len(regex_rows) // VIDEOS_LIST_BATCH_SIZE)
    quota_estimate = videos_list_calls + len(llm_rows) * SEARCH_LIST_QUOTA_UNITS

    print("\n### Summary\n")
    print(f"- Total SuperChats: {total}")
    print(f"- Regex path (ID-bearing): {len(regex_rows)} ({pct_regex:.1f}%)")
    print(f"- LLM path (NL-only):      {len(llm_rows)} ({pct_llm:.1f}%)")
    print(f"- videos.list misses on extracted IDs: {regex_miss}")
    print(f"- LLM tokens -- input: {total_in:,}  output: {total_out:,}  estimated cost: ${total_cost:.4f}")
    print(f"- YouTube Data API quota used (batched videos.list + search.list): {quota_estimate} units")


def resolve_llm_row(
    sc: SuperChat,
    model_choice: ModelChoice,
    anthropic_client: anthropic.Anthropic | None,
    openai_client: Any,
    yt_service: Any,
) -> BenchRow:
    if model_choice == "claude":
        if anthropic_client is None:
            raise RuntimeError("anthropic_client required for claude model")
        resolved, in_tok, out_tok = resolve_claude(sc.text, anthropic_client)
        model_label = CLAUDE_MODEL
    else:
        if openai_client is None:
            raise RuntimeError("openai_client required for gpt model")
        resolved, in_tok, out_tok = resolve_gpt(sc.text, openai_client)
        model_label = GPT_MODEL
    candidates = search_youtube(resolved, yt_service)
    return BenchRow(
        superchat=sc,
        resolver_path="llm",
        resolved=resolved,
        candidates=candidates,
        model=model_label,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


def build_resolver_clients(
    model_choice: ModelChoice,
) -> tuple[anthropic.Anthropic | None, Any]:
    anthropic_client: anthropic.Anthropic | None = None
    openai_client: Any = None
    if model_choice == "claude":
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            sys.exit("ANTHROPIC_API_KEY environment variable is required")
        anthropic_client = anthropic.Anthropic(api_key=key)
    else:
        if openai is None:
            sys.exit("openai package is not installed; run `uv add openai` or choose --model claude")
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            sys.exit("OPENAI_API_KEY environment variable is required")
        openai_client = openai.OpenAI(api_key=key)
    return anthropic_client, openai_client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vod_url", help="YouTube VOD URL")
    parser.add_argument("--limit", type=int, default=None, help="Max SuperChats to process (default: all)")
    parser.add_argument(
        "--model",
        choices=["claude", "gpt"],
        default="claude",
        help="LLM resolver to use for NL-only rows (default: claude)",
    )
    args = parser.parse_args()
    model_choice: ModelChoice = args.model

    youtube_api_key = os.getenv("YOUTUBE_API_KEY")
    if not youtube_api_key:
        sys.exit("YOUTUBE_API_KEY environment variable is required")

    yt_service = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, developerKey=youtube_api_key)
    anthropic_client, openai_client = build_resolver_clients(model_choice)

    print(f"Fetching SuperChats from {args.vod_url} via yt-dlp ...", flush=True)
    superchats = fetch_superchats(args.vod_url, args.limit)
    print(f"Found {len(superchats)} SuperChat messages with text.\n", flush=True)

    if not superchats:
        print("No SuperChat messages found. Exiting.")
        return

    regex_hits: list[tuple[SuperChat, str]] = []
    nl_only: list[SuperChat] = []
    for sc in superchats:
        video_id = extract_video_id(sc.text)
        if video_id:
            regex_hits.append((sc, video_id))
        else:
            nl_only.append(sc)

    print(f"Classified: {len(regex_hits)} ID-bearing, {len(nl_only)} NL-only.", flush=True)

    id_to_candidate: dict[str, Candidate] = {}
    if regex_hits:
        print("Batch-hydrating extracted IDs via videos.list ...", flush=True)
        id_to_candidate = batch_hydrate_videos([vid for _, vid in regex_hits], yt_service)

    regex_rows = [
        BenchRow(
            superchat=sc,
            resolver_path="regex",
            extracted_id=vid,
            candidates=[id_to_candidate[vid]] if vid in id_to_candidate else [],
        )
        for sc, vid in regex_hits
    ]

    llm_rows: list[BenchRow] = []
    for i, sc in enumerate(nl_only, 1):
        print(f"  LLM [{i}/{len(nl_only)}]: {sc.text[:60]!r}", flush=True)
        llm_rows.append(resolve_llm_row(sc, model_choice, anthropic_client, openai_client, yt_service))

    print_regex_table(regex_rows)
    print_llm_table(llm_rows)
    print_summary(regex_rows, llm_rows)


if __name__ == "__main__":
    main()
