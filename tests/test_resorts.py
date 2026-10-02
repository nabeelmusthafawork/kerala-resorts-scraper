"""Tests for the parsing/planning logic. Run: .venv/bin/python -m unittest discover tests"""

import csv
import json
import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from resorts_scraper.backend import Blocked, ensure_api_payload, force_ipv4, polite_sleep  # noqa: E402
from resorts_scraper.details import parse_details  # noqa: E402
from resorts_scraper.export import coverage_report, export_csv  # noqa: E402
from resorts_scraper.models import CSV_FIELDS, Place  # noqa: E402
from resorts_scraper.planner import CAP_THRESHOLD, Planner  # noqa: E402
from resorts_scraper.search import build_search_url, parse_search  # noqa: E402
from resorts_scraper.store import Store  # noqa: E402

FID = "0x3ba66e936bd4c741:0x2055e48a974347c7"

# Shaped exactly like the real tbm=map per-place array documented in
# docs/HOW_IT_WORKS.md section 2.
PLACE_ENTRY = [
    "token",
    "token",
    ["Puduppadi, Keralam 673586"],
    None,
    [None, None, None, [None, "1,978 reviews", None], None, None, None, 4.4, 1978],
    None,
    None,
    ["https://aachi.example/", "aachi.example", None],
    None,
    [None, None, 11.6123456, 76.0834567],
    FID,
    "iCon Residency",
    None,
    ["Resort hotel", "Hotel", "Resort"],
    None,
    "iCon Residency, Puduppadi, Keralam 673586",
]


def wrap(entries):
    return ")]}'\n\n" + json.dumps([[entries]])


class TestSearchParse(unittest.TestCase):
    def test_extracts_all_fields(self):
        [p] = parse_search(wrap(PLACE_ENTRY))
        self.assertEqual(p["feature_id"], FID)
        self.assertEqual(p["name"], "iCon Residency")
        self.assertEqual(p["title"], "Resort hotel")
        self.assertEqual(p["website"], "https://aachi.example/")
        self.assertAlmostEqual(p["latitude"], 11.6123456)
        self.assertAlmostEqual(p["longitude"], 76.0834567)
        self.assertEqual(p["rating"], "4.4")
        self.assertEqual(p["review_count"], "1978")
        self.assertIn("Puduppadi", p["address"])

    def test_dedupes_identical_feature_ids(self):
        both = ")]}'\n\n" + json.dumps([[PLACE_ENTRY, PLACE_ENTRY]])
        self.assertEqual(len(parse_search(both)), 1)

    def test_skips_google_hosted_websites(self):
        e = json.loads(json.dumps(PLACE_ENTRY))
        e[7] = ["https://www.google.com/maps", "google.com", None]
        [p] = parse_search(wrap(e))
        self.assertEqual(p["website"], "")

    def test_no_feature_id_returns_nothing(self):
        self.assertEqual(parse_search(wrap(["just", "strings", None])), [])

    def test_malformed_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_search(")]}'\n\n{not json")

    def test_count_capped_at_60(self):
        self.assertIn("7i60", urllib.parse.unquote(build_search_url("resorts in Kalpetta", count=500)))


class TestDetailsParse(unittest.TestCase):
    def test_phone_website_coords_address(self):
        body = (
            f'"{FID}","iCon Residency",'
            '["+91 9747 1234 56"],'
            '"https://i.example.com/x",  "i.example.com",'
            "[null, null, 11.612, 76.083],"
            '"iCon Residency, Puduppadi, Keralam 673586",'
            "4.4, 1978]"
        )
        d = parse_details(body, FID)
        self.assertEqual(d["phone"], "+91 9747 1234 56")
        self.assertEqual(d["website"], "https://i.example.com/x")
        self.assertAlmostEqual(d["latitude"], 11.612)
        self.assertIn("Puduppadi", d["address"])
        self.assertEqual(d["rating"], "4.4")

    def test_google_website_ignored(self):
        body = f'"{FID}","N", "https://www.google.com/", "google.com"'
        self.assertEqual(parse_details(body, FID).get("website"), None)

    def test_minimal_payload_returns_no_phone(self):
        self.assertNotIn("phone", parse_details(f'"{FID}","Quiet Place"', FID))


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "d.yaml")
        with open(self.path, "w") as f:
            f.write(
                "districts:\n"
                "  Alpha:\n"
                "    localities: [Town, Village]\n"
                "    subs:\n"
                "      Town: [Village, Hamlet]\n"
                "  Beta:\n"
                "    localities: [Elsewhere]\n"
            )
        self.p = Planner(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_districts_method_is_callable(self):
        """Regression: instance attr `districts` used to shadow the method."""
        self.assertEqual(sorted(self.p.districts()), ["Alpha", "Beta"])
        self.assertEqual(len(self.p.initial_queries()), 12)  # 3 localities x 4 variants, no TypeError

    def test_four_variants_per_locality(self):
        qs = self.p.initial_queries("Alpha", "Town")
        self.assertEqual(len(qs), 4)
        self.assertIn("resorts in Town", [q.text for q in qs])
        self.assertTrue(all(q.district == "Alpha" and q.locality == "Town" for q in qs))

    def test_all_districts_when_unspecified(self):
        self.assertEqual(len(self.p.initial_queries()), 12)

    def test_no_expansion_below_cap(self):
        [q] = [x for x in self.p.initial_queries("Alpha", "Town") if x.text == "resorts in Town"]
        self.assertEqual(self.p.expansion_for(q, CAP_THRESHOLD - 1), [])

    def test_expands_subs_on_cap(self):
        [q] = [x for x in self.p.initial_queries("Alpha", "Town") if x.text == "resorts in Town"]
        subs = self.p.expansion_for(q, 60)
        self.assertEqual({x.locality for x in subs}, {"Village", "Hamlet"})
        self.assertTrue(all(x.parent == "Town" for x in subs))

    def test_expansion_skips_self_reference(self):
        """A locality listed as its own sub must not re-queue itself."""
        with open(self.path, "a") as f:
            f.write("")
        self.p._districts["Alpha"]["subs"]["Village"] = ["Village", "Town"]
        [q] = [x for x in self.p.initial_queries("Alpha", "Village") if x.text == "resorts in Village"]
        locals_ = {x.locality for x in self.p.expansion_for(q, 60)}
        self.assertNotIn("Village", locals_)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_resume_skips_done_queries(self):
        s = Store(self.dir)
        s.record_query("resorts in X", "D", "X", 60, "cap_hit")
        self.assertTrue(Store(self.dir).query_done("resorts in X"))

    def test_blocked_query_is_retried(self):
        Store(self.dir).record_query("resorts in Y", "D", "Y", 0, "blocked")
        self.assertFalse(Store(self.dir).query_done("resorts in Y"))

    def test_places_dedupe_across_appends(self):
        Store(self.dir).save_places([Place(feature_id=FID, name="Old", website="")])
        self.assertEqual(len(Store(self.dir).places), 1)
        Store(self.dir).save_places([Place(feature_id=FID, name="Old", phone="+91 1")])
        self.assertEqual(len(Store(self.dir).places), 1)
        self.assertEqual(Store(self.dir).places[FID].phone, "+91 1")

    def test_missing_details_respects_tries(self):
        s = Store(self.dir)
        s.save_places([Place(feature_id=FID, name="P", detail_tries=3), Place(feature_id="0x1:0x2", name="Q")])
        self.assertEqual([p.name for p in s.missing_details(max_tries=3)], ["Q"])


class TestModels(unittest.TestCase):
    def test_merge_fills_blanks_only(self):
        a = Place(feature_id=FID, name="Keep", rating="4.4", detail_tries=2)
        a.merge(Place(feature_id=FID, name="Overwrite", rating="1.0", website="w", detail_tries=1))
        self.assertEqual(a.name, "Keep")
        self.assertEqual(a.rating, "4.4")
        self.assertEqual(a.website, "w")
        self.assertEqual(a.detail_tries, 2)

    def test_row_keys_match_csv_schema(self):
        row = Place(feature_id=FID, name="P", latitude=None).row()
        self.assertEqual(list(row), CSV_FIELDS)
        self.assertEqual(row["latitude"], "")
        self.assertEqual(row["place_id"], FID)

    def test_from_dict_ignores_unknown_keys(self):
        self.assertEqual(Place.from_dict({"feature_id": FID, "name": "P", "junk": 1}).name, "P")


class TestExport(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.out = os.path.join(self.dir, "r.csv")

    def test_csv_sorted_by_district_then_name(self):
        places = [
            Place(feature_id=FID, name="Zeta", district="B"),
            Place(feature_id="0x1:0x2", name="Alpha", district="B"),
            Place(feature_id="0x3:0x4", name="Mid", district="A"),
        ]
        self.assertEqual(export_csv(places, self.out), 3)
        with open(self.out, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["name"] for r in rows], ["Mid", "Alpha", "Zeta"])

    def test_coverage_report_counts_and_names_caps(self):
        rep = coverage_report({"a": {"status": "ok"}, "b": {"status": "cap_hit", "query": "b", "count": 60}})
        self.assertIn("cap_hit=1", rep)
        self.assertIn("! b (60 results)", rep)
        self.assertIn("No queries", coverage_report({}))


class TestBlockDetection(unittest.TestCase):
    def test_accepts_xssi_prefix(self):
        self.assertTrue(ensure_api_payload(")]}'\n[[1]]"))

    def test_rejects_captcha(self):
        with self.assertRaises(Blocked):
            ensure_api_payload("<html>please solve the captcha</html>")

    def test_rejects_anything_unexpected(self):
        with self.assertRaises(Blocked):
            ensure_api_payload("<html>hello</html>")

    def test_force_ipv4_pins_dns_once(self):
        import socket

        if getattr(socket, "_resorts_ipv4_pinned", False):
            self.assertFalse(force_ipv4())  # idempotent, never re-patches
            return
        self.assertTrue(force_ipv4())
        host = socket.getaddrinfo("localhost", 80, type=socket.SOCK_STREAM)[0]
        self.assertEqual(host[0], socket.AF_INET)
        self.assertFalse(force_ipv4())

    def test_polite_sleep_stays_in_band(self):
        import time

        t0 = time.time()
        polite_sleep(0.1)
        self.assertGreaterEqual(time.time() - t0, 0.115)


if __name__ == "__main__":
    unittest.main()
