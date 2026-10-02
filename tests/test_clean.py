"""Tests for the export-time filters. Offline only."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from resorts_scraper.clean import (  # noqa: E402
    address_in_kerala,
    filter_places,
    format_report,
    in_kerala,
    is_lodging,
    is_own_site,
)
from resorts_scraper.models import Place  # noqa: E402


def P(**kw):
    kw.setdefault("feature_id", "0x1:0x1")
    kw.setdefault("name", "R")
    kw.setdefault("title", "Resort hotel")
    kw.setdefault("latitude", 11.5)
    kw.setdefault("longitude", 76.2)
    return Place(**kw)


class TestGeoFilter(unittest.TestCase):
    def test_inside_kerala(self):
        self.assertTrue(in_kerala(P(latitude=10.85, longitude=76.27)))  # Wayanad
        self.assertTrue(in_kerala(P(latitude=8.9, longitude=77.0)))     # Kollam edge

    def test_outside_kerala_dropped(self):
        self.assertFalse(in_kerala(P(latitude=12.97, longitude=77.5)))  # Bengaluru
        self.assertFalse(in_kerala(P(latitude=20.3, longitude=75.1)))   # Maharashtra
        self.assertFalse(in_kerala(P(latitude=39.7, longitude=45.2)))   # Armenia
        self.assertFalse(in_kerala(P(latitude=13.19, longitude=77.7)))  # just past edge

    def test_missing_coords_dropped(self):
        self.assertFalse(in_kerala(P(latitude=None, longitude=None)))


class TestStateFilter(unittest.TestCase):
    def test_kerala_addresses_pass(self):
        self.assertTrue(address_in_kerala(P(address="Kalpetta, Wayanad, Kerala 673121")))
        self.assertTrue(address_in_kerala(P(address="Kozhikode, Keralam 673001")))
        self.assertTrue(address_in_kerala(P(address="")))  # nothing to judge

    def test_other_states_rejected_even_inside_box(self):
        self.assertFalse(address_in_kerala(P(address="Madikeri, Karnataka 571201")))
        self.assertFalse(address_in_kerala(P(address="Mahé, Puducherry 673310")))
        self.assertFalse(address_in_kerala(P(address="Bengaluru, Karnataka 560116")))

    def test_kerala_wins_when_both_named(self):
        self.assertFalse(address_in_kerala(P(address="Kerala, near Madikeri, Karnataka")))


class TestCategoryFilter(unittest.TestCase):
    def test_lodging_kept(self):
        for t in ("Resort hotel", "Home Stay", "Hotel", "Villa", "Farmstay", "Lodge"):
            self.assertTrue(is_lodging(P(title=t)), t)

    def test_non_lodging_dropped(self):
        for t in ("Restaurant", "Amusement park", "Swimming pool", "Auditorium",
                  "Corporate office", "Wedding venue", "Chicken & Mutton Shop", ""):
            self.assertFalse(is_lodging(P(title=t)), t)

    def test_case_insensitive(self):
        self.assertTrue(is_lodging(P(title="resort HOTEL")))


class TestWebsiteFilter(unittest.TestCase):
    def test_own_site_and_socials(self):
        self.assertTrue(is_own_site(P(website="https://resort.example")))
        for u in ("https://instagram.com/x", "https://www.booking.com/hotel/y",
                  "https://www.google.com/maps", ""):
            self.assertFalse(is_own_site(P(website=u)), u)


class TestFilterPlaces(unittest.TestCase):
    def test_counts_every_bucket(self):
        places = [
            P(feature_id="a", name="Keep", address="Kalpetta, Kerala"),
            P(feature_id="b", name="OutOfBox", latitude=12.97, longitude=77.5),
            P(feature_id="c", name="OtherState", address="Madikeri, Karnataka"),
            P(feature_id="d", name="NotLodging", title="Restaurant"),
            P(feature_id="e", latitude=None, longitude=None),
        ]
        kept, rep = filter_places(places)
        self.assertEqual([p.name for p in kept], ["Keep"])
        self.assertEqual(rep["total"], 5)
        self.assertEqual(rep["kept"], 1)
        self.assertEqual(rep["drop_outside_state_geo"], 1)
        self.assertEqual(rep["drop_other_state_address"], 1)
        self.assertEqual(rep["drop_not_lodging"], 1)
        self.assertEqual(rep["drop_no_coords"], 1)

    def test_duplicates_within_2km_collapse_far_apart_survive(self):
        places = [
            P(feature_id="a", name="Bombo Resort", latitude=11.5, longitude=76.2),
            P(feature_id="b", name="bombo  resort!", latitude=11.501, longitude=76.201),
            P(feature_id="c", name="Bombo Resort", latitude=11.9, longitude=76.2),
        ]
        kept, rep = filter_places(places)
        self.assertEqual(len(kept), 2)
        self.assertEqual(rep["drop_duplicate"], 1)

    def test_blank_name_not_collapsed_by_name_but_by_coords(self):
        """Blank names never hit the name key; identical coordinates still do."""
        places = [P(feature_id="a", name="", latitude=11.5), P(feature_id="b", name="", latitude=11.6)]
        kept, rep = filter_places(places)
        self.assertEqual(len(kept), 2)
        self.assertEqual(rep["drop_duplicate"], 0)
        kept, rep = filter_places([P(feature_id="a", name=""), P(feature_id="b", name="")])
        self.assertEqual(len(kept), 1)  # same exact coords = same place

    def test_same_coords_different_names_collapsed(self):
        places = [P(feature_id="a", name="Fancy Resort"), P(feature_id="b", name="Resort Fancy")]
        kept, rep = filter_places(places)
        self.assertEqual(len(kept), 1)
        self.assertEqual(rep["drop_duplicate"], 1)

    def test_strict_drops_rows_missing_required_fields(self):
        places = [P(feature_id="a", name="HasPhone", phone="+91 1", latitude=11.5),
                  P(feature_id="b", name="NoPhone", phone="", latitude=11.6),
                  P(feature_id="c", name="NoTitle", title="", latitude=11.7)]
        kept, rep = filter_places(places, strict=True)
        self.assertEqual([p.name for p in kept], ["HasPhone"])
        # NoPhone goes to missing_required; NoTitle has no category either, so
        # it is already caught by the non-lodging filter before strict applies
        self.assertEqual(rep["drop_missing_required"], 1)
        self.assertEqual(rep["drop_not_lodging"], 1)
        # non-strict keeps rows that still have a category, flagged
        kept2, rep2 = filter_places(places, strict=False)
        self.assertEqual([p.name for p in kept2], ["HasPhone", "NoPhone"])
        self.assertEqual(rep2["flag_missing_phone"], 1)

    def test_flags_do_not_drop_rows(self):
        places = [P(website="https://instagram.com/x", phone="", email="", description="")]
        kept, rep = filter_places(places)
        self.assertEqual(len(kept), 1)
        self.assertEqual(rep["flag_social_site"], 1)
        self.assertEqual(rep["flag_missing_phone"], 1)
        self.assertEqual(rep["flag_missing_email"], 1)
        self.assertEqual(rep["flag_missing_description"], 1)

    def test_report_renders_without_KeyError(self):
        _, rep = filter_places([P()])
        out = format_report(rep)
        self.assertIn("Kept 1 of 1", out)
        self.assertIn("outside Kerala bounding box", out)


if __name__ == "__main__":
    unittest.main()