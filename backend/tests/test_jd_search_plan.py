import json
import unittest
from jd_search_plan import build_plan, open_to_work
from external_evidence_match import build_professional_match
from peopleDataLabs.peopleSearch import build_candidate_search_payload, PeopleDataLabsError


class JobRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.jd = {"title": "Senior / Staff Full-Stack Engineer (Founding Engineer)", "description":
                   "Own a Java / Spring Boot backend.\nShip React applications.\nRun PostgreSQL, Docker and Linux.\n"
                   "Bonus: Airflow and ActiveMQ.\n6+ years building full-stack systems.",
                   "skills": ["Java", "Boot", "React", "Make", "WAS", "Teams", "Airflow", "ActiveMQ"]}

    def test_extracts_core_bonus_aliases_and_experience_without_junk(self):
        plan = build_plan(self.jd)
        self.assertEqual(plan["coreSkills"][:3], ["Java", "Spring Boot", "React"])
        self.assertEqual(plan["bonusSkills"], ["Airflow", "ActiveMQ"])
        self.assertEqual(plan["minYears"], 6)
        self.assertIn("software engineer", plan["titles"])
        self.assertNotIn("Make", plan["coreSkills"])
        self.assertTrue(all(item["jdEvidence"] for item in plan["skills"]))

    def test_ignore_all_cannot_become_linkedin_only_query(self):
        payload = build_candidate_search_payload([], [], [], workforce_location="either", job_plan=build_plan(self.jd), size=5)
        must = payload["query"]["bool"]["must"]
        self.assertEqual(len(must), 5)  # role + three independent core skills + profile
        query = json.dumps(payload)
        for term in ("java", "spring boot", "react", "software engineer"):
            self.assertIn(term, query)
        self.assertNotIn("activemq", query)
        self.assertNotIn("minimum_should_match", query)
        self.assertEqual(payload["size"], 5)

    def test_missing_relevance_blocks_before_spending(self):
        with self.assertRaises(PeopleDataLabsError):
            build_candidate_search_payload([], [], [], job_plan=build_plan({"title": "Role", "description": "Be great."}))

    def test_core_engineer_outranks_marketing_and_bonus_only(self):
        core = {"title": "Senior Software Developer", "skills": ["Java", "Spring Boot", "React", "Postgres", "Docker", "Linux"], "years_experience": 8}
        good = build_professional_match(self.jd, core)
        marketing = build_professional_match(self.jd, {"title": "Marketing Director", "skills": ["Management", "Graphic Design"]})
        bonus = build_professional_match(self.jd, {"title": "Analyst", "skills": ["Airflow", "ActiveMQ"]})
        self.assertGreater(good["score"], 90)
        self.assertEqual(marketing["score"], 0)
        self.assertLess(bonus["score"], 10)
        self.assertIn("PostgreSQL", good["matched"])
        self.assertNotIn("Airflow", good["mustHaveUnknown"])

    def test_javascript_does_not_supply_java_evidence(self):
        result = build_professional_match(self.jd, {"title": "Engineer", "skills": ["JavaScript"]})
        self.assertNotIn("Java", result["matched"])

    def test_open_to_work_is_explicit_text_not_inferred_availability(self):
        for candidate in ({}, {"job_title": "Unemployed"}, {"headline": "Not open to work"},
                          {"headline": "No longer #OpenToWork"}, {"headline": "Helping candidates open to work"},
                          {"headline": "Hiring engineers #OpenToWork"}):
            self.assertEqual(open_to_work(candidate)["status"], "unknown")
        signal = open_to_work({"profile_data": {"headline": "Software engineer | #OpenToWork"}})
        self.assertEqual(signal["status"], "signal")
        self.assertFalse(signal["linkedinBadgeVerified"])
        self.assertEqual(signal["evidence"], "#OpenToWork")


if __name__ == "__main__":
    unittest.main()
