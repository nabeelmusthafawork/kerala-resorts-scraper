# Kerala resorts: enrichment report

Source rows: **7,813**. Columns: **48** (13 original + 35 enrichment fields; `search_query` removed).

## Method

- Setting: primary label plus up to four tags, inferred from OSM Kerala extract (coast, beaches, lakes, backwaters, rivers, protected areas, islands, towns, airports, railways), SRTM elevation, listing text and the resort's own website copy.
- Amenities: site and listing text. For rows without a usable own site, a second pass reads the Google Maps place attribute table for the same listing. `unknown` means not found, not absent. `swimming_pool` is a separate yes / no / not-sure field; `no` means no pool was found in the inspected sources, not a verified absence.
- Food and campfire: explicit listing/site wording only. A restaurant or breakfast amenity alone does not prove food is included. `campfire` is yes / no / not-sure; `food_included` is included / excluded / not-sure.
- Affordability: one of `low`, `medium`, `high`, `premium`. A calibrated internal score uses lodging category, district, setting, rating, reviews and amenities; quality rules moderate the band. No nightly rate or currency value is written.
- Website crawls honour `robots.txt`. Setting and access use OSM plus OpenTopoData; rows without a usable own website use Google Maps place attributes from the same listing. No paid APIs and no OTA scraping.

## Coverage

- Rows with an affordability band: **7,813 / 7,813**
- Environment confidence: medium 3,750, high 2,569, low 1,494
- Affordability confidence: medium 6,260, high 1,210, low 343

| travel access | rows with a value |
|---|---:|
| airport | 7,813 (100%) |
| railway station | 7,805 (100%) |
| town | 7,807 (100%) |
| coast distance | 7,813 (100%) |
| lake | 7,011 (90%) |
| beach | 7,813 (100%) |

## Setting

| primary environment | rows |
|---|---:|
| beach | 1,513 |
| urban | 1,373 |
| mountain | 960 |
| hill | 919 |
| backwater | 825 |
| river | 710 |
| lake | 664 |
| forest | 390 |
| countryside | 301 |
| plantation | 87 |
| island | 57 |
| waterfall | 8 |
| cliff | 6 |

## Amenities found

| amenity | rows with `yes` |
|---|---:|
| spa | 859 (11%) |
| restaurant | 2,576 (33%) |
| wifi | 3,917 (50%) |
| parking | 3,223 (41%) |
| air conditioning | 2,697 (35%) |
| breakfast | 2,949 (38%) |

## Swimming pool

| status | rows | share |
|---|---:|---:|
| yes | 1,601 | 20% |
| no | 3,440 | 44% |
| not-sure | 2,772 | 35% |

## Food included

| status | rows | share |
|---|---:|---:|
| included | 591 | 8% |
| excluded | 17 | 0% |
| not-sure | 7,205 | 92% |

## Campfire

| status | rows | share |
|---|---:|---:|
| yes | 507 | 6% |
| no | 2,888 | 37% |
| not-sure | 4,418 | 57% |

## Affordability

| band | rows | share |
|---|---:|---:|
| low | 1,039 | 13% |
| medium | 3,808 | 49% |
| high | 2,438 | 31% |
| premium | 528 | 7% |

| category | rows | low | medium | high | premium |
|---|---:|---:|---:|---:|---:|
| Home Stay | 2,169 | 377 | 1,760 | 32 | 0 |
| Resort hotel | 2,068 | 8 | 28 | 1,592 | 440 |
| Hotel | 1,967 | 37 | 1,549 | 378 | 3 |
| Lodge | 548 | 548 | 0 | 0 | 0 |
| Villa | 357 | 0 | 3 | 284 | 70 |
| Holiday home | 131 | 1 | 102 | 28 | 0 |
| Holiday apartment rental | 113 | 0 | 98 | 15 | 0 |
| Guest house | 85 | 5 | 78 | 2 | 0 |
| Bed & breakfast | 76 | 0 | 69 | 7 | 0 |
| Serviced accommodation | 51 | 0 | 18 | 33 | 0 |
| Service Apartment | 44 | 0 | 18 | 26 | 0 |
| Farmstay | 40 | 0 | 36 | 4 | 0 |

## Best for

| traveller type | rows |
|---|---:|
| family | 3,811 |
| couples | 2,718 |
| group | 815 |
| wellness | 356 |
| luxury | 226 |
| honeymoon | 183 |
| business | 171 |
| budget | 124 |
| backpacker | 46 |

## Caveats

- `district` comes from the Google search query. Some rows sit in a neighbouring district's coordinates; the setting fields use coordinates, so a row can legitimately say Alappuzha and mountain.
- Affordability is a modelled band, not a booking quote, availability check or a claim about the exact rate. It is intended for comparison within this dataset.
- OSM coverage is incomplete in places; `unknown` amenities and blank distance fields mean no nearby feature was found in the extract.

## Files

- `/home/nabeel/Documents/projects/resorts-scraper/output/kerala_resorts_enriched.csv` — enriched rows, original 14 columns first
- `output/enrichment/site_facts.jsonl` — append-only website crawl cache
- `output/enrichment/detail_facts.jsonl` — Google Maps amenity cache for no-site rows
- `output/enrichment/osm_context.json` — derived OSM context
- `output/enrichment/elevation.json` — SRTM elevation cache
