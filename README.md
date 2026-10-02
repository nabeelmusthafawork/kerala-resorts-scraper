# resorts-scraper

Google Maps scraper for **Kerala resorts and homestays**, built on
[Scrapling](https://github.com/D4Vinci/Scrapling) with a plain-HTTP fallback.
Covers all 14 districts, enriches each place with phone/website/coordinates,
optionally harvests public emails and descriptions, and exports a filtered CSV.

> Full technical documentation — endpoints, `pb` formats, coverage algorithm,
> architecture, what was tested: **[docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)**

## Result (final, 2614 KB CSV)

```
16787  places discovered   (raw, output/resorts_report.csv)
7813  clean report rows   (title + phone + coords required on every row)
10901  with a phone number   64%
6913  with a website        41%
2802  with an email         16%   (optional; sweep stopped early by request)
1597  with a description    9%   (Google only writes blurbs for notable places)
2652  queries, 0 blocks
229  duplicates nuked (same Google id / same coords / same name within 2km)
```

## How it works

1. **Search** — Google Maps `tbm=map` internal protobuf endpoint (the same data
   the Maps app renders), 60 results per request. Scrapling's `FetcherSession`
   (TLS impersonation) when installed, automatic urllib fallback otherwise.
2. **Coverage** — every locality in `config/districts.yaml` (462 across Kerala)
   is queried with four variants. When a query hits Google's 60-result cap, the
   planner expands into that locality's sub-localities. Dedupe by Google place id.
3. **Details** — `/maps/preview/place` per place id supplies the phone number
   (never present in search results), plus missing website/address/coordinates.
4. **Emails** *(optional)* — crawls each resort's own website, obeying
   robots.txt, and reads publicly-listed contact addresses. Google never exposes
   email, so this is the only route.
5. **Checkpoints** — every query, place and crawl outcome is appended to
   `checkpoints/*.jsonl`; Ctrl+C or a block stops safely and rerunning resumes.

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/resorts doctor --probe        # verify environment + live endpoint
```

Optional, for TLS impersonation and browser mode:

```bash
.venv/bin/pip install -e ".[fetchers]" && .venv/bin/scrapling install
```

## Usage

```bash
# one district
resorts run --district Kozhikode

# every district, one process each (much faster)
./run-districts.sh                    # all 14
./run-districts.sh Kozhikode Wayanad  # or a subset

# chase missing fields with parallel crawlers
resorts phones --shard 2/4 --workers 6
resorts emails --shard 1/6 --email-workers 10

# report
resorts status
resorts export --clean -o output/kerala_resorts.csv
```

### `--shard i/n`

Splits the outstanding work across processes. The split is a pure function of
the work list sorted by place id, so N processes started at any time each take
a disjoint slice and none fetch the same place twice. Verified: 4 shards of
1,010 places sum to 1,010 with zero overlap.

### `run` flags

| flag | default | meaning |
|---|---|---|
| `--delay` | 2.0 | base seconds between searches (randomized on top) |
| `--workers` | 3 | concurrent detail lookups |
| `--max-queries` | 0 (off) | stop after N search queries (pilots) |
| `--detail-tries` | 3 | max detail attempts per place |
| `--no-details` | off | skip phone enrichment |
| `--no-emails` | off | skip website email harvesting |
| `--mode` | auto | `auto / http / scrapling / urllib / browser` |
| `--out` | `output/resorts_report.csv` | CSV destination |

## Output files

| file | contents |
|---|---|
| `output/kerala_resorts.csv` | **the deliverable** — filtered to Kerala lodging |
| `output/resorts_report.csv` | raw, unfiltered, every discovered place |

### CSV columns

`name, title, description, phone, email, website, latitude, longitude,
place_id, address, rating, review_count, district, search_query`

`title` is Google's category (`Resort hotel`, `Home Stay`, …).

## Filtering (`resorts export --clean`)

Recall-first scraping collects things that are not Kerala resorts. The clean
export drops them at export time, so nothing needs re-scraping:

| filter | what it removes |
|---|---|
| bounding box | resorts Google returned for a Kerala locality name but which sit in another state (Bengaluru, Maharashtra, even Armenia) |
| address state | Coorg, Mahé (Puducherry), border towns that fall inside the box |
| category | restaurants, wedding venues, auditoriums, shops |
| duplicates | one resort listed twice under two Google ids, within 2km |

Roughly a quarter of the raw rows are dropped. `resorts clean` prints exactly
what was removed and why. Incomplete rows are **flagged, not dropped** — a
resort with no phone is still a resort.

## Known limits

- **Emails ~21%** and still climbing — the sweep was interrupted, not finished.
  Roughly half the sites crawled yielded an address.
- **Descriptions ~12%** — not a bug. Google publishes editorial blurbs only for
  notable places.
- **No reviews.** Google's review endpoint (`/maps/preview/review/listentitiesreviews`)
  returns 404; nine path/`pb` variants were tried. Would need browser-mode XHR
  capture (`--mode browser`) with Scrapling's browsers installed.
- **60-result ceiling per query** — the planner's sub-locality expansion is what
  gets around it. Check the coverage report for capped localities without subs.

## Troubleshooting

- **Every command fails with a timeout, but `curl` works** — this machine
  advertises IPv6 with no route and `getaddrinfo` returns AAAA first; urllib
  has no Happy Eyeballs. `backend.force_ipv4()` handles it automatically.
- **Blocked mid-run** — the run stops after 3 consecutive blocks, saves state
  and prints the reason. Wait, switch `--mode browser`, and rerun the same command.
- **Run died without finishing** — a foreground process group got killed. Start
  long runs with `setsid nohup ... & disown`.

## Extending coverage

Add towns/villages to `config/districts.yaml` under `localities` (always
queried, 4 variants each) or `subs` (queried only when the parent locality hits
the 60-result cap). After a run, `resorts status` prints which localities hit
the cap — those are the ones worth deepening.
