import unittest
from pathlib import Path


PAGES = Path(__file__).resolve().parents[1] / "ui" / "pages"


class VoyagerBrandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header = (PAGES / "components" / "searchBar.html").read_text(encoding="utf-8")

    def test_shared_header_places_voyager_brand_before_profile_search(self):
        brand = self.header.index('class="voyager-brand"')
        search = self.header.index('class="search-container"', brand)

        self.assertLess(brand, search)
        self.assertIn('aria-label="Voyager"', self.header)
        self.assertIn('<span class="voyager-brand-name">Voyager</span>', self.header)

    def test_symbol_is_inline_and_uses_each_domains_theme(self):
        self.assertIn('class="voyager-brand-mark"', self.header)
        self.assertIn('<svg viewBox="0 0 32 32"', self.header)
        self.assertIn('var(--primary-2, #245c3b)', self.header)
        self.assertIn('var(--primary-rgb, 47, 125, 75)', self.header)

    def test_wordmark_only_collapses_at_the_smallest_mobile_width(self):
        self.assertIn('@media (max-width: 350px)', self.header)
        self.assertIn(".voyager-brand-name", self.header)


if __name__ == "__main__":
    unittest.main()
