"""Tests for scripts/resolver_bench.py helper functions."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from resolver_bench import (
    CANDIDATE_TITLE_MAX_LEN,
    BenchRow,
    Candidate,
    Resolved,
    _get_message_text,
    print_table,
    search_youtube,
)


class TestGetMessageText:
    def test_string_message(self):
        msg = {"message": "can you react to Numb by Linkin Park"}
        assert _get_message_text(msg) == "can you react to Numb by Linkin Park"

    def test_dict_message(self):
        msg = {"message": {"message": "play Bohemian Rhapsody", "emotes": []}}
        assert _get_message_text(msg) == "play Bohemian Rhapsody"

    def test_missing_message(self):
        assert _get_message_text({}) == ""

    def test_empty_message(self):
        assert _get_message_text({"message": ""}) == ""


class TestSearchYoutube:
    def _make_yt_service(self, items: list[dict]) -> MagicMock:
        mock_svc = MagicMock()
        mock_svc.search().list().execute.return_value = {"items": items}
        return mock_svc

    def test_returns_candidates(self):
        items = [
            {"id": {"videoId": "abc123"}, "snippet": {"title": "Numb - Linkin Park", "channelTitle": "LP"}},
            {"id": {"videoId": "def456"}, "snippet": {"title": "Numb (Cover)", "channelTitle": "CoverCh"}},
        ]
        resolved = Resolved(artist="Linkin Park", track="Numb", confidence=0.9, fallback_query="")
        candidates = search_youtube(resolved, self._make_yt_service(items))
        assert len(candidates) == len(items)
        assert candidates[0].video_id == "abc123"
        assert candidates[0].title == "Numb - Linkin Park"

    def test_uses_fallback_query_when_no_artist_or_track(self):
        items = [{"id": {"videoId": "xyz"}, "snippet": {"title": "Song X", "channelTitle": "Ch"}}]
        resolved = Resolved(artist="", track="", confidence=0.2, fallback_query="numb linkin park reaction")
        mock_svc = self._make_yt_service(items)
        search_youtube(resolved, mock_svc)
        mock_svc.search().list.assert_called_with(
            part="snippet", q="numb linkin park reaction", maxResults=3, type="video"
        )

    def test_empty_query_returns_no_candidates(self):
        resolved = Resolved(artist="", track="", confidence=0.0, fallback_query="")
        candidates = search_youtube(resolved, MagicMock())
        assert candidates == []

    def test_missing_items_returns_empty(self):
        mock_svc = MagicMock()
        mock_svc.search().list().execute.return_value = {}
        resolved = Resolved(artist="Adele", track="Hello", confidence=0.8, fallback_query="")
        candidates = search_youtube(resolved, mock_svc)
        assert candidates == []


class TestPrintTable:
    def test_prints_without_error(self, capsys: pytest.CaptureFixture[str]):
        rows = [
            BenchRow(
                message="can you react to Numb",
                resolved=Resolved(artist="Linkin Park", track="Numb", confidence=0.9, fallback_query=""),
                candidates=[Candidate("abc123", "Numb - Linkin Park", "LP")],
                model="claude-haiku-4-5-20251001",
            )
        ]
        print_table(rows)
        out = capsys.readouterr().out
        assert "Numb" in out
        assert "Linkin Park" in out

    def test_no_match_row(self, capsys: pytest.CaptureFixture[str]):
        rows = [
            BenchRow(
                message="hello streamer!",
                resolved=Resolved(artist="", track="", confidence=0.0, fallback_query=""),
                candidates=[],
                model="claude-haiku-4-5-20251001",
            )
        ]
        print_table(rows)
        out = capsys.readouterr().out
        assert "(no match)" in out


class TestCandidate:
    def test_str_truncates_long_title(self):
        c = Candidate("abc", "A" * 40, "Channel")
        result = str(c)
        assert "…" in result
        assert len(result) < CANDIDATE_TITLE_MAX_LEN + 20  # id prefix + ellipsis overhead

    def test_str_short_title(self):
        c = Candidate("abc", "Short title", "Channel")
        assert str(c) == "[abc] Short title"
