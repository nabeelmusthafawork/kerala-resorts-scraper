# Enriched Kerala resorts: field dictionary

The 13 kept original columns are unchanged and come first in
`output/kerala_resorts_enriched.csv`. The internal `search_query` column is
removed. The 35 added columns are below.

Times are local crawl dates. Distances are straight-line kilometres from the
listing coordinates. `""` means no nearby feature was found in the cached OSM
extract; it does not prove the feature is absent.

## Setting

| field | meaning |
|---|---|
| `environment_primary` | One of beach, backwater, lake, river, hill, mountain, forest, plantation, waterfall, island, cliff, urban, countryside. |
| `environment_tags` | Semicolon-separated secondary labels scoring at least 2.0, capped at five. |
| `environment_confidence` | high / medium / low, from the winning label's evidence score. |
| `environment_reason` | Short evidence trail: distance to coast/lake/forest, elevation, listing keywords, town/airport proximity. |
| `elevation_m` | SRTM 30 m elevation at the coordinate, metres. |

## Water, coast and land context

| field | meaning |
|---|---|
| `distance_to_coast_km` | Distance to the OSM coastline. |
| `nearest_waterbody` | Nearest named OSM water polygon (lake, reservoir, backwater, river). |
| `nearest_waterbody_type` | OSM water kind for that polygon. |
| `distance_to_waterbody_km` | Distance to that polygon. |
| `nearest_lake` | Nearest filtered lake/backwater; ponds, tanks and kulams are excluded. |
| `distance_to_lake_km` | Distance to that lake. |
| `nearest_beach` | Nearest OSM beach polygon, else nearest named beach point. |
| `distance_to_beach_km` | Distance to that beach. |
| `nearest_forest` | Nearest OSM protected area, with industrial campuses and bio parks filtered out. |
| `distance_to_forest_km` | Distance to that protected area. |

## Travel access

| field | meaning |
|---|---|
| `nearest_town` | Nearest OSM place with a town/city/village tag. |
| `distance_to_town_km` | Distance to that place. |
| `nearest_airport` | Nearest commercial airport in the extract. |
| `distance_to_airport_km` | Distance to that airport. |
| `nearest_railway_station` | Nearest OSM railway station. |
| `distance_to_railway_km` | Distance to that station. |

## Amenities

`amenities` is a semicolon list of detected amenities. Each `amenity_*` column
is `yes` or `unknown`; `unknown` means the listing, the resort's own site and
the Google Maps attribute table did not mention it, not that it is absent.

`amenity_spa`, `amenity_restaurant`, `amenity_wifi`, `amenity_parking`,
`amenity_ac`, `amenity_breakfast`.

For rows without a usable own website, a second pass reads the Google Maps
place attribute table for the same listing. It supplies positive amenity flags
and, for the pool field, positive or negative flags. No OTA page is scraped.

## Swimming pool

`swimming_pool` is a separate three-value field:

| value | meaning |
|---|---|
| `yes` | A positive pool phrase appears in the listing or the resort's own site, the site exposes a pool amenity, or Google Maps marks the pool attribute as present. |
| `no` | The inspected text has enough detail and mentions no pool, it explicitly says there is no pool, or Google Maps marks the pool attribute as absent. `no` is absence of evidence, not a verified physical absence. |
| `not-sure` | No source carries a pool signal, or too little usable text exists to call it either way. |

`pool table` and billiards wording are excluded from the pool signal.

## Food

`food_included` records whether food is included in the stay price:

| value | meaning |
|---|---|
| `included` | Explicit wording such as breakfast included, complimentary breakfast, meals included, or half/full board. |
| `excluded` | Explicit wording such as breakfast not included, breakfast at extra cost, or room only. |
| `not-sure` | A restaurant or breakfast is mentioned, but the copy does not say whether it is included. |

A restaurant or breakfast amenity alone is not treated as proof of inclusion.

## Campfire

| value | meaning |
|---|---|
| `yes` | The listing or own site mentions a campfire, bonfire or fire pit. |
| `no` | Explicit negative wording, or enough inspected copy with no mention. |
| `not-sure` | Too little usable text exists to call it either way. |

## Suitability

| field | meaning |
|---|---|
| `best_for` | Semicolon list such as couples, family, backpacker, wellness, luxury, business, group, budget. |

## Affordability

Four bands only: `low`, `medium`, `high`, `premium`.

| band | meaning |
|---|---|
| `low` | Budget stay: hostel, lodge, campground, or clear budget positioning. |
| `medium` | Standard hotel, homestay, guest house or mid-range resort. |
| `high` | Upscale resort, villa or hotel with stronger quality and demand signals. |
| `premium` | Top affordability tier: high position plus quality evidence such as luxury cues, a strong rating, many reviews and upscale amenities. |

An internal calibrated score uses lodging category, district, setting, rating,
review count and amenities. Quality rules can move a stay between bands. No
nightly rate, currency value or price evidence is written to the CSV.

| field | meaning |
|---|---|
| `affordability` | One of `low`, `medium`, `high`, `premium`. |
| `affordability_confidence` | high / medium / low confidence in the band, based on category, rating, review count, amenities and own-site evidence. |
| `affordability_reason` | Short evidence trail: category rule, quality override, rating, reviews and detected amenities. |

A band is a comparison label for this dataset. It is not a booking quote,
availability check or an exact rate.
