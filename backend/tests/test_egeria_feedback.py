import unittest
from unittest.mock import patch

from fastapi import HTTPException

import main


def feedback_event(event_id="EGR-1", acknowledged_at=""):
    return {
        "id": event_id,
        "domain": "law",
        "event_type": "candidate_role_feedback_submitted",
        "created_at": "2026-07-15T20:00:00Z",
        "acknowledged_at": acknowledged_at,
        "context": {
            "workflowId": "ROLE-1",
            "candidateId": "2367",
            "candidateName": "Gregory Brown",
            "candidateEmail": "gregory@example.com",
            "jobId": "MC-2026-003",
            "jobCompany": "Murchison & Cumming",
            "jobTitle": "Professional Liability Attorney",
        },
        "after": {"note_id": "NOTE-1", "interest": "Very interested"},
        "payload": {
            "link": {"token": "ROLE-1"},
            "note": {
                "id": "NOTE-1",
                "interest": "Very interested",
                "note": "The litigation work is a strong fit.",
                "availability": "Two weeks",
                "skills": "Professional liability",
                "questions": "Which office owns the caseload?",
                "private": True,
            },
        },
    }


class EgeriaCandidateFeedbackTests(unittest.TestCase):
    @patch("main._egeria_log_rows")
    def test_feedback_feed_returns_private_response_and_next_stage(self, read_log):
        read_log.return_value = [
            feedback_event(),
            {"id": "EGR-2", "domain": "law", "event_type": "one_tap_interest_started"},
        ]

        result = main.egeria_one_tap_feedback(domain="law", limit=20)

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["unread_count"], 1)
        notification = result["notifications"][0]
        self.assertEqual(notification["workflow_id"], "ROLE-1")
        self.assertEqual(notification["candidate"]["profile_id"], "2367")
        self.assertEqual(notification["feedback"]["interest"], "Very interested")
        self.assertTrue(notification["feedback"]["private"])
        self.assertEqual(notification["workflow"]["current_stage"], "candidate-review")
        self.assertEqual(len(notification["workflow"]["steps"]), 5)

    @patch("main._write_json_store")
    @patch("main._egeria_log_rows")
    def test_feedback_can_be_acknowledged_without_deleting_it(self, read_log, write_store):
        event = feedback_event()
        read_log.return_value = [event]

        result = main.acknowledge_egeria_one_tap_feedback(
            "EGR-1",
            domain="law",
            acknowledged_by="Darrin",
        )

        self.assertTrue(result["notification"]["acknowledged"])
        self.assertTrue(event["acknowledged_at"])
        self.assertEqual(event["acknowledged_by"], "Darrin")
        write_store.assert_called_once_with(main.EGERIA_PROCESS_LOG_PATH, [event])

        with self.assertRaises(HTTPException) as missing:
            main.acknowledge_egeria_one_tap_feedback("missing", domain="law")
        self.assertEqual(missing.exception.status_code, 404)

    @patch("main._egeria_log_event")
    @patch("main._write_profile_notes_store")
    @patch("main._read_profile_notes_store")
    def test_candidate_submission_saves_private_note_and_queues_review(self, read_notes, write_notes, log_event):
        store = {
            "profiles": {},
            "links": {
                "ROLE-1": {
                    "token": "ROLE-1",
                    "profile_id": "2367",
                    "domain": "law",
                    "candidate_name": "Gregory Brown",
                    "candidate_email": "gregory@example.com",
                    "job_id": "MC-2026-003",
                    "role_company": "Murchison & Cumming",
                    "role_title": "Professional Liability Attorney",
                    "status": "open",
                }
            },
        }
        read_notes.return_value = store

        result = main.profile_role_feedback_submit(
            "ROLE-1",
            interest="Very interested",
            thoughts="The litigation work is a strong fit.",
            skills="Professional liability",
            availability="Two weeks",
            questions="Which office owns the caseload?",
        )

        self.assertTrue(result["ok"])
        note = store["profiles"]["law:2367"]["notes"][0]
        self.assertEqual(note["kind"], "candidate_role_feedback")
        self.assertTrue(note["private"])
        self.assertEqual(store["links"]["ROLE-1"]["status"], "submitted")
        write_notes.assert_called_once_with(store)
        event = log_event.call_args
        self.assertEqual(event.args[1], "candidate_role_feedback_submitted")
        self.assertEqual(event.kwargs["context"]["workflowId"], "ROLE-1")
        self.assertEqual(event.kwargs["context"]["nextStep"], "candidate-review")
        self.assertTrue(event.kwargs["payload"]["note"]["private"])


if __name__ == "__main__":
    unittest.main()
