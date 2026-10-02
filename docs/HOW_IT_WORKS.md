# How resorts-scraper works

The complete technical story: why it is built this way, what was proven live,
and how to operate and extend it.

---

## 1. Goal

Scrape **as many resorts as possible** for every locality of a district
(starting with Kozhikode and Wayanad, later all of Kerala) from Google Maps,
and produce a CSV with:

```
name, title, description, phone, email, website, latitude, longitude,
place_id, address, rating, review_count, district, search_query
```

- `title` = Google's category line (e.g. `Resort hotel`, `Home Stay`)
- `description` = Google's editorial blurb, when the place is notable enough
  to have one (present for ~12% of places)
- `email` = never comes from Google. It is harvested by crawling each
  resort's own website (see §9) — Google Maps exposes no emails at all.

---

## 2. How the data is obtained (the recon findings)

Everything below was validated with live probes against Google before a single
line of the program was written.

### What does NOT work

| Idea | Result |
|---|---|
| Fetch `google.com/maps/search/...` HTML | JS-only "mapslite" shell, zero results in HTML |
| `tbm=map` with plain `q=` (no `pb`) | returns query metadata only, no places |
| `ll=@lat,lng,zoom` geo-bias parameter | **silently ignored** — identical results for different coordinates |
| `pb` with `!7i120` (120 results) | Google truncates to ~35 |
| Coordinate queries (`resorts near 11.68,76.13`) | only 6–7 results, useless for tiling |

### What DOES work

1. **Search endpoint** — `GET https://www.google.com/search?tbm=map`
   with a protobuf `pb` "shopping list" parameter. Returns structured
   protobuf-over-JSON (anti-XSSI prefix `)]}'`) with up to **60 places**:
   name, feature id, category, rating, review count, address, website,
   coordinates.
2. **Details endpoint** — `GET https://www.google.com/maps/preview/place?pb=...`
   per feature id. Returns the **phone number** (search results never contain
   phones), plus website/address/coords/hours for cross-filling.
3. **Geo-tiling via locality names** — `resorts in Kalpetta`,
   `resorts in Sulthan Bathery`, … each return results tightly localized
   around that place with **~0 overlap** between different localities.
   Text is the geo-bias mechanism.

### The validated `pb` formats

Search (requests 60 results — the maximum that survives):

```
!1s{query}!7i60!10b1!12m6!1m1!18b1!2m1!20e3!6m1!114b1!17m1!3e1
!20m57!2m2!1i203!2i100!3m2!2i4!5b1!6m6!1m2!1i86!2i86!1m2!1i408!2i240
!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3
!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2
!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0
!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20
```

Details (feature id in `!1s`):

```
!1m14!1s{fid}!3m9!1m3!1d5000!2d0!3d0!2m0!3m2!1i1024!2i768!4f13.1
!4m2!3d0!4d0!13m1!2m0!15m47!1m8!4e2!18m5!3b0!6b0!14b1!17b1!20b1!20e2!4b1
!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!29b1
!30m1!2b1!36b1!43b1!52b1!55b1!56m1!1b1
!65m5!3m4!1m3!1m2!1i224!2i298!22m1!1e81!29m0
!30m6!3b1!6m1!2b1!7m1!2b1!9b1!32b1!37i771
```

Both live in `search.py` / `details.py` (`*_PB_TMPL`).

### The response structure

Each place is one JSON array inside the payload. Fields were located by
**content scanning** rather than fixed indices (indices drift between
responses), in `search.py::_scan_place`:

```
[ token, token, [address parts…], null,
  [null, null, null, [null,"1,978 reviews",…], null, null, null, 4.4, 1978],  ← rating block
  null, null,
  ["https://site.example/", "site.example", …],                              ← website (or null)
  null,
  [null, null, 11.612, 76.083],                                              ← coordinates
  "0x3ba…:0x1ec9…",                                                          ← feature id
  "iCon Residency",                                                          ← name
  null,
  ["Hotel", "Resort hotel", …],                                              ← categories → title
  …, "<name>, <full address>" ]
```

The parser therefore looks for: an `0x…:0x…` id followed by a name string; a
list ending with `(float ≤ 5, int)` → rating/reviews; a `[null, null, lat, lng]`
→ coordinates; a `[url, domain]` → website; a plain-string list → address parts.

---

## 3. Coverage strategy (the "maximum data" part)

Google returns at most 60 results per query, and a single `resorts in <city>`
query barely scratches a district. The planner (`planner.py`) stacks three
mechanisms:

1. **Locality enumeration** — `config/districts.yaml` lists every town/area of
   the district (Kozhikode: 31, Wayanad: 20). Each locality is queried with
   four templates:
   `resorts in X`, `resort in X`, `holiday resorts in X`, `resorts near X`.
2. **Cap-driven recursion** — if any query returns ≥ 58 results, Google almost
   certainly cut it off. The planner immediately expands that locality into its
   `subs` (sub-localities in the YAML) and queues their queries too.
   Verified live: `Kalpetta (cap) → Meppadi → Chundale` cascade.
3. **Deduplication** — everything is keyed by Google's feature id
   (`0x…:0x…`), so overlap between variants/subs collapses automatically.

A run's printed coverage report tells you which localities hit the cap — those
are the ones to extend in the YAML for even deeper coverage.

---

## 4. Program architecture

```
resorts-scraper/
├── pyproject.toml            pip package + `resorts` console script
├── config/districts.yaml     locality + sub-locality lists (edit to extend)
├── src/resorts_scraper/
│   ├── cli.py                run | status | export | doctor
│   ├── planner.py            query generation + 60-cap expansion
│   ├── search.py             tbm=map pb build + response parser
│   ├── details.py            preview/place pb build + phone parser
│   ├── backend.py            HTTP backends, block detection, backoff
│   ├── browser.py            Scrapling StealthySession mode (desktop)
│   ├── store.py              JSONL checkpoints, resume, dedupe
│   ├── export.py             CSV writer + coverage report
│   └── models.py             Place dataclass + CSV schema
├── checkpoints/*.jsonl       append-only run state (resume source of truth)
├── output/resorts_report.csv the report
└── docs/HOW_IT_WORKS.md      this document
```

### Data flow of `resorts run`

```
districts.yaml ──► planner ──► query queue
                                  │
                    search (60 results) ──► parse ──► dedupe by feature id
                                  │                      │
                        cap ≥58? ─┴── expand subs        ▼
                                                   checkpoints/places.jsonl
                                                          │
                    details per place (phone…) ◄──────────┘
                                  │
                            checkpoints again
                                  │
                              export CSV
```

### Backends (Scrapling integration)

`backend.py` picks a transport for every HTTP call:

| mode | transport |
|---|---|
| `scrapling` | Scrapling `FetcherSession(impersonate="chrome")` — TLS fingerprint spoofing |
| `urllib` | stdlib fallback (no dependencies) |
| `auto` (default) | Scrapling if importable, else urllib |
| `browser` | `browser.py`: `StealthySession` + `capture_xhr` on the Maps UI, DOM fallback for place pages |

On the machine this was developed on (Termux/Android, Python 3.14) Scrapling's
native dependencies cannot build, so `auto` selected urllib and every result
in this document was produced through it. On desktop Linux:

```bash
pip install ".[fetchers]"
scrapling install          # browsers, enables --mode browser
resorts doctor --probe     # confirms which backend is live
```

Browser mode is the escape hatch: it drives the real Maps UI and snaffles the
XHR payloads the page itself downloads (`capture_xhr`), falling back to DOM
parsing (`a[href*='/maps/place/']`, `data-item-id^='phone:tel:'`, …) when no
payload is captured.

### Reliability

- **Block detection** — any response without the `)]}'` prefix is treated as a
  consent/CAPTCHA/`/sorry/` page → `Blocked`.
- **Backoff** — each fetch retries up to 4 times with exponential sleep +
  jitter; 3 consecutive blocked *queries* stop the search phase safely.
- **Politeness** — `--delay` seconds (default 2.0) + 15–60 % random jitter
  between searches; detail lookups capped at `--workers` (default 3).
- **Checkpoints** — every query outcome and every place is appended to
  `checkpoints/*.jsonl` *as it happens*. Ctrl+C, a crash, or a block lose
  nothing: rerunning the same command resumes (finished queries are skipped,
  places merged by feature id).

---

## 5. CLI reference

```bash
resorts doctor [--probe]                 # environment + optional live check
resorts run    --district Kozhikode      # full district (resumable)
resorts run    --district Wayanad --locality Kalpetta   # single locality
resorts status                           # progress + coverage report
resorts export -o output/resorts_report.csv
```

`run` flags: `--delay`, `--workers`, `--max-queries` (pilots), `--no-details`,
`--mode auto|http|scrapling|urllib|browser`, `--checkpoint DIR`, `--config PATH`.

---

## 6. What was tested (all green)

| Test | Result |
|---|---|
| `doctor --probe` | 60 places from `resorts in Kalpetta`, names/titles/websites correct |
| Kozhikode pilot (4 queries) | 34 unique places, dedupe across variants correct |
| Detail enrichment | phone+website+coords parsed; verified that 8 phoneless places *genuinely* have no phone in Google's payload (not a parse bug) |
| Cap expansion (Kalpetta) | 60-result cap detected → Meppadi/Chundale sub-queries auto-queued → 226 places from one locality |
| Resume | killed mid-run, rerun continued from checkpoints with zero lost/duplicated work |
| `status` / `export -o` | 830 places → CSV: 830 coords, 466 phones, 492 websites, email column blank everywhere |
| CSV schema | matches the 13 documented columns, UTF-8-BOM (Excel-safe) |

Coverage at the time of writing: **830 unique resorts, 165 queries
(98 ok / 67 cap-hit), zero blocks** — the two-district run was stopped early
by request; a full `resorts run --district Kozhikode` / `--district Wayanad`
picks up from here and finishes the job.

---

## 7. Known limitations & troubleshooting

- **Phone coverage ≈ 60–85 %** — Google simply has no phone for some places;
  retrying (`--detail-tries`, default 3) does not invent numbers. Websites are
  likewise absent when the business never listed one.
- **No emails** — by design (scope decision). Column exists, always empty.
- **60-result ceiling** — no query can return more; the planner's expansion
  is what gets around it. Check the coverage report for capped localities
  without `subs` and add village/area names to the YAML.
- **`scrapling install` fails / no browsers** — program still runs over HTTP
  (`auto` falls back to urllib). Only `--mode browser` needs browsers.
- **Blocked mid-run** — the run stops after 3 consecutive blocks, prints the
  reason, and saves state. Wait a while (or switch `--mode browser` / add a
  proxy later) and rerun the same command.
- **Endpoints break one day** — Google's internal APIs drift. Health check:
  `resorts doctor --probe`. The `pb` templates in `search.py`/`details.py`
  are the only parts that would need updating.

---

## 8. Extending to the whole Kerala

1. Add a district block to `config/districts.yaml` following the existing
   pattern (`localities` + optional `subs`).
2. `resorts run --district <name>` — checkpoints keep districts independent
   and all results accumulate into the same CSV.
3. After each district, read the coverage report and add `subs` where
   localities hit the 60 cap.

The same skeleton works for any category (`hotels`, `restaurants`, …): change
the query templates in `planner.py` (`QUERY_TEMPLATES`).


---

## 9. What was added after the first two districts

### 9.1 Description is free

Google's editorial blurb (`"Upscale property with free WiFi, breakfast &
parking..."`) is already in the **search** payload, not the details payload.
It sits at a variable depth inside the per-place array, so `_sentences()`
walks down a few levels rather than hardcoding a position, and keeps strings
that are 30-900 chars, end in a period, and have more than four words — which
excludes addresses and category tags.

Verified live: 9 of 60 places in one Kalpetta response carried a blurb.
Overall coverage lands around 12%; Google only writes them for notable
properties.

### 9.2 Email needs a separate crawler

Google exposes no emails (0 hits across detail payloads), so `email.py`
fetches each resort's own site:

- `robots.txt` is parsed once per host and obeyed before every request.
- deobfuscates `info (at) site.com`, `info[at]site.com`, `info &#64; site.com`.
- keeps only addresses on the resort's own domain, or an explicit
  `mailto:` — so a tracking pixel in a footer is not reported as the
  business's address.
- prefers a contact-style local part (`info@`, `reservations@`, `booking@`).
- at most two contact pages per site, so a crawl can never dominate runtime.

Hit rate is ~50% of sites that have a website at all.

### 9.3 Reviews are not reachable over plain HTTP

Google retired `/maps/preview/review/listentitiesreviews`. Nine path/`pb`
combinations were swept: all returned 404 or 400. The working place payload
contains no review text either. Getting reviews would require `--mode browser`
with Scrapling's browsers, letting the Maps UI load and capturing the XHR.

### 9.4 Precision pass (`resorts export --clean`)

Recall-first querying means the raw set contains non-Kerala places and
non-lodging categories. `clean.py` filters at export time, so nothing is
re-scraped:

| filter | why it exists |
|---|---|
| Kerala bounding box (8.05-12.95 N, 74.75-77.65 E) | Google returns Bengaluru and Maharashtra places for Kerala locality names — including one in Armenia |
| address must not name another state | Coorg and Mahé sit *inside* the bbox, so coordinates alone would keep them |
| title must be a lodging category | restaurants, wedding venues, auditoriums, chicken shops |
| duplicate collapse | same normalized name within ~2km under two Google ids |

About 26% of raw rows are dropped. Incomplete rows are flagged, not removed.

### 9.5 Sharding

`--shard i/n` splits outstanding work across processes. The slice is taken from
the work list **sorted by place id**, so it is a pure function of the data and
two processes never compute different answers regardless of when they start.
Verified: four shards of 1,010 places sum to 1,010 with zero overlap.

```bash
for i in 1 2 3 4; do resorts phones --shard $i/4 --workers 6 & done
```

### 9.6 Parallel district runs

`run-districts.sh` starts one `resorts run` process per district. Districts are
independent query sets and every write is an append to the shared
`checkpoints/*.jsonl`, which `Store` merges by feature id on load, so
concurrent workers cannot lose each other's work. Google sees `--delay` per
worker; with four workers a district set runs roughly 14x faster than
sequentially.

### 9.7 IPv6 gotcha on this machine

A host that advertises AAAA records without a working IPv6 route makes every
`urllib` request walk dead addresses first — each paying its full timeout —
so requests appear to hang forever while `curl` works fine. Python's urllib
has no Happy Eyeballs. `backend.force_ipv4()` pins `getaddrinfo` to `AF_INET`
for the process. Query time went from *unbounded* to ~1.4s.

---

## 10. Coverage at this writing

| | |
|---|---|
| places discovered | 6814 |
| kept after filtering | 5039 (14 districts) |
| phone | 4273 of 5039 (85%) |
| website | 3055 (61%) |
| email | 1068 (21%, sweep interrupted) |
| description | 883 (12%) |
| queries | 1700 (1163 ok / 537 cap-hit), 0 blocks |
