#!/usr/bin/env python3
"""Load the resort CSVs into Supabase Postgres.

Creates two tables and bulk-inserts the matching CSV into each:

  resorts_source    <- output/kerala_resorts.csv          (raw 14-column source mirror)
  resorts_enriched  <- output/kerala_resorts_enriched.csv (48-column enriched dataset)

Both tables get row level security with a public read policy. Writes happen with
the service key, which bypasses RLS.

Usage:
  SUPABASE_ACCESS_TOKEN=sbp_... SERVICE_KEY=eyJ... \
    python3 scripts/load_supabase.py --ref <project-ref> [--dry-run]

The access token runs the DDL through the Management API. SERVICE_KEY inserts the
rows through PostgREST. Neither is written to disk.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request

BATCH = 500

NUMERIC = {
    "latitude", "longitude", "rating",
    "environment_confidence", "affordability_confidence",
    "elevation_m",
    "distance_to_coast_km", "distance_to_waterbody_km", "distance_to_lake_km",
    "distance_to_beach_km", "distance_to_forest_km", "distance_to_town_km",
    "distance_to_airport_km", "distance_to_railway_km",
}
INTEGER = {"review_count"}


def col_type(name: str) -> str:
    if name in NUMERIC:
        return "double precision"
    if name in INTEGER:
        return "integer"
    return "text"


def coerce(name: str, value: str | None):
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    if name in NUMERIC:
        try:
            return float(value)
        except ValueError:
            return None
    if name in INTEGER:
        try:
            return int(float(value))
        except ValueError:
            return None
    return value


def read_csv(path: str) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = [c.strip() for c in (reader.fieldnames or [])]
        rows = []
        for raw in reader:
            rows.append({col: coerce(col, raw.get(col)) for col in columns})
    return columns, rows


def ddl(table: str, columns: list[str]) -> str:
    cols = ",\n  ".join(f'"{c}" {col_type(c)}' for c in columns)
    return f"""
drop table if exists public.{table};
create table public.{table} (
  id bigint generated always as identity primary key,
  {cols},
  loaded_at timestamptz not null default now()
);
alter table public.{table} enable row level security;
drop policy if exists "public read {table}" on public.{table};
create policy "public read {table}" on public.{table} for select using (true);
create index if not exists {table}_district_idx on public.{table} (district);
"""


def run_sql(ref: str, token: str, sql: str):
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{ref}/database/query",
        data=json.dumps({"query": sql}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        body = response.read().decode()
        return response.status, body


def insert_batch(base: str, service: str, table: str, rows: list[dict]):
    req = urllib.request.Request(
        f"{base}/rest/v1/{table}",
        data=json.dumps(rows).encode(),
        headers={
            "apikey": service,
            "Authorization": f"Bearer {service}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        return response.status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", required=True)
    parser.add_argument("--source", default="output/kerala_resorts.csv")
    parser.add_argument("--enriched", default="output/kerala_resorts_enriched.csv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    service = os.environ.get("SERVICE_KEY", "")
    if not args.dry_run and (not token or not service):
        print("set SUPABASE_ACCESS_TOKEN and SERVICE_KEY", file=sys.stderr)
        return 2

    base = f"https://{args.ref}.supabase.co"
    tables = [
        ("resorts_source", args.source),
        ("resorts_enriched", args.enriched),
    ]

    for table, path in tables:
        columns, rows = read_csv(path)
        print(f"{table}: {len(rows)} rows, {len(columns)} columns from {path}")
        if args.dry_run:
            print(f"  columns: {', '.join(columns)}")
            continue

        status, body = run_sql(args.ref, token, ddl(table, columns))
        print(f"  ddl: {status} {body[:80]}")

        inserted = 0
        for start in range(0, len(rows), BATCH):
            chunk = rows[start:start + BATCH]
            code = insert_batch(base, service, table, chunk)
            if code not in (200, 201, 204):
                print(f"  batch at {start} returned {code}", file=sys.stderr)
                return 1
            inserted += len(chunk)
            print(f"  inserted {inserted}/{len(rows)}", end="\r")
        print(f"  inserted {inserted}/{len(rows)} rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
