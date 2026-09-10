import copy
import unittest
from unittest.mock import patch

from azureUtils.routes import azureJobEndpoints


class ExternalContactImportTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "source": "pdl", "source_id": "pdl-sample", "name": "Sample Developer",
            "title": "Software Developer", "skills": ["Python"],
            "profile_url": "https://www.linkedin.com/in/sample-developer",
            "email": "saved@example.test", "phone": "+1 202 555 0100",
            "contact": {
                "primaryEmail": "saved@example.test", "workEmail": "saved@example.test",
                "primaryPhone": "+1 202 555 0100", "mobilePhone": "+1 202 555 0100",
                "personalEmails": ["personal@example.test"],
                "phoneNumbers": ["+1 202 555 0101"],
            },
        }
        self.provider = {
            "status": 200, "likelihood": 9,
            "data": {"id": "pdl-sample", "first_name": "Sample", "last_name": "Developer",
                     "job_title": "Software Developer", "skills": ["Python", "FastAPI"],
                     "linkedin_url": "linkedin.com/in/sample-developer"},
        }
        self.enrich = self.start_patch("peopleDataLabs.enrichPerson", return_value=self.provider)
        self.find_temp = self.start_patch("candidates.findTemporaryExternalProfile", return_value=None)
        self.upload = self.start_patch("candidates.uploadProfile", side_effect=lambda **kwargs: {
            "status": "success", "personid": 700, "name": "Sample Developer",
        })
        self.stored = {
            "personid": 700, "name": "Sample Developer", "email": self.candidate["email"],
            "profileUrl": self.candidate["profile_url"], "location": {},
            "externalProfile": {
                "source": "People Data Labs", "contact": copy.deepcopy(self.candidate["contact"]),
                "enrichment": {"status": "not_requested"},
            },
        }
        self.get_temp = self.start_patch("candidates.getTemporaryExternalProfileForEnrichment", return_value=self.stored)
        self.apply_enrichment = self.start_patch("candidates.applyTemporaryExternalProfileEnrichment", side_effect=lambda pid, domain, candidate, metadata: {
            "status": "success", "personid": pid, "name": "Sample Developer",
            "profileUrl": candidate.get("profile_url"), "enrichment": metadata.get("enrichment"),
        })
        self.start_patch("coreSignal.configured", return_value=False)

    def start_patch(self, target, **kwargs):
        patcher = patch("azureUtils.routes.azureJobEndpoints." + target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def import_candidate(self, **extra):
        return azureJobEndpoints.external_candidate_import({
            "domain": "dev", "candidate": copy.deepcopy(self.candidate), **extra,
        })

    def test_discovery_import_defaults_to_no_new_provider_call(self):
        result = self.import_candidate()
        self.enrich.assert_not_called()
        self.upload.assert_called_once()
        self.assertTrue(result["temporaryProfile"])
        self.assertEqual(result["providerCreditsUsed"], 0)
        self.assertEqual(result["enrichment"]["status"], "not_requested")
        self.assertEqual(self.upload.call_args.kwargs["email"], "saved@example.test")
        self.assertEqual(self.upload.call_args.kwargs["linkedInUrl"], self.candidate["profile_url"])

    def test_only_boolean_true_opts_into_a_new_paid_contact_call(self):
        for value in (False, "true", 1, None, {}):
            with self.subTest(value=value):
                self.import_candidate(enrich_contacts=value)
                self.enrich.assert_not_called()
        result = self.import_candidate(enrich_contacts=True)
        self.enrich.assert_called_once_with(profile=self.candidate["profile_url"], pdl_id="pdl-sample")
        self.assertEqual(result["providerCreditsUsed"], 1)
        self.assertEqual(result["enrichment"]["status"], "completed")

    def test_import_opt_in_retains_contacts_omitted_by_provider(self):
        self.provider["data"].pop("linkedin_url")
        result = self.import_candidate(enrich_contacts=True)
        merged = result["enriched_candidate"]
        self.assertEqual(merged["email"], self.candidate["email"])
        self.assertEqual(merged["phone"], self.candidate["phone"])
        self.assertEqual(merged["profile_url"], self.candidate["profile_url"])
        self.assertEqual(merged["contact"]["personalEmails"], ["personal@example.test"])
        self.assertEqual(merged["contact"]["phoneNumbers"], ["+1 202 555 0101"])
        _, metadata = azureJobEndpoints.candidates.splitExternalProfileDescription(self.upload.call_args.kwargs["candidateDescription"])
        self.assertEqual(metadata["contact"]["primaryPhone"], self.candidate["phone"])
        self.assertEqual(self.upload.call_args.kwargs["email"], self.candidate["email"])

    def test_selected_result_enrichment_preserves_contacts_and_combines_alternatives(self):
        self.provider["data"].update({
            "work_email": "new@example.test", "personal_emails": ["extra@example.test", "personal@example.test"],
        })
        original = copy.deepcopy(self.candidate)
        result = azureJobEndpoints.external_candidate_enrich_result({"domain": "dev", "candidate": self.candidate})
        self.assertEqual(result["candidate"]["email"], "new@example.test")
        self.assertEqual(result["candidate"]["phone"], "+1 202 555 0100")
        self.assertEqual(result["candidate"]["contact"]["personalEmails"], ["extra@example.test", "personal@example.test"])
        self.assertEqual(self.candidate, original)
        self.upload.assert_not_called()

    def test_absent_or_malformed_contact_values_do_not_become_usable_contacts(self):
        self.candidate = {"source": "pdl", "source_id": "pdl-sample", "name": "Sample Developer"}
        self.provider["data"].update({
            "work_email": "unknown", "recommended_personal_email": {"unexpected": "value"},
            "personal_emails": [None, {}, "not an email"], "mobile_phone": "not provided",
            "phone_numbers": [None, {}, "N/A", 123], "linkedin_url": {"unexpected": "value"},
        })
        merged, _ = azureJobEndpoints._enrich_external_pdl_result(self.candidate)
        self.assertEqual(merged["email"], "")
        self.assertEqual(merged["phone"], "")
        self.assertEqual(merged["profile_url"], "")
        self.assertEqual(merged["contact"]["personalEmails"], [])
        self.assertEqual(azureJobEndpoints._candidate_contact_channels(merged), [])
        self.enrich.reset_mock()
        self.candidate.update({"email": "N/A", "phone": {"value": "+1 202 555 0100"}})
        result = self.import_candidate()
        self.assertEqual(result["enriched_candidate"]["email"], "")
        self.assertEqual(result["enriched_candidate"]["phone"], "")
        self.enrich.assert_not_called()

    def test_prior_enrichment_and_duplicate_profiles_are_reused_without_paid_calls(self):
        self.candidate["external_enrichment"] = {
            "status": "completed", "profileVersion": 2, "provider": "People Data Labs Person Enrichment", "creditsUsed": 1,
        }
        result = self.import_candidate(enrich_contacts=True)
        self.assertTrue(result["enrichmentReused"])
        self.assertEqual(result["providerCreditsUsed"], 0)
        self.enrich.assert_not_called()
        self.find_temp.return_value = {"personid": 700, "duplicate": True}
        self.upload.reset_mock()
        duplicate = self.import_candidate(enrich_contacts=True)
        self.assertTrue(duplicate["enrichmentSkipped"])
        self.enrich.assert_not_called()
        self.upload.assert_not_called()

    def test_saved_pdl_enrichment_retains_contact_metadata_and_returns_it(self):
        result = azureJobEndpoints.external_candidate_enrich_temp_profile("700", {"domain": "dev"})
        self.enrich.assert_called_once()
        mapped, metadata = self.apply_enrichment.call_args.args[2:]
        self.assertEqual(mapped["email"], self.candidate["email"])
        self.assertEqual(metadata["contact"]["primaryPhone"], self.candidate["phone"])
        self.assertEqual(metadata["contact"]["personalEmails"], ["personal@example.test"])
        self.assertEqual(result["email"], self.candidate["email"])
        self.assertEqual(result["phone"], self.candidate["phone"])
        self.assertEqual(result["contact"], metadata["contact"])

    def test_saved_reused_enrichment_returns_contacts_without_provider_or_storage_write(self):
        self.stored["externalProfile"]["enrichment"] = {
            "status": "completed", "profileVersion": 2, "provider": "People Data Labs Person Enrichment",
        }
        result = azureJobEndpoints.external_candidate_enrich_temp_profile("700", {"domain": "dev"})
        self.assertTrue(result["reused"])
        self.assertEqual(result["creditsUsed"], 0)
        self.assertEqual(result["email"], self.candidate["email"])
        self.assertEqual(result["phone"], self.candidate["phone"])
        self.enrich.assert_not_called()
        self.apply_enrichment.assert_not_called()

    def test_saved_coresignal_enrichment_preserves_phone_which_provider_does_not_return(self):
        self.stored["externalProfile"]["source"] = "Coresignal"
        self.stored["externalProfile"]["sourceId"] = "core-123"
        self.start_patch("coreSignal.configured", return_value=True)
        self.start_patch("coreSignal.enrichment_dataset", return_value="base")
        self.start_patch("coreSignal.collect_person", return_value={"status": 200, "data": {}, "dataset": "base", "credits_used": 1})
        self.start_patch("_coresignal_collected_row", return_value={
            "name": "Sample Developer", "profile_url": self.candidate["profile_url"],
            "email": "", "phone": "", "contact": {"primaryEmail": "", "primaryPhone": "", "phoneNumbers": []},
        })
        result = azureJobEndpoints.external_candidate_enrich_temp_profile("700", {"domain": "dev"})
        self.assertEqual(result["phone"], self.candidate["phone"])
        self.assertEqual(result["contact"]["phoneNumbers"], ["+1 202 555 0101"])
        self.enrich.assert_not_called()

    def test_selected_coresignal_result_also_preserves_existing_contact_details(self):
        self.candidate["source"] = "coresignal"
        self.candidate["contact"]["primaryProfessionalEmailStatus"] = "verified"
        self.start_patch("coreSignal.enrichment_dataset", return_value="base")
        self.start_patch("coreSignal.collect_person", return_value={"status": 200, "data": {}, "dataset": "base", "credits_used": 1})
        self.start_patch("_coresignal_collected_row", return_value={
            "name": "Sample Developer", "profile_url": self.candidate["profile_url"],
            "email": "", "phone": "", "contact": {"primaryEmail": "", "primaryPhone": "", "primaryProfessionalEmailStatus": ""},
        })
        result = azureJobEndpoints.external_candidate_enrich_result({"domain": "dev", "candidate": self.candidate})
        self.assertEqual(result["candidate"]["phone"], self.candidate["phone"])
        self.assertEqual(result["candidate"]["email"], self.candidate["email"])
        self.assertEqual(result["candidate"]["contact"]["primaryProfessionalEmailStatus"], "verified")
        self.enrich.assert_not_called()


if __name__ == "__main__":
    unittest.main()
