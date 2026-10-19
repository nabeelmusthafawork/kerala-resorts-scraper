#!/usr/bin/env python3
"""Build the full-record site dataset from the enriched CSV.

The editorial shortlist in docs/data/resorts.json holds the top 100. This script
writes docs/data/resorts-all.json with every row (7,813), slimmed to the fields
the filters and cards need, so the site can filter the whole record without
pulling the 48-column table into the browser.

Schema matches the shortlist stay object, minus the static image fields, which
are resolved live. Records are ranked by the same Bayesian score as the
shortlist: (50 * mean + n * rating) / (50 + n).
"""

from __future__ import annotations

import collections
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_site_data import (  # noqa: E402
    PRIOR_REVIEWS,
    SETTING_LABELS,
    TYPE_ORDER,
    as_float,
    as_int,
    clean_text,
    property_type,
    slugify,
    why_lines,
)

DEFAULT_CSV = ROOT / "output" / "kerala_resorts_enriched.csv"
OUT = ROOT / "docs" / "data" / "resorts-all.json"


def main() -> int:
    with open(DEFAULT_CSV, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    rated = [r for r in rows if 0 < as_float(r.get("rating")) <= 5]
    mean_rating = sum(as_float(r["rating"]) for r in rated) / len(rated)

    for row in rows:
        n = max(as_int(row.get("review_count")), 0)
        rating = as_float(row.get("rating"))
        row["_score"] = (PRIOR_REVIEWS * mean_rating + n * rating) / (PRIOR_REVIEWS + n) if rating > 0 else 0
        row["_reviews"] = n

    rows.sort(key=lambda r: (-r["_score"], -r["_reviews"], (r.get("name") or "").lower()))

    used_slugs: collections.Counter = collections.Counter()
    stays = []
    for rank, row in enumerate(rows, 1):
        name = clean_text(row.get("name"))
        district = clean_text(row.get("district"))
        town = clean_text(row.get("nearest_town")) or district
        setting = row.get("environment_primary") or "countryside"
        setting_label = SETTING_LABELS.get(setting, setting.title())

        base_slug = slugify(f"{name}-{town}") or f"stay-{rank}"
        used_slugs[base_slug] += 1
        slug = base_slug if used_slugs[base_slug] == 1 else f"{base_slug}-{used_slugs[base_slug]}"

        stays.append({
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
            "reviews": row["_reviews"],
            "score": round(float(row["_score"]), 3),
            "elevation": as_int(row.get("elevation_m")),
            "affordability": row.get("affordability") or "medium",
            "pool": row.get("swimming_pool") or "not-sure",
            "food": row.get("food_included") or "not-sure",
            "campfire": row.get("campfire") or "not-sure",
            "best_for": [t for t in (row.get("best_for") or "").split(";") if t],
            "website": row.get("website") or "",
            "maps_url": "https://www.google.com/maps/place/?q=place_id:"
            + (row.get("place_id") or ""),
            "lat": round(as_float(row.get("latitude")), 6),
            "lon": round(as_float(row.get("longitude")), 6),
            "why": why_lines(row, setting_label, town),
            "image_key": slug,
            "place_id": row.get("place_id") or "",
        })

    meta = {
        "title": "Kerala Field Notes",
        "subtitle": "Every rated Kerala stay",
        "generated_at": __import__("datetime").date.today().isoformat(),
        "source_rows": len(rows),
        "rated_rows": len(rated),
        "shortlist": len(stays),
        "districts": len({s["district"] for s in stays if s["district"]}),
        "mean_rating": round(mean_rating, 2),
        "min_reviews": 0,
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
    }

    payload = json.dumps({"meta": meta, "stays": stays}, separators=(",", ":"), ensure_ascii=False)
    OUT.write_text(payload, encoding="utf-8")

    print(f"wrote {OUT} ({len(payload) / 1e6:.2f} MB raw)")
    print(f"{len(stays)} stays, {meta['districts']} districts")
    print("types:", collections.Counter(s["type"] for s in stays).most_common())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
