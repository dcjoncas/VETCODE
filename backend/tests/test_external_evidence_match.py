"""No-spend synthetic fixtures for JD/professional-profile comparison."""

import copy
import unittest

from external_evidence_match import build_professional_match, match_is_current


class ProfessionalEvidenceMatchTests(unittest.TestCase):
    def setUp(self):
        self.jd = {"jd_id": "job-test", "title": "Senior Software Engineer", "skills": ["Python", "AWS", "PostgreSQL"]}
        self.criteria = {
            "titles": ["Senior Software Engineer"], "mustHaveSkills": ["Python", "AWS"],
            "minYears": 5, "licensesOrCertifications": ["AWS Certified Solutions Architect"],
            "locations": ["Denver"], "workArrangements": ["hybrid"], "workforceLocations": ["onshore"],
        }
        self.candidate = {
            "source_label": "People Data Labs", "title": "Senior Software Developer",
            "skills": ["Python", "Amazon Web Services", "Postgres"], "years_experience": 7,
            "location": "Denver, Colorado, United States",
            "profile_data": {"certifications": [{"name": "AWS Certified Solutions Architect"}]},
        }

    def match(self, candidate=None, criteria=None, jd=None):
        return build_professional_match(self.jd if jd is None else jd,
                                        self.candidate if candidate is None else candidate,
                                        self.criteria if criteria is None else criteria)

    def test_complete_evidence_has_explanations_and_no_resume_or_probability_claim(self):
        result = self.match()
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["coveragePercent"], 100)
        self.assertFalse(result["resumeCompared"])
        self.assertFalse(result["providerContacted"])
        self.assertEqual(result["providerCreditsUsed"], 0)
        self.assertEqual(result["confidence"], "Not statistically calibrated")
        for criterion in result["criteria"]:
            if criterion["status"] == "matched":
                self.assertTrue(criterion["evidence"])
                self.assertIn("field", criterion["evidence"][0])

    def test_unknown_skill_is_not_a_confirmed_gap(self):
        candidate = copy.deepcopy(self.candidate)
        candidate["skills"] = ["Python"]
        candidate["profile_data"] = {}
        result = self.match(candidate=candidate)
        self.assertIn("AWS", result["unknown"])
        self.assertEqual(result["gaps"], [])
        self.assertIn("AWS", result["mustHaveUnknown"])
        self.assertLess(result["score"], 100)
        self.assertLess(result["coveragePercent"], 100)

    def test_known_experience_gap_is_distinct_from_unknown(self):
        candidate = dict(self.candidate, years_experience=2)
        result = self.match(candidate=candidate)
        self.assertIn("Reported total experience: at least 5 years", result["mustHaveGaps"])
        self.assertEqual(result["coveragePercent"], 100)
        self.assertEqual(result["score"], 85)

    def test_missing_or_legacy_zero_years_is_unknown_not_zero_experience(self):
        for value in (None, "", 0, False, "bad", -1, float("nan")):
            with self.subTest(value=value):
                result = self.match(candidate=dict(self.candidate, years_experience=value))
                self.assertTrue(any(item["group"] == "experience" and item["status"] == "unknown" for item in result["criteria"]))
        result = self.match(candidate=dict(self.candidate, inferred_years_experience=0))
        self.assertTrue(result["mustHaveGaps"])

    def test_raw_pdl_and_normalized_search_profiles_have_same_match(self):
        raw = {"job_title": self.candidate["title"], "skills": self.candidate["skills"],
               "inferred_years_experience": 7, "certifications": self.candidate["profile_data"]["certifications"],
               "location_name": self.candidate["location"]}
        self.assertEqual(self.match(candidate=raw)["score"], self.match()["score"])

    def test_no_jd_has_no_percentage_even_with_recruiter_criteria(self):
        self.assertIsNone(self.match(jd={})["score"])
        self.assertEqual(self.match(jd={})["status"], "unavailable")

    def test_contact_identity_and_demographic_fields_are_not_evidence(self):
        result = self.match(candidate={
            "name": "Python AWS PostgreSQL", "email": "python@example.test",
            "phone": "+1 555 010 1000", "profile_url": "https://linkedin.com/in/aws",
            "location": "Denver", "age": 35, "gender": "male", "nationality": "US",
        })
        self.assertIsNone(result["score"])
        self.assertIsNone(result["coveragePercent"])
        self.assertIn("contact links alone", result["reason"])

    def test_derived_scores_cannot_be_recycled_as_professional_evidence(self):
        derived = {"top_matches": ["Python", "AWS"], "providerSkills": ["Python", "AWS"],
                   "score": 100, "score_details": {"matched": ["Python"]}}
        self.assertIsNone(self.match(candidate=derived)["score"])
        with_evidence = dict(derived, title="Account Executive")
        self.assertEqual(self.match(candidate=with_evidence)["score"], 0)

    def test_match_fingerprint_ignores_identity_contacts_and_prior_score(self):
        original = self.match()
        edited = dict(self.candidate, name="Another name", email="a@example.test", score=1,
                      top_matches=["anything"], gender="female", birth_year=1970)
        self.assertTrue(match_is_current(original, self.jd, edited, self.criteria))

    def test_fingerprint_invalidates_for_job_criteria_and_professional_evidence(self):
        result = self.match()
        for candidate, criteria, jd in (
            (dict(self.candidate, skills=["Python"]), self.criteria, self.jd),
            (self.candidate, dict(self.criteria, minYears=10), self.jd),
            (self.candidate, self.criteria, dict(self.jd, description="Changed responsibilities")),
            (self.candidate, dict(self.criteria, ignoredCriteria=["titles"]), self.jd),
            (self.candidate, self.criteria, dict(self.jd, jd_id="different-job")),
        ):
            self.assertFalse(match_is_current(result, jd, candidate, criteria))

    def test_ignored_criteria_do_not_affect_denominator_or_required_gaps(self):
        criteria = dict(self.criteria, ignoredCriteria=["skills", "licenses", "experience"])
        candidate = {"title": "Senior Software Engineer", "years_experience": 1}
        result = self.match(candidate=candidate, criteria=criteria)
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["requiredCount"], 1)
        self.assertEqual(result["mustHaveGaps"], [])
        self.assertEqual(result["mustHaveUnknown"], [])
        for item in result["criteria"]:
            if item["status"] == "ignored":
                self.assertEqual(item["weight"], 0)

    def test_all_ignored_produces_no_percentage(self):
        self.assertIsNone(self.match(criteria=dict(self.criteria, ignoreAll=True))["score"])

    def test_exact_technology_boundaries_prevent_java_javascript_false_match(self):
        result = self.match(jd={"title": "Developer", "skills": ["Java", "C", "C#", "C++"]},
                            criteria={"ignoredCriteria": ["titles"]},
                            candidate={"skills": ["JavaScript", "C++"]})
        self.assertEqual(result["matched"], ["C++"])
        self.assertEqual(result["score"], 25)

    def test_one_word_of_compound_requirement_is_not_full_support(self):
        result = self.match(jd={"title": "Engineer", "skills": ["distributed systems", "project management"]},
                            criteria={"ignoredCriteria": ["titles"]}, candidate={"skills": ["systems", "project"]})
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["matched"], [])

    def test_narrative_experience_is_shown_for_review_not_confirmed_as_skill(self):
        result = self.match(candidate={"title": "Senior Software Engineer", "profile_data": {
            "experience": [{"title": {"name": "Backend Developer"}, "summary": "Built production APIs with Python and PostgreSQL on Amazon Web Services."}]
        }})
        self.assertIn("Python", result["unknown"])
        self.assertIn("AWS", result["unknown"])
        python = next(item for item in result["criteria"] if item["label"] == "Python")
        self.assertEqual(python["evidence"][0]["field"], "profile_data.experience[0].summary")

    def test_negative_or_aspirational_skill_mentions_do_not_count(self):
        for text in ("No experience with Python", "Not familiar with Python", "Want to learn Python", "Learning Python", "I plan to learn Python next year.", "Python: no experience."):
            result = self.match(jd={"title": "Developer", "skills": ["Python"]},
                                criteria={"ignoredCriteria": ["titles"]}, candidate={"summary": text})
            self.assertEqual(result["score"], 0, text)

    def test_positive_narrative_mentions_remain_visible_but_unconfirmed(self):
        for text in ("No Java experience, but extensive Python expertise.", "Not only Python but also Go."):
            result = self.match(jd={"title": "Developer", "skills": ["Python"]},
                                criteria={"ignoredCriteria": ["titles"]}, candidate={"summary": text})
            self.assertEqual(result["score"], 0, text)
            python = next(item for item in result["criteria"] if item["label"] == "Python")
            self.assertEqual(python["status"], "unknown")
            self.assertIn("Python", python["evidence"][0]["text"])

    def test_complex_negative_and_studying_narratives_never_confirm_skills(self):
        for text in ("No experience in JavaScript, Python, or Go.", "Cannot use Python.", "Currently studying Python."):
            result = self.match(jd={"title": "Engineer", "skills": ["Python"]}, criteria={"ignoredCriteria": ["titles"]},
                                candidate={"summary": text})
            self.assertEqual(result["score"], 0, text)
            self.assertEqual(result["matched"], [])

    def test_explicit_contrary_narrative_reports_gap_and_conflict_needs_review(self):
        result = self.match(jd={"title": "Engineer", "skills": ["Python"]}, criteria={"ignoredCriteria": ["titles"]},
                            candidate={"summary": "I have no Python experience."})
        self.assertEqual(result["gaps"], ["Python"])
        self.assertEqual(result["unknown"], [])
        conflict = self.match(jd={"title": "Engineer", "skills": ["Python"]}, criteria={"ignoredCriteria": ["titles"]},
                              candidate={"summary": "I have no Python experience.", "skills": ["Python"]})
        self.assertEqual(conflict["unknown"], ["Python"])
        self.assertEqual(conflict["score"], 0)

    def test_skill_tag_does_not_parse_arbitrary_sentences_as_qualification(self):
        for value in ("Cannot use Python", "Currently studying Python", "No JavaScript, Python, or Go"):
            result = self.match(jd={"title": "Engineer", "skills": ["Python"]}, criteria={"ignoredCriteria": ["titles"]}, candidate={"skills": [value]})
            self.assertEqual(result["score"], 0)

    def test_original_skill_indices_survive_duplicate_or_excluded_tags(self):
        result = self.match(jd={"title": "Engineer", "skills": ["Python"]}, criteria={"ignoredCriteria": ["titles"]},
                            candidate={"skills": ["Java", "Java", "Python"]})
        python = next(item for item in result["criteria"] if item["label"] == "Python")
        self.assertEqual(python["evidence"][0]["field"], "skills[2]")

    def test_normalized_education_provenance_retains_profile_data_prefix(self):
        result = self.match(jd={"title": "Engineer", "skills": ["Computer Science"]}, criteria={"ignoredCriteria": ["titles"]},
                            candidate={"profile_data": {"education": [{"majors": ["Computer Science"]}]}})
        degree = next(item for item in result["criteria"] if item["label"] == "Computer Science")
        self.assertEqual(degree["evidence"][0]["field"], "profile_data.education[0].majors")

    def test_explicit_not_expired_text_is_not_treated_as_expired(self):
        candidate = dict(self.candidate, profile_data={"certifications": [{"name": "AWS Certified Solutions Architect (not expired)"}]})
        result = self.match(candidate=candidate)
        credential = next(item for item in result["criteria"] if item["group"] == "licenses")
        self.assertEqual(credential["status"], "matched")

    def test_title_only_does_not_claim_complete_jd_match(self):
        result = self.match(jd={"title": "Engineer", "description": "Must have ten years of Python experience and current AWS certification."},
                            candidate={"title": "Engineer"}, criteria={})
        self.assertIsNone(result["score"])
        self.assertIsNone(result["coveragePercent"])
        self.assertFalse(result["fullJobAssessment"])
        self.assertIn("title alone", result["reason"])

    def test_expired_credential_is_a_gap_not_supported_current_credential(self):
        for credential in ({"name": "AWS Certified Solutions Architect", "end_date": "2020-01-01"},
                           {"name": "AWS Certified Solutions Architect", "status": "expired"},
                           {"name": "AWS Certified Solutions Architect (expired 2020)"}):
            candidate = dict(self.candidate, profile_data={"certifications": [credential]})
            result = self.match(candidate=candidate)
            self.assertTrue(any("AWS Certified" in item for item in result["mustHaveGaps"]))
            self.assertLess(result["score"], 100)

    def test_evidence_excerpt_contains_term_even_late_in_long_summary(self):
        text = "This is a lengthy professional history. " * 20 + "Built APIs in Python for an internal tool."
        result = self.match(jd={"title": "Developer", "skills": ["Python"]},
                            candidate={"summary": text}, criteria={"ignoredCriteria": ["titles"]})
        python = next(item for item in result["criteria"] if item["label"] == "Python")
        self.assertIn("Python", python["evidence"][0]["text"])

    def test_credential_requires_credential_field_not_skill_or_title_coincidence(self):
        result = self.match(candidate={"title": "AWS Certified Solutions Architect", "skills": ["AWS"]})
        credential = next(item for item in result["criteria"] if item["group"] == "licenses")
        self.assertEqual(credential["status"], "unknown")

    def test_none_required_credential_is_not_a_requirement(self):
        result = self.match(criteria=dict(self.criteria, licensesOrCertifications=["None required"]))
        self.assertFalse(any(item["group"] == "licenses" for item in result["criteria"]))

    def test_experience_ranges_are_or_not_minimum_only(self):
        criteria = dict(self.criteria, experienceRanges=["3-5", "10-14"])
        self.assertTrue(self.match(candidate=dict(self.candidate, years_experience=7), criteria=criteria)["gaps"])
        self.assertFalse(self.match(candidate=dict(self.candidate, years_experience=12), criteria=criteria)["gaps"])

    def test_logistics_unknowns_do_not_raise_or_lower_professional_score(self):
        other = dict(self.candidate, location="Toronto, Canada")
        self.assertEqual(self.match(candidate=other)["score"], self.match()["score"])
        result = self.match()
        logistics = [item for item in result["criteria"] if item["group"] in {"arrangements", "workforce"}]
        self.assertTrue(all(item["status"] == "unknown" and item["weight"] == 0 for item in logistics))

    def test_professional_requirements_are_not_truncated_to_twelve(self):
        skills = [f"Technology {index}" for index in range(20)]
        result = self.match(jd={"title": "Engineer", "skills": skills}, criteria={"ignoredCriteria": ["titles"]},
                            candidate={"skills": skills[:-1]})
        self.assertEqual(result["requiredCount"], 20)
        self.assertEqual(result["score"], 95)
        self.assertEqual(result["unknown"], ["Technology 19"])

    def test_duplicate_alias_requirements_do_not_double_weight(self):
        result = self.match(jd={"title": "Engineer", "skills": ["AWS", "Amazon Web Services", "Python"]},
                            criteria={"ignoredCriteria": ["titles"]}, candidate={"skills": ["AWS"]})
        self.assertEqual(result["requiredCount"], 2)
        self.assertEqual(result["score"], 50)

    def test_protected_requirements_excluded_and_protected_profile_fields_ignored(self):
        result = self.match(jd={"title": "Engineer", "skills": ["Python", "male", "US citizenship", "age under 40"]},
                            criteria={"ignoredCriteria": ["titles"]}, candidate={"skills": ["Python"], "gender": "male"})
        self.assertEqual(result["requiredCount"], 1)
        self.assertEqual(result["score"], 100)

    def test_same_contract_supports_legal_engineering_and_dental_skills(self):
        for title, skill, credential in (("Attorney", "Civil Litigation", "California attorney license"),
                                         ("Civil Engineer", "Structural Analysis", "Professional Engineer"),
                                         ("Dental Hygienist", "Digital Radiography", "RDH")):
            result = self.match(jd={"title": title, "skills": [skill]}, criteria={"licensesOrCertifications": [credential]},
                                candidate={"title": title, "skills": [skill], "certifications": [{"name": credential}]})
            self.assertEqual(result["score"], 100)


if __name__ == "__main__":
    unittest.main()
