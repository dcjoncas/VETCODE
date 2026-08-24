import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree

from starlette.requests import Request

from azureUtils import externalSearchReport
from azureUtils.routes import azureJobEndpoints
from azureUtils.storage import externalSearchHistory


class ExternalSearchHistoryTests(unittest.TestCase):
    def test_find_out_page_exposes_saved_search_and_ranked_report_actions(self):
        html = (
            Path(__file__).resolve().parents[1] / "ui" / "pages" / "mine-candidate-external.html"
        ).read_text(encoding="utf-8")

        self.assertIn('id="savedSearchSelect"', html)
        self.assertIn('id="btnOpenSavedSearch"', html)
        self.assertIn('id="btnExportCurrentReport"', html)
        self.assertIn("/api/azureJobs/external/search-history", html)
        self.assertIn('fd.append("client_name", attachedAtlasClientName())', html)
        self.assertIn('activeExternalSearch.fields.push(["history_root_id"', html)
        self.assertIn('href="saved-searches.html">View full library</a>', html)
        self.assertIn('get("savedSearchId")', html)
        self.assertIn("downloadActiveSavedSearchReport", html)
        self.assertIn("X-VETCODE-Record-Count", html)
        self.assertIn("X-VETCODE-Filename", html)
        self.assertIn("rankedExportFilename", html)
        self.assertIn("compactSavedSearchLabel", html)
        self.assertIn("CourtListener is ready - choose a search method", html)

    def test_saved_searches_have_a_persistent_domain_library(self):
        backend = Path(__file__).resolve().parents[1]
        html = (backend / "ui" / "pages" / "saved-searches.html").read_text(encoding="utf-8")
        nav = (backend / "ui" / "pages" / "components" / "sideNav.html").read_text(encoding="utf-8")
        main = (backend / "main.py").read_text(encoding="utf-8")

        self.assertIn("Permanent VETCODE search history", html)
        self.assertIn("VETCODE does not automatically delete them", html)
        self.assertIn("Open saved results", html)
        self.assertIn("Export ranked Excel", html)
        self.assertIn("downloadRankedSearch", html)
        self.assertIn("No rows to export", html)
        self.assertIn("Downloaded ranked Excel with", html)
        self.assertIn("X-VETCODE-Filename", html)
        self.assertIn("rankedExportFilename", html)
        self.assertIn("Export search register", html)
        self.assertIn("Load older saved searches", html)
        self.assertNotIn("Delete saved search", html)
        self.assertIn('data-menu-key="saved_searches"', nav)
        self.assertIn('"key": "saved_searches"', main)

    def test_saved_search_workflow_accepts_product_domain_aliases(self):
        backend = Path(__file__).resolve().parents[1]
        for page_name in ("mine-candidate-external.html", "saved-searches.html", "temp-profiles.html"):
            html = (backend / "ui" / "pages" / page_name).read_text(encoding="utf-8")
            with self.subTest(page=page_name):
                self.assertIn('technology: "dev"', html)
                self.assertIn('tech: "dev"', html)
                self.assertIn('engineering: "engineer"', html)
                self.assertIn('dentalready: "dental"', html)

    @patch("azureUtils.routes.azureJobEndpoints.externalSearchHistory.count_searches")
    @patch("azureUtils.routes.azureJobEndpoints.externalSearchHistory.list_searches")
    def test_saved_search_library_is_isolated_for_every_product_domain(self, list_searches, count_searches):
        list_searches.return_value = []
        count_searches.return_value = 0

        aliases = {
            "tech": "dev",
            "technology": "dev",
            "engineering": "engineer",
            "buildready": "engineer",
            "dentalready": "dental",
            "legalready": "law",
        }
        for requested, stored in aliases.items():
            with self.subTest(requested=requested, stored=stored):
                list_searches.reset_mock()
                count_searches.reset_mock()
                response = azureJobEndpoints.external_candidate_search_history(
                    domain=requested,
                    limit=25,
                    offset=0,
                )
                list_searches.assert_called_once_with(stored, 25, 0)
                count_searches.assert_called_once_with(stored)
                self.assertEqual(response["total"], 0)

    @patch("azureUtils.routes.azureJobEndpoints.externalSearchHistory.count_searches")
    @patch("azureUtils.routes.azureJobEndpoints.externalSearchHistory.list_searches")
    def test_saved_search_history_is_paginated_without_hiding_older_queries(self, list_searches, count_searches):
        list_searches.return_value = [{"id": 101, "queryName": "Saved_QRY"}]
        count_searches.return_value = 250

        response = azureJobEndpoints.external_candidate_search_history(
            domain="law",
            limit=100,
            offset=100,
        )

        list_searches.assert_called_once_with("law", 100, 100)
        count_searches.assert_called_once_with("law")
        self.assertEqual(response["total"], 250)
        self.assertTrue(response["hasMore"])
        self.assertIn("not automatically deleted", response["retention"])

    def test_query_name_uses_required_parts_and_not_found_fallbacks(self):
        self.assertEqual(
            externalSearchHistory.build_query_name(
                "General Dentist / Lead",
                "Bright Smile Dental",
                date(2026, 8, 24),
            ),
            "General_Dentist_Lead_Bright_Smile_Dental_2026-08-24_QRY",
        )
        self.assertEqual(
            externalSearchHistory.build_query_name("", "", date(2026, 8, 24)),
            "Not_Found_Not_Found_2026-08-24_QRY",
        )

    def test_query_cache_key_is_stable_and_changes_with_page_token(self):
        first = externalSearchHistory.query_cache_key({"source": "pdl", "scrollToken": ""})
        repeated = externalSearchHistory.query_cache_key({"scrollToken": "", "source": "pdl"})
        next_page = externalSearchHistory.query_cache_key({"source": "pdl", "scrollToken": "next"})

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, next_page)

    @patch("azureUtils.routes.azureJobEndpoints.peopleDataLabs.searchLawyers")
    @patch("azureUtils.routes.azureJobEndpoints._get_job_skills")
    @patch("azureUtils.routes.azureJobEndpoints.externalSearchHistory.get_cached_search")
    def test_repeated_pdl_search_uses_saved_results_without_provider_call(self, cached, get_job, pdl_search):
        get_job.return_value = (
            {
                "jd_id": 85,
                "company": "Example Client",
                "title": "Litigation Attorney",
                "description": "California litigation attorney",
            },
            ["Litigation"],
        )
        cached.return_value = {
            "metadata": {
                "id": 9,
                "rootId": 9,
                "queryName": "Litigation_Attorney_Example_Client_2026_08_24_QRY",
                "cacheHit": True,
            },
            "response": {
                "source": "pdl",
                "results": [{"name": "Saved Candidate", "score": 88}],
                "sourceAudit": {"provider": "People Data Labs", "estimatedCreditsUsed": 5},
                "pagination": {"pageSize": 5, "hasNext": False},
            },
        }

        response = azureJobEndpoints.external_candidate_search(
            domain="law",
            jd_id="85",
            source="pdl",
            top_k=5,
            titles="",
            practice_areas="",
            locations="",
            region="",
            min_years=0,
            strict_locations=None,
            scroll_token="",
        )

        pdl_search.assert_not_called()
        self.assertEqual(response["results"][0]["name"], "Saved Candidate")
        self.assertFalse(response["sourceAudit"]["queryExecuted"])
        self.assertEqual(response["sourceAudit"]["estimatedCreditsUsed"], 0)
        self.assertTrue(response["savedSearch"]["cacheHit"])

    def test_ranked_report_has_order_rank_sources_and_clickable_profile_links(self):
        workbook = externalSearchReport.build_ranked_search_xlsx(
            {
                "metadata": {
                    "queryName": "Dentist_Bright_Smile_2026_08_24_QRY",
                    "jdName": "Dentist",
                    "clientName": "Bright Smile",
                    "source": "pdl",
                    "createdAt": "2026-08-24T12:00:00+00:00",
                }
            },
            [
                {
                    "searchOrder": 2,
                    "rank": 1,
                    "name": "Anne Podraza",
                    "title": "Dentist",
                    "location": "Denver, Colorado",
                    "matchScore": 91,
                    "matchBand": "Strong",
                    "matched": ["General Dentistry"],
                    "missing": ["Invisalign"],
                    "source": "People Data Labs",
                    "evidenceSources": ["People Data Labs", "CourtListener / RECAP"],
                    "sourceProfileUrl": "https://www.linkedin.com/in/anne-podraza",
                    "tempProfileUrl": "https://vetcode.example/ui/pages/profile-preview.html?profileId=2389",
                    "tempProfileId": 2389,
                }
            ],
        )

        with ZipFile(BytesIO(workbook)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            rels = archive.read("xl/worksheets/_rels/sheet1.xml.rels").decode("utf-8")
            sheet_root = ElementTree.fromstring(sheet)
            styles_root = ElementTree.fromstring(archive.read("xl/styles.xml"))

        self.assertIn("Search Order", sheet)
        self.assertIn("Current Rank", sheet)
        self.assertIn("Candidate rows", sheet)
        self.assertIn("Not returned", sheet)
        self.assertIn("TEMP Profile", sheet)
        self.assertIn("People Data Labs; CourtListener / RECAP", sheet)
        self.assertIn('ref="N9"', sheet)
        self.assertIn('ref="O9"', sheet)
        self.assertIn("profileId=2389", rels)
        worksheet_children = [node.tag.rsplit("}", 1)[-1] for node in sheet_root]
        self.assertLess(worksheet_children.index("autoFilter"), worksheet_children.index("mergeCells"))
        font_children = [
            [node.tag.rsplit("}", 1)[-1] for node in font]
            for font in styles_root.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}fonts")
        ]
        self.assertEqual(font_children[1], ["b", "sz", "color", "name"])
        self.assertEqual(font_children[2], ["b", "sz", "color", "name"])
        self.assertEqual(font_children[3], ["u", "sz", "color", "name"])

    @patch("azureUtils.routes.azureJobEndpoints.externalSearchHistory.get_search_group")
    @patch("azureUtils.routes.azureJobEndpoints._saved_search_report_rows")
    def test_ranked_export_uses_short_filename_and_reports_rows(self, report_rows, get_group):
        get_group.return_value = {
            "metadata": {
                "id": 5,
                "rootId": 5,
                "queryName": "Very_Long_Job_Description_Client_Name_2026_08_24_QRY",
            },
            "pages": [],
        }
        report_rows.return_value = [{"searchOrder": 1, "rank": 1, "name": "Candidate One"}]
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/azureJobs/external/search-history/5/export",
                "headers": [],
                "scheme": "https",
                "server": ("vetcode.example", 443),
                "query_string": b"",
            }
        )

        response = azureJobEndpoints.external_candidate_export_saved_search("5", request, domain="law")

        self.assertEqual(response.headers["content-disposition"], 'attachment; filename="law-ranked-5.xlsx"')
        self.assertEqual(response.headers["x-vetcode-filename"], "law-ranked-5.xlsx")
        self.assertEqual(response.headers["x-vetcode-record-count"], "1")

    @patch("azureUtils.routes.azureJobEndpoints.externalSearchHistory.get_search_group")
    @patch("azureUtils.routes.azureJobEndpoints._saved_search_report_rows")
    def test_ranked_export_rejects_saved_queries_without_candidate_rows(self, report_rows, get_group):
        get_group.return_value = {"metadata": {"queryName": "Empty_Query_QRY"}, "pages": []}
        report_rows.return_value = []
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/azureJobs/external/search-history/3/export",
                "headers": [],
                "scheme": "https",
                "server": ("vetcode.example", 443),
                "query_string": b"",
            }
        )

        with self.assertRaisesRegex(Exception, "no candidate rows") as raised:
            azureJobEndpoints.external_candidate_export_saved_search("3", request, domain="law")

        self.assertEqual(raised.exception.status_code, 409)

    def test_ranked_export_uses_forwarded_https_for_temp_profile_links(self):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/azureJobs/external/search-history/3/export",
                "headers": [(b"x-forwarded-proto", b"https")],
                "scheme": "http",
                "server": ("vetcode-dev.up.railway.app", 80),
                "query_string": b"",
            }
        )

        self.assertEqual(
            azureJobEndpoints._public_request_base_url(request),
            "https://vetcode-dev.up.railway.app",
        )


if __name__ == "__main__":
    unittest.main()
