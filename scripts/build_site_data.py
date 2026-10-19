"""Build the static site dataset from the enriched Kerala CSV.

The shortlist is ranked with a Bayesian rating score: a 4.9 from 7,000
reviews outranks a 5.0 from eight. Coordinates are reverse-geocoded once
through Nominatim and cached, because the source `district` column came from
the search query and is wrong for a small number of border rows.

    python3 scripts/build_site_data.py
    python3 scripts/build_site_data.py --limit 100 --no-geocode
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import math
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "output" / "kerala_resorts_enriched.csv"
SITE_DATA = ROOT / "docs" / "data"
IMAGE_MANIFEST = ROOT / "docs" / "assets" / "locations" / "manifest.json"
GEOCODE_CACHE = ROOT / "docs" / "data" / "geocode_cache.json"
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = (
    "kerala-field-notes/1.0 "
    "(https://github.com/nabeelmusthafawork/kerala-resorts-scraper; research map)"
)
PRIOR_REVIEWS = 50.0

SETTING_LABELS = {
    "beach": "Beach",
    "backwater": "Backwater",
    "mountain": "Mountain",
    "hill": "Hill",
    "forest": "Forest",
    "lake": "Lake",
    "river": "River",
    "urban": "Town",
    "countryside": "Countryside",
    "plantation": "Plantation",
    "island": "Island",
    "waterfall": "Waterfall",
    "cliff": "Cliff",
}
TYPE_ORDER = ("Resort", "Hotel", "Homestay", "Villa", "Lodge", "Other")


def as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "place"


def property_type(title: str, name: str) -> str:
    text = f"{title} {name}".lower()
    if "resort" in text or "retreat" in text:
        return "Resort"
    if "homestay" in text or "home stay" in text or "guest house" in text:
        return "Homestay"
    if "villa" in text or "farmstay" in text or "farm house" in text:
        return "Villa"
    if "hotel" in text or "inn" in text:
        return "Hotel"
    if "lodge" in text:
        return "Lodge"
    return "Other"


def clean_text(value: str, limit: int = 0) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit].rstrip() if limit else value


def load_geocode_cache() -> dict:
    if GEOCODE_CACHE.exists():
        try:
            return json.loads(GEOCODE_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_json(path: pathlib.Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def reverse_geocode(lat: float, lon: float, cache: dict, use_network: bool) -> dict:
    key = f"{lat:.5f},{lon:.5f}"
    cached = cache.get(key)
    if cached:
        return cached
    if not use_network:
        return {}

    params = {
        "format": "jsonv2",
        "lat": f"{lat:.7f}",
        "lon": f"{lon:.7f}",
        "zoom": "10",
        "addressdetails": "1",
        "accept-language": "en",
    }
    url = NOMINATIM + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.load(response)
            address = data.get("address") or {}
            record = {
                "district": address.get("state_district") or address.get("county") or "",
                "town": (
                    address.get("town")
                    or address.get("city")
                    or address.get("municipality")
                    or address.get("village")
                    or address.get("suburb")
                    or address.get("county")
                    or ""
                ),
                "display": data.get("display_name") or "",
            }
            cache[key] = record
            time.sleep(1.05)
            return record
        except Exception as exc:  # noqa: BLE001 - a cache miss must not kill the build
            if attempt == 2:
                print(f"geocode failed {key}: {exc}", file=sys.stderr)
                return {}
            time.sleep(2.0 + attempt * 2.0)
    return {}


def load_manifest() -> dict:
    if not IMAGE_MANIFEST.exists():
        return {}
    try:
        return json.loads(IMAGE_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def why_lines(row: dict, setting_label: str, town: str) -> list[str]:
    lines: list[str] = []
    lines.append(f"{setting_label} setting near {town}")
    if row.get("swimming_pool") == "yes":
        lines.append("Pool listed")
    if row.get("campfire") == "yes":
        lines.append("Bonfire listed")
    if row.get("food_included") == "included":
        lines.append("Breakfast or meals included")
    if row.get("amenity_spa") == "yes":
        lines.append("Spa on site")
    if row.get("amenity_restaurant") == "yes":
        lines.append("Restaurant on site")
    return lines[:4]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--no-geocode", action="store_true")
    parser.add_argument("--min-reviews", type=int, default=30)
    parser.add_argument("--no-images", action="store_true")
    args = parser.parse_args(argv)

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        all_rows = list(csv.DictReader(f))
    rows = [r for r in all_rows if 0 < as_float(r.get("rating")) <= 5]

    if not rows:
        print("no rated rows", file=sys.stderr)
        return 1

    mean_rating = sum(as_float(r["rating"]) for r in rows) / len(rows)
    for row in rows:
        n = max(as_int(row.get("review_count")), 0)
        rating = as_float(row.get("rating"))
        row["_score"] = (PRIOR_REVIEWS * mean_rating + n * rating) / (PRIOR_REVIEWS + n)
        row["_reviews"] = n

    candidates = [r for r in rows if r["_reviews"] >= args.min_reviews]
    candidates.sort(key=lambda r: (-r["_score"], -r["_reviews"], (r.get("name") or "").lower()))
    shortlist = candidates[: args.limit]

    cache = load_geocode_cache()
    manifest = {} if args.no_images else load_manifest().get("images", {})
    used_slugs: collections.Counter = collections.Counter()
    stays = []
    image_keys = set()

    for rank, row in enumerate(shortlist, 1):
        lat = as_float(row.get("latitude"))
        lon = as_float(row.get("longitude"))
        geo = reverse_geocode(lat, lon, cache, not args.no_geocode)
        district = clean_text(geo.get("district")) or clean_text(row.get("district"))
        # OSM's nearest_town keeps the familiar local name (Kumily, Munnar,
        # Varkala); Nominatim's town field often returns the taluk instead.
        town = clean_text(row.get("nearest_town")) or clean_text(geo.get("town"))
        setting = row.get("environment_primary") or "countryside"
        setting_label = SETTING_LABELS.get(setting, setting.title())
        name = clean_text(row.get("name"))
        base_slug = slugify(f"{name}-{town or district}")
        used_slugs[base_slug] += 1
        slug = base_slug if used_slugs[base_slug] == 1 else f"{base_slug}-{used_slugs[base_slug]}"

        image_key = slugify(f"{town}-{district}-{setting}") or slug
        image_keys.add(image_key)
        image = manifest.get(image_key) or {}
        image_file = image.get("file") or ""
        if image_file and not (ROOT / "docs" / "assets" / "locations" / image_file).exists():
            image_file = ""

        stay = {
            "rank": rank,
            "slug": slug,
            "name": name,
            "type": property_type(row.get("title", ""), name),
            "district": district,
            "town": town,
            "setting": setting,
            "setting_label": setting_label,
            "tags": [t for t in (row.get("environment_tags") or "").split(";") if t],
            "rating": round(as_float(row.get("rating")), 2),
            "reviews": as_int(row.get("review_count")),
            "score": round(float(row["_score"]), 3),
            "elevation": as_int(row.get("elevation_m")),
            "affordability": row.get("affordability") or "medium",
            "pool": row.get("swimming_pool") or "not-sure",
            "food": row.get("food_included") or "not-sure",
            "campfire": row.get("campfire") or "not-sure",
            "best_for": [t for t in (row.get("best_for") or "").split(";") if t],
            "website": row.get("website") or "",
            "maps_url": (
                "https://www.google.com/maps/place/?q=place_id:"
                + urllib.parse.quote(row.get("place_id") or "")
            ),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "why": why_lines(row, setting_label, town),
            "image_key": image_key,
            "image": f"assets/locations/{image_file}" if image_file else "",
            "image_alt": image.get("alt") or f"{setting_label} landscape near {town}, Kerala",
            "image_title": image.get("title") or "",
            "image_author": image.get("author") or "",
            "image_license": image.get("license") or "",
            "image_license_url": image.get("license_url") or "",
            "image_page": image.get("page") or "",
        }
        stays.append(stay)

    meta = {
        "title": "Kerala Field Notes",
        "subtitle": "An evidence-led shortlist of Kerala stays",
        "generated_at": dt.date.today().isoformat(),
        "source_rows": len(all_rows),
        "rated_rows": len(rows),
        "shortlist": len(stays),
        "districts": len({s["district"] for s in stays if s["district"]}),
        "mean_rating": round(mean_rating, 2),
        "min_reviews": args.min_reviews,
        "prior_reviews": PRIOR_REVIEWS,
        "settings": [
            {"key": key, "label": label, "count": sum(1 for s in stays if s["setting"] == key)}
            for key, label in SETTING_LABELS.items()
            if any(s["setting"] == key for s in stays)
        ],
        "types": [
            {"key": key, "count": sum(1 for s in stays if s["type"] == key)}
            for key in TYPE_ORDER
            if any(s["type"] == key for s in stays)
        ],
        "affordability": [
            {"key": key, "count": sum(1 for s in stays if s["affordability"] == key)}
            for key in ("low", "medium", "high", "premium")
        ],
        "image_coverage": sum(1 for s in stays if s["image"]),
        "missing_images": len(image_keys) - sum(1 for s in stays if s["image"]),
        "credits": "Location photography from Wikimedia Commons. Full credits in the image manifest.",
    }

    save_json(GEOCODE_CACHE, cache)
    save_json(SITE_DATA / "resorts.json", {"meta": meta, "stays": stays})
    save_json(SITE_DATA / "image_keys.json", sorted(image_keys))

    print(f"wrote {SITE_DATA / 'resorts.json'}")
    print(f"{len(stays)} stays, {meta['districts']} districts, {meta['image_coverage']} images")
    print("settings:", collections.Counter(s["setting"] for s in stays).most_common())
    print("types:", collections.Counter(s["type"] for s in stays).most_common())
    print("affordability:", collections.Counter(s["affordability"] for s in stays).most_common())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
