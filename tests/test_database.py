"""Tests for database module."""

from datetime import UTC, datetime, timedelta
from unittest import TestCase

from jawed.database import (
    complete_snapshot,
    create_api_user,
    create_stream_snapshot,
    end_stream,
    get_all_active_channels,
    get_api_user_by_id,
    get_api_user_by_username,
    get_channel_config,
    get_channel_from_master,
    get_channel_requests,
    get_event,
    get_events_for_stream,
    get_request,
    init_channel_db,
    init_master_db,
    insert_event,
    insert_snapshot,
    is_channel_accepting_requests,
    is_token_expired,
    register_channel_in_master,
    save_channel_config,
    save_request,
    token_near_expiry,
    update_request_status,
    upsert_stream_state,
)
from tests.utils import temp_data_dir

TEST_PASSWORD_HASH = "hash123"  # noqa: S105


class TestMasterDatabase(TestCase):
    def test_init_master_db(self):
        with temp_data_dir() as data_dir:
            init_master_db()
            master_db = data_dir / "master.db"
            self.assertTrue(master_db.exists())

    def test_register_channel_in_master(self):
        with temp_data_dir():
            init_master_db()
            channel = register_channel_in_master("UC123", "Test Channel")

            self.assertEqual(channel["channel_id"], "UC123")
            self.assertEqual(channel["channel_name"], "Test Channel")
            self.assertTrue(channel["is_active"])

    def test_get_channel_from_master(self):
        with temp_data_dir():
            init_master_db()
            register_channel_in_master("UC123", "Test Channel")

            channel = get_channel_from_master("UC123")
            self.assertIsNotNone(channel)
            self.assertEqual(channel["channel_id"], "UC123")

    def test_get_channel_from_master_not_found(self):
        with temp_data_dir():
            init_master_db()
            channel = get_channel_from_master("nonexistent")
            self.assertIsNone(channel)

    def test_get_all_active_channels(self):
        with temp_data_dir():
            init_master_db()
            register_channel_in_master("UC123", "Channel 1")
            register_channel_in_master("UC456", "Channel 2")

            channels = get_all_active_channels()
            self.assertEqual(len(channels), 2)


class TestChannelDatabase(TestCase):
    def test_init_channel_db(self):
        with temp_data_dir() as data_dir:
            init_channel_db("UC123")
            channel_db = data_dir / "channel_UC123.db"
            self.assertTrue(channel_db.exists())

    def test_save_channel_config(self):
        with temp_data_dir():
            init_channel_db("UC123")

            config = save_channel_config(
                channel_id="UC123",
                channel_name="Test Channel",
                live_chat_id="LC123",
            )

            self.assertEqual(config["channel_id"], "UC123")
            self.assertEqual(config["channel_name"], "Test Channel")
            self.assertEqual(config["live_chat_id"], "LC123")

    def test_get_channel_config(self):
        with temp_data_dir():
            init_channel_db("UC123")
            save_channel_config(
                channel_id="UC123",
                channel_name="Test Channel",
            )

            config = get_channel_config("UC123")
            self.assertIsNotNone(config)
            self.assertEqual(config["channel_name"], "Test Channel")

    def test_get_channel_config_not_found(self):
        with temp_data_dir():
            config = get_channel_config("nonexistent")
            self.assertIsNone(config)


class TestRequestAcceptance(TestCase):
    def test_is_channel_accepting_requests_true(self):
        with temp_data_dir():
            init_channel_db("UC123")

            # Set start time to past, no end time
            past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            save_channel_config(
                channel_id="UC123",
                channel_name="Test Channel",
                accepting_requests_start_datetime=past,
                accepting_requests_end_datetime=None,
            )

            self.assertTrue(is_channel_accepting_requests("UC123"))

    def test_is_channel_accepting_requests_false_future_start(self):
        with temp_data_dir():
            init_channel_db("UC123")

            # Set start time to future
            future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            save_channel_config(
                channel_id="UC123",
                channel_name="Test Channel",
                accepting_requests_start_datetime=future,
            )

            self.assertFalse(is_channel_accepting_requests("UC123"))

    def test_is_channel_accepting_requests_false_has_end(self):
        with temp_data_dir():
            init_channel_db("UC123")

            past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            end = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
            save_channel_config(
                channel_id="UC123",
                channel_name="Test Channel",
                accepting_requests_start_datetime=past,
                accepting_requests_end_datetime=end,
            )

            self.assertFalse(is_channel_accepting_requests("UC123"))

    def test_is_channel_accepting_requests_false_no_config(self):
        with temp_data_dir():
            self.assertFalse(is_channel_accepting_requests("nonexistent"))


class TestRequests(TestCase):
    def test_save_request(self):
        with temp_data_dir():
            init_channel_db("UC123")

            req = save_request(
                channel_id="UC123",
                request_id="req-123",
                requesting_username="testuser",
                youtube_link="https://youtube.com/watch?v=test123",
                youtube_link_title="Test Video",
                user_message="Please play this!",
            )

            self.assertEqual(req["request_id"], "req-123")
            self.assertEqual(req["requesting_username"], "testuser")
            self.assertEqual(req["status"], "pending")

    def test_get_request(self):
        with temp_data_dir():
            init_channel_db("UC123")
            save_request(
                channel_id="UC123",
                request_id="req-123",
                requesting_username="testuser",
                youtube_link="https://youtube.com/watch?v=test123",
            )

            req = get_request("UC123", "req-123")
            self.assertIsNotNone(req)
            self.assertEqual(req["requesting_username"], "testuser")

    def test_get_request_not_found(self):
        with temp_data_dir():
            init_channel_db("UC123")
            req = get_request("UC123", "nonexistent")
            self.assertIsNone(req)

    def test_update_request_status(self):
        with temp_data_dir():
            init_channel_db("UC123")
            save_request(
                channel_id="UC123",
                request_id="req-123",
                requesting_username="testuser",
                youtube_link="https://youtube.com/watch?v=test123",
            )

            updated = update_request_status("UC123", "req-123", "posted", "chat-msg-456")
            self.assertEqual(updated["status"], "posted")
            self.assertEqual(updated["chat_message_id"], "chat-msg-456")

    def test_get_channel_requests(self):
        with temp_data_dir():
            init_channel_db("UC123")
            save_request(
                channel_id="UC123",
                request_id="req-1",
                requesting_username="user1",
                youtube_link="https://youtube.com/watch?v=test1",
            )
            save_request(
                channel_id="UC123",
                request_id="req-2",
                requesting_username="user2",
                youtube_link="https://youtube.com/watch?v=test2",
            )

            requests = get_channel_requests("UC123")
            self.assertEqual(len(requests), 2)

    def test_get_channel_requests_with_status_filter(self):
        with temp_data_dir():
            init_channel_db("UC123")
            save_request(
                channel_id="UC123",
                request_id="req-1",
                requesting_username="user1",
                youtube_link="https://youtube.com/watch?v=test1",
            )
            save_request(
                channel_id="UC123",
                request_id="req-2",
                requesting_username="user2",
                youtube_link="https://youtube.com/watch?v=test2",
            )
            update_request_status("UC123", "req-1", "posted")

            pending = get_channel_requests("UC123", status="pending")
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["request_id"], "req-2")


class TestApiUsers(TestCase):
    def test_create_api_user(self):
        with temp_data_dir():
            init_master_db()

            user = create_api_user(
                user_id="user-123",
                username="testuser",
                password_hash=TEST_PASSWORD_HASH,
                is_admin=True,
            )

            self.assertEqual(user["user_id"], "user-123")
            self.assertEqual(user["username"], "testuser")
            self.assertTrue(user["is_admin"])

    def test_get_api_user_by_username(self):
        with temp_data_dir():
            init_master_db()
            create_api_user("user-123", "testuser", TEST_PASSWORD_HASH)

            user = get_api_user_by_username("testuser")
            self.assertIsNotNone(user)
            self.assertEqual(user["user_id"], "user-123")

    def test_get_api_user_by_id(self):
        with temp_data_dir():
            init_master_db()
            create_api_user("user-123", "testuser", TEST_PASSWORD_HASH)

            user = get_api_user_by_id("user-123")
            self.assertIsNotNone(user)
            self.assertEqual(user["username"], "testuser")


class TestTokenUtilities(TestCase):
    def test_is_token_expired_no_config(self):
        with temp_data_dir():
            self.assertTrue(is_token_expired("nonexistent"))

    def test_is_token_expired_no_token_expiry(self):
        with temp_data_dir():
            init_channel_db("UC123")
            save_channel_config(channel_id="UC123", channel_name="Test Channel")
            self.assertTrue(is_token_expired("UC123"))

    def test_is_token_expired_true(self):
        with temp_data_dir():
            init_channel_db("UC123")
            past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            save_channel_config(channel_id="UC123", channel_name="Test Channel", token_expiry=past)
            self.assertTrue(is_token_expired("UC123"))

    def test_is_token_expired_false(self):
        with temp_data_dir():
            init_channel_db("UC123")
            future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            save_channel_config(channel_id="UC123", channel_name="Test Channel", token_expiry=future)
            self.assertFalse(is_token_expired("UC123"))

    def test_token_near_expiry_true(self):
        with temp_data_dir():
            init_channel_db("UC123")
            soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
            save_channel_config(channel_id="UC123", channel_name="Test Channel", token_expiry=soon)
            self.assertTrue(token_near_expiry("UC123", days=7))

    def test_token_near_expiry_false(self):
        with temp_data_dir():
            init_channel_db("UC123")
            far = (datetime.now(UTC) + timedelta(days=30)).isoformat()
            save_channel_config(channel_id="UC123", channel_name="Test Channel", token_expiry=far)
            self.assertFalse(token_near_expiry("UC123", days=7))


class TestEvents(TestCase):
    def _insert_test_event(self, message_id: str = "msg-1") -> bool:
        return insert_event(
            channel_id="UC123",
            live_chat_id="LC123",
            message_id=message_id,
            user_sub="sub-abc",
            stream_id="stream-xyz",
            timestamp=datetime.now(UTC).isoformat(),
            status="pending",
            superchat_text="Play Bohemian Rhapsody",
        )

    def test_insert_event(self):
        with temp_data_dir():
            init_channel_db("UC123")
            inserted = self._insert_test_event()
            self.assertTrue(inserted)

    def test_insert_event_duplicate_ignored(self):
        with temp_data_dir():
            init_channel_db("UC123")
            self._insert_test_event()
            inserted_again = self._insert_test_event()
            self.assertFalse(inserted_again)

    def test_get_event(self):
        with temp_data_dir():
            init_channel_db("UC123")
            self._insert_test_event()
            event = get_event("UC123", "LC123", "msg-1")
            self.assertIsNotNone(event)
            self.assertEqual(event["superchat_text"], "Play Bohemian Rhapsody")

    def test_get_events_for_stream(self):
        with temp_data_dir():
            init_channel_db("UC123")
            self._insert_test_event("msg-1")
            self._insert_test_event("msg-2")
            events = get_events_for_stream("UC123", "LC123")
            self.assertEqual(len(events), 2)


class TestStreamState(TestCase):
    def _create_state(self) -> dict:
        return upsert_stream_state(
            channel_id="UC123",
            live_chat_id="LC123",
            user_sub="sub-abc",
            stream_id="stream-xyz",
            started_at=datetime.now(UTC).isoformat(),
        )

    def test_upsert_stream_state_create(self):
        with temp_data_dir():
            init_channel_db("UC123")
            state = self._create_state()
            self.assertIsNotNone(state)
            self.assertEqual(state["live_chat_id"], "LC123")
            self.assertIsNone(state["next_page_token"])

    def test_upsert_stream_state_update_next_page_token(self):
        with temp_data_dir():
            init_channel_db("UC123")
            self._create_state()
            updated = upsert_stream_state(
                channel_id="UC123",
                live_chat_id="LC123",
                user_sub="sub-abc",
                stream_id="stream-xyz",
                started_at=datetime.now(UTC).isoformat(),
                next_page_token="token-page-2",  # noqa: S106
            )
            self.assertEqual(updated["next_page_token"], "token-page-2")

    def test_end_stream(self):
        with temp_data_dir():
            init_channel_db("UC123")
            self._create_state()
            state = end_stream("UC123", "LC123")
            self.assertIsNotNone(state["ended_at"])


class TestSnapshots(TestCase):
    def test_insert_snapshot(self):
        with temp_data_dir():
            init_channel_db("UC123")
            snap = insert_snapshot("UC123", "sub-abc", "stream-xyz", "2026-01-01T00:00:00+00:00")
            self.assertIsNone(snap["completed_at"])

    def test_complete_snapshot(self):
        with temp_data_dir():
            init_channel_db("UC123")
            started_at = "2026-01-01T00:00:00+00:00"
            insert_snapshot("UC123", "sub-abc", "stream-xyz", started_at)
            snap = complete_snapshot("UC123", "sub-abc", "stream-xyz", started_at)
            self.assertIsNotNone(snap["completed_at"])

    def test_create_stream_snapshot(self):
        with temp_data_dir():
            init_channel_db("UC123")
            started_at = datetime.now(UTC).isoformat()
            upsert_stream_state(
                channel_id="UC123",
                live_chat_id="LC123",
                user_sub="sub-abc",
                stream_id="stream-xyz",
                started_at=started_at,
            )
            insert_snapshot("UC123", "sub-abc", "stream-xyz", started_at)
            insert_event(
                channel_id="UC123",
                live_chat_id="LC123",
                message_id="msg-1",
                user_sub="sub-abc",
                stream_id="stream-xyz",
                timestamp=started_at,
                status="resolved",
                superchat_text="Play Bohemian Rhapsody",
            )
            end_stream("UC123", "LC123")
            path = create_stream_snapshot("UC123", "LC123")
            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            lines = path.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("Bohemian Rhapsody", lines[0])
