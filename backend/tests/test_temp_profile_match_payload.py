import unittest
from unittest.mock import Mock, patch

from azureUtils.storage import candidates


class TemporaryProfileMatchPayloadTests(unittest.TestCase):
    def list_with_match(self, value):
        description = candidates.attachExternalProfileMetadata(
            "Temporary external profile. Confirm details before publishing.",
            {"source": "People Data Labs", "match": value},
        )
        connection = Mock()
        connection.cursor.return_value.fetchall.return_value = [
            (7, "Sample", "Candidate", "", "Engineer", description, "", "", "", "", None)
        ]
        with patch.object(candidates.client, "getConnection", return_value=connection):
            result = candidates.listTemporaryExternalProfiles("dev")
        connection.commit.assert_not_called()
        connection.close.assert_called_once()
        return result["profiles"][0]

    def test_complete_structured_match_is_available_without_flattening_away_context(self):
        match = {
            "status": "calculated", "score": 62, "evidenceType": "structured_professional_profile",
            "coveragePercent": 73, "criteriaSnapshot": {"ignoredCriteria": ["cities"]},
            "criteria": [{"label": "Python", "status": "matched", "evidence": [{"field": "skills[0]", "text": "Python"}]}],
            "evidenceFingerprint": "fixture-fingerprint", "fullJobAssessment": False,
        }
        result = self.list_with_match(match)
        self.assertEqual(result["match"], match)
        self.assertEqual(result["matchScore"], 62)
        self.assertTrue(result["matchCalculated"])

    def test_unavailable_null_percentage_and_reason_survive(self):
        match = {"status": "unavailable", "score": None, "coveragePercent": None,
                 "evidenceType": "structured_professional_profile", "reason": "No active professional requirements."}
        result = self.list_with_match(match)
        self.assertEqual(result["match"], match)
        self.assertIsNone(result["matchScore"])
        self.assertFalse(result["matchCalculated"])

    def test_legacy_match_is_not_mislabeled_as_structured_evidence(self):
        result = self.list_with_match({"status": "calculated", "score": 95})
        self.assertEqual(result["match"], {"status": "calculated", "score": 95})
        self.assertNotIn("evidenceType", result["match"])
        self.assertNotIn("criteriaSnapshot", result["match"])

    def test_missing_or_malformed_match_is_safe_not_zero_percent(self):
        for value in (None, "old non-object", []):
            result = self.list_with_match(value)
            self.assertEqual(result["match"], {})
            self.assertIsNone(result["matchScore"])
            self.assertEqual(result["matchStatus"], "not_run")


if __name__ == "__main__":
    unittest.main()
