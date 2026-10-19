"""Verify that the enriched CSV preserves the clean source and fills the new fields.

    python3 scripts/verify_enrichment.py
    python3 scripts/verify_enrichment.py --source output/kerala_resorts.csv \
        --enriched output/kerala_resorts_enriched.csv

Exits non-zero if a row, an original value or a required field is missing.
"""

import argparse
import collections
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "output" / "kerala_resorts.csv"
ENRICHED = ROOT / "output" / "kerala_resorts_enriched.csv"

REQUIRED = [
    "environment_primary",
    "environment_tags",
    "environment_confidence",
    "environment_reason",
    "swimming_pool",
    "food_included",
    "campfire",
    "best_for",
    "affordability",
    "affordability_confidence",
    "affordability_reason",
]
AFFORDABILITY_LEVELS = {"low", "medium", "high", "premium"}
POOL_LEVELS = {"yes", "no", "not-sure"}
FOOD_LEVELS = {"included", "excluded", "not-sure"}
CAMPFIRE_LEVELS = {"yes", "no", "not-sure"}
EXPECTED_ADDED = 35
DROPPED = {"search_query"}


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--enriched", default=str(ENRICHED))
    args = ap.parse_args(argv)

    src, src_fields = read(args.source)
    out, out_fields = read(args.enriched)
    expected_src = [field for field in src_fields if field not in DROPPED]
    errors = []

    if len(src) != len(out):
        errors.append(f"row count changed: {len(src)} -> {len(out)}")
    if out_fields[: len(expected_src)] != expected_src:
        errors.append("kept original columns are not first, or their order changed")

    n = min(len(src), len(out))
    for i in range(n):
        for field in expected_src:
            if src[i].get(field, "") != out[i].get(field, ""):
                errors.append(f"row {i + 1} field {field} changed")
                break
        if len(errors) >= 10:
            break

    for field in REQUIRED:
        if field not in out_fields:
            errors.append(f"missing field: {field}")

    added = len(out_fields) - len(expected_src)
    if added != EXPECTED_ADDED:
        errors.append(f"expected {EXPECTED_ADDED} added columns, found {added}")
    banned = [f for f in out_fields if any(x in f.lower() for x in ("price", "rupee", "inr"))]
    if banned:
        errors.append(f"price-like columns remain: {', '.join(banned)}")
    if "amenity_pool" in out_fields:
        errors.append("legacy amenity_pool remains; use swimming_pool")
    if "search_query" in out_fields:
        errors.append("search_query column remains")

    blank_affordability = [r for r in out if not r.get("affordability")]
    invalid_affordability = [
        r for r in out if r.get("affordability") not in AFFORDABILITY_LEVELS
    ]
    blank_pool = [r for r in out if not r.get("swimming_pool")]
    invalid_pool = [r for r in out if r.get("swimming_pool") not in POOL_LEVELS]
    blank_food = [r for r in out if not r.get("food_included")]
    invalid_food = [r for r in out if r.get("food_included") not in FOOD_LEVELS]
    blank_fire = [r for r in out if not r.get("campfire")]
    invalid_fire = [r for r in out if r.get("campfire") not in CAMPFIRE_LEVELS]
    blank_env = sum(1 for r in out if not r.get("environment_primary"))
    if blank_affordability:
        errors.append(f"{len(blank_affordability)} rows without an affordability band")
    if invalid_affordability:
        errors.append(f"{len(invalid_affordability)} rows with an invalid affordability band")
    if blank_pool:
        errors.append(f"{len(blank_pool)} rows without a swimming-pool status")
    if invalid_pool:
        errors.append(f"{len(invalid_pool)} rows with an invalid swimming-pool status")
    if blank_food:
        errors.append(f"{len(blank_food)} rows without a food-included status")
    if invalid_food:
        errors.append(f"{len(invalid_food)} rows with an invalid food-included status")
    if blank_fire:
        errors.append(f"{len(blank_fire)} rows without a campfire status")
    if invalid_fire:
        errors.append(f"{len(invalid_fire)} rows with an invalid campfire status")
    if blank_env:
        errors.append(f"{blank_env} rows without an environment label")

    print(f"source   {len(src):,} rows, {len(src_fields)} columns")
    print(f"enriched {len(out):,} rows, {len(out_fields)} columns")
    print(f"added    {added} columns")
    print("affordability:", dict(collections.Counter(r.get("affordability") for r in out)))
    print("affordability confidence:", dict(collections.Counter(r.get("affordability_confidence") for r in out)))
    print("swimming pool:", dict(collections.Counter(r.get("swimming_pool") for r in out)))
    print("food included:", dict(collections.Counter(r.get("food_included") for r in out)))
    print("campfire:", dict(collections.Counter(r.get("campfire") for r in out)))
    print("environment:", dict(collections.Counter(r.get("environment_primary") for r in out)))

    if errors:
        print("\nFAIL")
        for e in errors:
            print(" -", e)
        return 1
    print("\nPASS: rows, original values and required fields are intact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
