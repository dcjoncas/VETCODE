import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import main


class ChannelParticipantTests(unittest.TestCase):
    @patch("main._channel_people", return_value=[])
    def test_manual_email_does_not_require_a_talent_profile(self, _people):
        participants = main._channel_participants_from_json(
            json.dumps([{"email": "outside.counsel@example.com"}]),
            "law",
        )

        self.assertEqual(participants[0]["email"], "outside.counsel@example.com")
        self.assertEqual(participants[0]["role"], "member")
        self.assertEqual(participants[0]["source"], "manual")
        self.assertFalse(participants[0]["profile_id"])

        with self.assertRaises(HTTPException) as invalid:
            main._channel_participants_from_json(json.dumps([{"email": "not-an-email"}]), "law")
        self.assertEqual(invalid.exception.status_code, 400)

    @patch("main._write_json_store")
    @patch("main._read_channel_conversations")
    @patch("main._channel_people", return_value=[])
    def test_new_conversation_includes_recipient_and_creator(self, _people, read, write):
        store = {"law": []}
        conversations = []
        read.return_value = (store, conversations)

        result = main.create_channel_conversation(
            domain="law",
            title="Outside counsel update",
            topic="",
            participants_json=json.dumps([{"email": "outside@example.com"}]),
            created_by_name="Darrin",
            created_by_email="owner@example.com",
        )

        emails = [row["email"] for row in result["conversation"]["participants"]]
        self.assertEqual(emails[0], "egeria@devready.ai")
        self.assertIn("outside@example.com", emails)
        self.assertIn("owner@example.com", emails)
        write.assert_called_once()

    @patch("main._write_json_store")
    @patch("main._read_channel_conversations")
    def test_participant_can_be_removed_but_egeria_is_protected(self, read, write):
        conversation = {
            "id": "outside-counsel",
            "participants": [
                main._egeria_participant(),
                {"name": "Outside", "email": "outside@example.com"},
            ],
        }
        read.return_value = ({"law": [conversation]}, [conversation])

        result = main.remove_channel_conversation_participant(
            "outside-counsel",
            domain="law",
            email="outside@example.com",
        )

        self.assertEqual(result["removed_email"], "outside@example.com")
        self.assertEqual(
            [row["email"] for row in result["conversation"]["participants"]],
            ["egeria@devready.ai"],
        )
        write.assert_called_once()

        with self.assertRaises(HTTPException) as protected:
            main.remove_channel_conversation_participant(
                "outside-counsel",
                domain="law",
                email="egeria@devready.ai",
            )
        self.assertEqual(protected.exception.status_code, 400)


class ChannelGuestAccessTests(unittest.TestCase):
    @patch("main._write_json_store")
    @patch("main._seed_access_users")
    @patch("main._read_channel_conversations")
    def test_invited_email_registers_as_channel_guest(self, read, users, write):
        invited = {
            "id": "outside-counsel",
            "participants": [
                main._egeria_participant(),
                {"name": "Outside", "email": "outside@example.com"},
            ],
        }
        read.return_value = ({"law": [invited]}, [invited])
        users.return_value = {}

        result = main.access_register(
            username="outside@example.com",
            display_name="Outside Counsel",
            email="outside@example.com",
            password="StrongPass123!",
            confirm_password="StrongPass123!",
            password_confirm="StrongPass123!",
            login_type="channel",
            domain="law",
            conversation_id="outside-counsel",
        )

        self.assertEqual(result["user"]["role"], "channel_guest")
        self.assertEqual(result["user"]["allowed_menu"], ["channels"])
        self.assertFalse(result["user"]["candidate_profile_id"])
        write.assert_called_once()

    @patch("main._seed_access_users", return_value={})
    @patch("main._read_channel_conversations", return_value=({"law": []}, []))
    def test_uninvited_email_cannot_register_for_conversation(self, _read, _users):
        with self.assertRaises(HTTPException) as denied:
            main.access_register(
                username="outside@example.com",
                display_name="Outside Counsel",
                email="outside@example.com",
                password="StrongPass123!",
                confirm_password="StrongPass123!",
                password_confirm="StrongPass123!",
                login_type="channel",
                domain="law",
                conversation_id="outside-counsel",
            )
        self.assertEqual(denied.exception.status_code, 403)


class ChannelArchiveTests(unittest.TestCase):
    @patch("main._write_json_store")
    @patch("main._read_channel_conversations")
    def test_archive_and_restore_preserve_conversation_record(self, read, write):
        conversation = {
            "id": "outside-counsel",
            "title": "Outside counsel",
            "last_message": "Prior message remains available.",
            "participants": [main._egeria_participant()],
        }
        read.return_value = ({"law": [conversation]}, [conversation])

        archived = main.archive_channel_conversation(
            "outside-counsel",
            domain="law",
            archived=True,
            archived_by_name="Darrin",
            archived_by_email="owner@example.com",
        )

        self.assertTrue(archived["archived"])
        self.assertTrue(archived["messages_preserved"])
        self.assertEqual(archived["conversation"]["last_message"], "Prior message remains available.")
        self.assertEqual(archived["conversation"]["archive_history"][0]["action"], "archived")

        restored = main.archive_channel_conversation(
            "outside-counsel",
            domain="law",
            archived=False,
            archived_by_name="Darrin",
            archived_by_email="owner@example.com",
        )

        self.assertFalse(restored["archived"])
        self.assertNotIn("archived_at", restored["conversation"])
        self.assertEqual(
            [row["action"] for row in restored["conversation"]["archive_history"]],
            ["archived", "restored"],
        )
        self.assertEqual(write.call_count, 2)

    @patch("main._write_json_store")
    @patch("main._read_json_store")
    @patch("main._read_channel_conversations")
    def test_archived_conversation_rejects_new_messages(self, read_conversations, read_store, write):
        conversation = {
            "id": "outside-counsel",
            "archived_at": "2026-07-15T18:00:00Z",
            "participants": [main._egeria_participant()],
        }
        read_conversations.return_value = ({"law": [conversation]}, [conversation])

        with self.assertRaises(HTTPException) as blocked:
            main.post_channel_message(
                channel="outside-counsel",
                conversation_id="outside-counsel",
                domain="law",
                message="This must not be added.",
                author_name="Darrin",
                author_email="owner@example.com",
                audience="conversation",
            )

        self.assertEqual(blocked.exception.status_code, 409)
        read_store.assert_not_called()
        write.assert_not_called()

    @patch("main._read_channel_conversations")
    def test_archived_conversation_rejects_participant_changes(self, read):
        conversation = {
            "id": "outside-counsel",
            "archived_at": "2026-07-15T18:00:00Z",
            "participants": [main._egeria_participant()],
        }
        read.return_value = ({"law": [conversation]}, [conversation])

        with self.assertRaises(HTTPException) as blocked:
            main.add_channel_conversation_participants(
                "outside-counsel",
                domain="law",
                participants_json=json.dumps([{"email": "new@example.com"}]),
            )
        self.assertEqual(blocked.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
