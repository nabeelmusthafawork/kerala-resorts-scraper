"""Write a compact markdown report from the enriched Kerala CSV.

    python3 scripts/enrichment_report.py
    python3 scripts/enrichment_report.py --csv output/kerala_resorts_enriched.csv

The report is descriptive only. It does not re-crawl anything.
"""

import argparse
import collections
import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "output" / "kerala_resorts_enriched.csv"
DEFAULT_OUT = ROOT / "output" / "ENRICHMENT_REPORT.md"


def pct(n, total):
    return f"{100.0 * n / total:.0f}%" if total else "0%"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("no rows", file=sys.stderr)
        return 1
    fields = list(rows[0])
    total = len(rows)

    env = collections.Counter(r["environment_primary"] for r in rows)
    env_conf = collections.Counter(r["environment_confidence"] for r in rows)
    afford = collections.Counter(r["affordability"] for r in rows)
    afford_conf = collections.Counter(r["affordability_confidence"] for r in rows)
    pool = collections.Counter(r.get("swimming_pool") or "missing" for r in rows)
    food = collections.Counter(r.get("food_included") or "missing" for r in rows)
    campfire = collections.Counter(r.get("campfire") or "missing" for r in rows)
    best = collections.Counter()
    for r in rows:
        for tag in (r.get("best_for") or "").split(";"):
            if tag:
                best[tag] += 1

    amenity_cols = [
        ("spa", "amenity_spa"),
        ("restaurant", "amenity_restaurant"),
        ("wifi", "amenity_wifi"),
        ("parking", "amenity_parking"),
        ("air conditioning", "amenity_ac"),
        ("breakfast", "amenity_breakfast"),
    ]
    amenity_yes = {label: sum(1 for r in rows if r.get(col) == "yes") for label, col in amenity_cols}
    access = {
        "airport": sum(1 for r in rows if r.get("nearest_airport")),
        "railway station": sum(1 for r in rows if r.get("nearest_railway_station")),
        "town": sum(1 for r in rows if r.get("nearest_town")),
        "coast distance": sum(1 for r in rows if r.get("distance_to_coast_km") not in ("", None)),
        "lake": sum(1 for r in rows if r.get("nearest_lake")),
        "beach": sum(1 for r in rows if r.get("nearest_beach")),
    }

    by_title = collections.defaultdict(collections.Counter)
    for r in rows:
        by_title[r.get("title") or "Unknown"][r.get("affordability") or "unknown"] += 1

    lines = [
        "# Kerala resorts: enrichment report",
        "",
        f"Source rows: **{total:,}**. Columns: **{len(fields)}** "
        f"(13 original + {len(fields) - 13} enrichment fields; "
        f"`search_query` removed).",
        "",
        "## Method",
        "",
        "- Setting: primary label plus up to four tags, inferred from OSM Kerala "
        "extract (coast, beaches, lakes, backwaters, rivers, protected areas, "
        "islands, towns, airports, railways), SRTM elevation, listing text and "
        "the resort's own website copy.",
        "- Amenities: site and listing text. For rows without a usable own site, "
        "a second pass reads the Google Maps place attribute table for the same "
        "listing. `unknown` means not found, not absent. `swimming_pool` is a "
        "separate yes / no / not-sure field; `no` means no pool was found in the "
        "inspected sources, not a verified absence.",
        "- Food and campfire: explicit listing/site wording only. A restaurant or "
        "breakfast amenity alone does not prove food is included. `campfire` is "
        "yes / no / not-sure; `food_included` is included / excluded / not-sure.",
        "- Affordability: one of `low`, `medium`, `high`, `premium`. A calibrated "
        "internal score uses lodging category, district, setting, rating, reviews "
        "and amenities; quality rules moderate the band. No nightly rate or "
        "currency value is written.",
        "- Website crawls honour `robots.txt`. Setting and access use OSM plus "
        "OpenTopoData; rows without a usable own website use Google Maps place "
        "attributes from the same listing. No paid APIs and no OTA scraping.",
        "",
        "## Coverage",
        "",
        f"- Rows with an affordability band: **{sum(afford.values()):,} / {total:,}**",
        f"- Environment confidence: "
        + ", ".join(f"{k} {v:,}" for k, v in env_conf.most_common()),
        f"- Affordability confidence: "
        + ", ".join(f"{k} {v:,}" for k, v in afford_conf.most_common()),
        "",
        "| travel access | rows with a value |",
        "|---|---:|",
    ]
    for label, n in access.items():
        lines.append(f"| {label} | {n:,} ({pct(n, total)}) |")

    lines += [
        "",
        "## Setting",
        "",
        "| primary environment | rows |",
        "|---|---:|",
    ]
    for label, n in env.most_common():
        lines.append(f"| {label} | {n:,} |")

    lines += [
        "",
        "## Amenities found",
        "",
        "| amenity | rows with `yes` |",
        "|---|---:|",
    ]
    for label, n in amenity_yes.items():
        lines.append(f"| {label} | {n:,} ({pct(n, total)}) |")

    lines += [
        "",
        "## Swimming pool",
        "",
        "| status | rows | share |",
        "|---|---:|---:|",
    ]
    for label in ("yes", "no", "not-sure"):
        n = pool.get(label, 0)
        lines.append(f"| {label} | {n:,} | {pct(n, total)} |")

    lines += [
        "",
        "## Food included",
        "",
        "| status | rows | share |",
        "|---|---:|---:|",
    ]
    for label in ("included", "excluded", "not-sure"):
        n = food.get(label, 0)
        lines.append(f"| {label} | {n:,} | {pct(n, total)} |")

    lines += [
        "",
        "## Campfire",
        "",
        "| status | rows | share |",
        "|---|---:|---:|",
    ]
    for label in ("yes", "no", "not-sure"):
        n = campfire.get(label, 0)
        lines.append(f"| {label} | {n:,} | {pct(n, total)} |")

    lines += [
        "",
        "## Affordability",
        "",
        "| band | rows | share |",
        "|---|---:|---:|",
    ]
    for label in ("low", "medium", "high", "premium"):
        n = afford.get(label, 0)
        lines.append(f"| {label} | {n:,} | {pct(n, total)} |")

    lines += [
        "",
        "| category | rows | low | medium | high | premium |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for title, counts in sorted(by_title.items(), key=lambda kv: -sum(kv[1].values()))[:12]:
        lines.append(
            f"| {title} | {sum(counts.values()):,} | "
            + " | ".join(f"{counts.get(level, 0):,}" for level in ("low", "medium", "high", "premium"))
            + " |"
        )

    lines += [
        "",
        "## Best for",
        "",
        "| traveller type | rows |",
        "|---|---:|",
    ]
    for label, n in best.most_common(10):
        lines.append(f"| {label} | {n:,} |")

    lines += [
        "",
        "## Caveats",
        "",
        "- `district` comes from the Google search query. Some rows sit in a "
        "neighbouring district's coordinates; the setting fields use coordinates, "
        "so a row can legitimately say Alappuzha and mountain.",
        "- Affordability is a modelled band, not a booking quote, availability "
        "check or a claim about the exact rate. It is intended for comparison "
        "within this dataset.",
        "- OSM coverage is incomplete in places; `unknown` amenities and blank "
        "distance fields mean no nearby feature was found in the extract.",
        "",
        "## Files",
        "",
        f"- `{args.csv}` — enriched rows, original 14 columns first",
        "- `output/enrichment/site_facts.jsonl` — append-only website crawl cache",
        "- `output/enrichment/detail_facts.jsonl` — Google Maps amenity cache for no-site rows",
        "- `output/enrichment/osm_context.json` — derived OSM context",
        "- `output/enrichment/elevation.json` — SRTM elevation cache",
        "",
    ]

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out} ({total:,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
