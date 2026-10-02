"""Generate output/FINAL_STATS.md: the numbers behind the report.

    python3 -m resorts_scraper.stats
"""

import collections
import csv
import datetime
import os
import sys

from .clean import filter_places
from .store import Store

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build(store: Store, csv_path: str | None = None) -> str:
    raw = store.all_places()
    kept, rep = filter_places(raw, strict=True)
    pct = lambda x: f"{x} ({x * 100 // max(len(kept), 1)}%)"

    lines = [
        "# Kerala resorts - final report",
        "",
        f"Generated: {datetime.date.today().isoformat()}",
        "",
        "## Headline numbers",
        "",
        "| metric | value |",
        "|---|---|",
        f"| places discovered (raw) | {len(raw)} |",
        f"| Kerala resorts (filtered) | {len(kept)} |",
        f"| districts covered | {len({p.district for p in kept})} |",
        f"| with phone | {pct(sum(1 for p in kept if p.phone))} |",
        f"| with website | {pct(sum(1 for p in kept if p.website))} |",
        f"| with email | {pct(sum(1 for p in kept if p.email))} |",
        f"| with description | {pct(sum(1 for p in kept if p.description))} |",
        f"| with rating | {pct(sum(1 for p in kept if p.rating))} |",
        "",
        "## Queries",
        "",
    ]
    c = collections.Counter(r.get("status") for r in store.queries.values())
    lines.append(f"Total {len(store.queries)}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    lines += [
        "",
        "## Filter breakdown (raw -> kept)",
        "",
        "| filter | dropped |",
        "|---|---|",
    ]
    for k in (
        "drop_outside_state_geo",
        "drop_other_state_address",
        "drop_not_lodging",
        "drop_duplicate",
        "drop_no_coords",
    ):
        lines.append(f"| {k.replace('drop_', '')} | {rep[k]} |")

    lines += ["", "## By district", "", "| district | resorts | phones | emails |", "|---|---|---|---|"]
    bd = collections.defaultdict(list)
    for p in kept:
        bd[p.district].append(p)
    for d in sorted(bd, key=lambda d: -len(bd[d])):
        ps = bd[d]
        lines.append(
            f"| {d} | {len(ps)} | {sum(1 for p in ps if p.phone)} | {sum(1 for p in ps if p.email)} |"
        )

    lines += ["", "## Top categories", ""]
    for t, n in collections.Counter(p.title for p in kept).most_common(8):
        lines.append(f"- {t}: {n}")

    lines += [
        "",
        "## Incomplete rows (kept, flagged)",
        "",
        f"- no phone: {rep['flag_missing_phone']}",
        f"- no email: {rep['flag_missing_email']}",
        f"- no description: {rep['flag_missing_description']} (Google writes blurbs for ~12% of places)",
        f"- website is social/OTA page: {rep['flag_social_site']}",
        "",
        "## Files",
        "",
        "- `output/kerala_resorts.csv` - filtered, the deliverable",
        "- `output/resorts_report.csv` - raw, unfiltered",
    ]
    return "\n".join(lines) + "\n"


def main():
    store = Store(os.path.join(PROJECT_ROOT, "checkpoints"))
    out = os.path.join(PROJECT_ROOT, "output", "FINAL_STATS.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(build(store))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
