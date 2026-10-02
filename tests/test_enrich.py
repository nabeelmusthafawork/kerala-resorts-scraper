"""Tests for description extraction and the email harvester. Offline only."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from resorts_scraper.email import EmailCrawler, find_emails  # noqa: E402
from resorts_scraper.models import CSV_FIELDS, Place  # noqa: E402
from resorts_scraper.search import _sentences, parse_search  # noqa: E402
from resorts_scraper.store import Store  # noqa: E402

FID = "0x3ba67332963ac41b:0x986411f359512b47"

# Real tbm=map shape, including the description block at fid+22.
ENTRY_WITH_DESC = [
    "tok", "tok", ["J34M+FH5", "near New Bus Terminal", "Kalpetta, Keralam 673121"], None,
    [None, None, None, [None, "2,653 reviews", None], None, None, None, 4.4, 2653],
    None, None,
    ["https://hotel.example/", "hotel.example", None], None,
    [None, None, 11.6061487, 76.0839972],
    FID, "HOTEL INDRIYA", None, ["Hotel"], "Mandayapuram",
    None, None, None, None, None, None, None,
    None, None, None,
    [
        [None, "Modern hotel with free WiFi & breakfast", None, None, None, 1],
        [None, "Upscale property with free WiFi, breakfast & parking, plus an outdoor pool & an Indian restaurant.", None, None, None, 1],
    ],
]


class TestDescription(unittest.TestCase):
    def test_extracts_editorial_prose(self):
        """Only the full prose sentence qualifies: the short tagline carries no
        trailing period, and requiring one keeps addresses/categories out."""
        [p] = parse_search(")]}'\n" + __import__("json").dumps([[ENTRY_WITH_DESC]]))
        self.assertIn("Upscale property with free WiFi", p["description"])
        self.assertNotIn("Modern hotel", p["description"])

    def test_no_description_block_yields_empty(self):
        bare = [x for x in ENTRY_WITH_DESC if not (isinstance(x, list) and x and isinstance(x[0], list))]
        out = parse_search(")]}'\n" + __import__("json").dumps([[bare]]))
        self.assertEqual(out[0]["description"], "")

    def test_sentences_ignores_short_and_non_prose(self):
        self.assertEqual(_sentences(["Kalpetta", "Keralam 673121", None]), [])
        self.assertEqual(_sentences([]), [])

    def test_sentences_reads_two_levels_down(self):
        """Real shape: [ [ null, ... ] , [ null, ..., [ "s1", "s2" ] ] ]"""
        block = [[None, None, None, [None, None, ["A real sentence that is long enough to count.", "Another real sentence that is long enough as well."]], None]]
        self.assertEqual(len(_sentences(block)), 2)


class TestEmailParsing(unittest.TestCase):
    def test_plain_and_obfuscated(self):
        for html in (
            "<a href='mailto:info@hotel.in'>mail</a>",
            "<p>info (at) hotel.in</p>",
            "<p>info [at] hotel.in</p>",
            "<p>info &#64; hotel.in</p>",
        ):
            self.assertIn("info@hotel.in", find_emails(html, "hotel.in"), html)

    def test_ignores_third_party_domains(self):
        self.assertEqual(find_emails("tracker@ads.net", "hotel.in"), [])

    def test_keeps_mailto_cross_domain(self):
        self.assertIn("sales@group.co", find_emails("write <mailto:sales@group.co>", "hotel.in"))

    def test_blocks_placeholders_and_images(self):
        junk = "a@b.com src=\"/logo.png\" alt=\"c@d.com\""
        self.assertEqual(find_emails(junk, "hotel.in"), [])

    def test_accepts_subdomain_of_base(self):
        self.assertIn("info@mail.hotel.in", find_emails("info@mail.hotel.in", "hotel.in"))


class TestEmailPick(unittest.TestCase):
    def test_ignores_non_contact_links(self):
        self.assertEqual(EmailCrawler._contact_links("<a href='/x'>x</a>", "http://h/"), [])

    def test_finds_contact_links_capped_at_two(self):
        html = "".join(f"<a href='/{n}'>c</a>" for n in ("contact", "contact-us", "reach-us", "get-in-touch"))
        links = EmailCrawler._contact_links(html, "https://hotel.in/")
        self.assertEqual(len(links), 2)
        self.assertTrue(all(l.startswith("https://hotel.in/") for l in links))


class TestEmailStore(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp()

    def test_only_websiteless_or_uncrawled_places_queued(self):
        s = Store(self.dir)
        s.save_places([
            Place(feature_id="0x1:0x1", name="NoSite"),
            Place(feature_id="0x2:0x2", name="Fresh", website="https://a.in"),
            Place(feature_id="0x3:0x3", name="Done", website="https://b.in", email="x@b.in"),
            Place(feature_id="0x4:0x4", name="Tried", website="https://c.in", email_tries=1),
        ])
        self.assertEqual([p.name for p in s.missing_emails()], ["Fresh"])

    def test_email_survives_reload_and_merge(self):
        Store(self.dir).save_places([Place(feature_id="0x1:0x1", name="P", website="https://a.in")])
        Store(self.dir).save_places([Place(feature_id="0x1:0x1", name="P", website="https://a.in", email="p@a.in", email_tries=1)])
        p = Store(self.dir).places["0x1:0x1"]
        self.assertEqual(p.email, "p@a.in")
        self.assertEqual(p.email_tries, 1)
        self.assertEqual(Store(self.dir).missing_emails(), [])


class TestCsvSchema(unittest.TestCase):
    def test_description_column_present_and_positioned(self):
        self.assertIn("description", CSV_FIELDS)
        row = Place(feature_id=FID, name="N", description="d").row()
        self.assertEqual(list(row), CSV_FIELDS)
        self.assertEqual(row["description"], "d")


if __name__ == "__main__":
    unittest.main()
