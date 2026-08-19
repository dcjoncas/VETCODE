import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
PAGES = BACKEND / "ui" / "pages"


class InterviewStageWorkflowTests(unittest.TestCase):
    def test_candidate_review_is_explicitly_internal_and_precedes_client_interview(self):
        schedule = (PAGES / "schedule-interview.html").read_text(encoding="utf-8")
        flow = (PAGES / "components" / "processFlow.html").read_text(encoding="utf-8")

        self.assertIn("Candidate Review — Internal DevReady", schedule)
        self.assertIn("The client is not part of this meeting", schedule)
        self.assertIn("7A - Candidate Review (DevReady)", flow)
        self.assertIn("Only after the candidate is confirmed interested", flow)
        self.assertLess(flow.index("7A - Candidate Review (DevReady)"), flow.index("7B - Client Interview"))

    def test_email_and_calendly_steps_are_saved_in_order(self):
        schedule = (PAGES / "schedule-interview.html").read_text(encoding="utf-8")

        self.assertIn("async function sendDraftToEmail()", schedule)
        self.assertIn('api("/api/outlook/mail/send"', schedule)
        self.assertIn("Scheduling email opened for sending", schedule)
        self.assertIn('params.set("email", candidateEmail)', schedule)
        self.assertIn('params.set("guests", guests.join(","))', schedule)
        self.assertIn("async function confirmCalendlyInviteSent()", schedule)
        self.assertIn("Candidate Review scheduled", schedule)
        self.assertIn("Conduct the internal DevReady Candidate Review", schedule)

    def test_client_interview_requires_an_interested_candidate(self):
        schedule = (PAGES / "schedule-interview.html").read_text(encoding="utf-8")
        status = (PAGES / "status-tracker.html").read_text(encoding="utf-8")

        self.assertIn('candidateInterestDecision() !== "interested"', schedule)
        self.assertIn("async function recordCandidateInterest(decision)", status)
        self.assertIn("Interested — set up Client Interview", status)
        self.assertIn("Candidate not interested", status)
        self.assertIn('interview=client', status)
        self.assertIn("function moveToNextReviewCandidate()", status)

    def test_backend_draft_language_matches_the_two_stage_workflow(self):
        router = (BACKEND / "calendar_router.py").read_text(encoding="utf-8")

        self.assertIn('else "Candidate Review"', router)
        self.assertIn("internal DevReady meeting", router)
        self.assertNotIn('else "Ready Interview"', router)

    def test_devready_agent_is_automatic_for_both_interview_types(self):
        schedule = (PAGES / "schedule-interview.html").read_text(encoding="utf-8")
        router = (BACKEND / "calendar_router.py").read_text(encoding="utf-8")
        status = (PAGES / "status-tracker.html").read_text(encoding="utf-8")

        self.assertIn('email: "egeria@devready.ai"', schedule)
        self.assertIn("[meetingAgentAttendee(), ...primaryAttendees()", schedule)
        self.assertIn("DEVREADY_MEETING_AGENT_EMAIL", router)
        self.assertIn("attendee_emails.append(DEVREADY_MEETING_AGENT_EMAIL)", router)
        self.assertIn('"interview_types": ["ready", "client"]', router)
        self.assertIn("DevReady agent", status)

    def test_archive_and_local_view_cleanup_have_distinct_reversible_actions(self):
        status = (PAGES / "status-tracker.html").read_text(encoding="utf-8")

        self.assertIn("Archive selected record", status)
        self.assertIn("async function archiveSelectedRecord()", status)
        self.assertIn("await persistSelectedRecord()", status)
        self.assertIn("Clear local view", status)
        self.assertIn("function clearLocalTrackingView()", status)
        self.assertIn("Undo clear", status)
        self.assertIn("async function undoClearLocalTrackingView()", status)
        self.assertIn("it does not delete saved interview history", status)
        self.assertIn("Tracking records are hidden from this browser view", status)
        self.assertIn("trackingHiddenIds:", status)
        self.assertIn("trackingClearUndo:", status)


if __name__ == "__main__":
    unittest.main()
