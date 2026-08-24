import unittest
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree

from azureUtils import linkedinResultsExport


class LinkedInResultsExportTests(unittest.TestCase):
    def test_workbook_contains_rich_profile_fields_and_safe_linkedin_hyperlink(self):
        workbook = linkedinResultsExport.build_linkedin_results_xlsx(
            [
                {
                    "personid": 2389,
                    "name": "Anne Podraza",
                    "title": "Healthcare Consultant",
                    "email": "anne@example.org",
                    "phone": "+1 609 555 0100",
                    "location": "Trenton, New Jersey, United States",
                    "source": "Coresignal Preview",
                    "linkedInEnriched": True,
                    "enrichmentLikelihood": 9,
                    "matchScore": 67,
                    "matchBand": "strong",
                    "matchMatched": ["Patient care", "Leadership"],
                    "matchMissing": ["Digital radiography"],
                    "profileUrl": "https://www.linkedin.com/in/anne-podraza",
                    "updated": "2026-08-18T12:00:00+00:00",
                },
                {
                    "personid": 2390,
                    "name": "=WEBSERVICE(\"https://unsafe.example\")",
                    "linkedInEnriched": True,
                    "profileUrl": "https://linkedin.com.evil.example/in/not-linkedin",
                },
            ],
            "dental",
        )

        with ZipFile(BytesIO(workbook)) as archive:
            names = set(archive.namelist())
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            relationships = archive.read("xl/worksheets/_rels/sheet1.xml.rels").decode("utf-8")
            sheet_root = ElementTree.fromstring(sheet)
            styles_root = ElementTree.fromstring(archive.read("xl/styles.xml"))

        self.assertIn("[Content_Types].xml", names)
        self.assertIn("Dental LinkedIn-Enriched TEMP Profiles", sheet)
        self.assertIn("anne@example.org", sheet)
        self.assertIn("Patient care; Leadership", sheet)
        self.assertIn('ref="N3"', sheet)
        self.assertNotIn('ref="N4"', sheet)
        self.assertIn("https://www.linkedin.com/in/anne-podraza", relationships)
        self.assertNotIn("linkedin.com.evil.example", relationships)
        self.assertNotIn("<f>", sheet)
        worksheet_children = [node.tag.rsplit("}", 1)[-1] for node in sheet_root]
        self.assertLess(worksheet_children.index("autoFilter"), worksheet_children.index("mergeCells"))
        font_children = [
            [node.tag.rsplit("}", 1)[-1] for node in font]
            for font in styles_root.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}fonts")
        ]
        self.assertEqual(font_children[1], ["b", "sz", "color", "name"])
        self.assertEqual(font_children[2], ["b", "sz", "color", "name"])
        self.assertEqual(font_children[3], ["u", "sz", "color", "name"])


if __name__ == "__main__":
    unittest.main()
