import csv
import os
from collections import Counter

from .models import CSV_FIELDS, Place


def export_csv(places: list[Place], out_path: str) -> int:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for p in sorted(places, key=lambda x: (x.district, x.name.lower())):
            w.writerow(p.row())
    return len(places)


def coverage_report(queries: dict) -> str:
    if not queries:
        return "No queries recorded yet."
    counts = Counter(r.get("status", "?") for r in queries.values())
    lines = [f"Queries: {len(queries)}  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))]
    caps = [r for r in queries.values() if r.get("status") == "cap_hit"]
    if caps:
        lines.append(f"Hit the 60-result cap ({len(caps)}) - sub-localities were expanded where available:")
        for r in caps:
            lines.append(f"  ! {r['query']} ({r['count']} results)")
    blocked = [r for r in queries.values() if r.get("status") == "blocked"]
    if blocked:
        lines.append(f"Blocked queries ({len(blocked)}) - rerun to retry:")
        for r in blocked:
            lines.append(f"  x {r['query']}")
    return "\n".join(lines)
