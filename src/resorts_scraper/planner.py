from dataclasses import dataclass

import yaml

QUERY_TEMPLATES = [
    "resorts in {loc}",
    "resort in {loc}",
    "holiday resorts in {loc}",
    "resorts near {loc}",
]

# Second wave for localities that hit the 60-cap but have no `subs` in the
# YAML: different wording returns a different top-60, so the union goes past
# what any single query can show. Anchored to QUERY_TEMPLATES[0] so a
# fallback query that itself caps does not regenerate itself forever.
FALLBACK_TEMPLATES = [
    "homestay in {loc}",
    "resort hotels in {loc}",
    "holiday homes in {loc}",
    "villas in {loc}",
    "lodges in {loc}",
    "tourist homes in {loc}",
    "resorts around {loc}",
]

CAP_THRESHOLD = 58  # >= this many results means Google likely cut us off


@dataclass(frozen=True)
class Query:
    text: str
    district: str
    locality: str
    parent: str = ""


class Planner:
    def __init__(self, yaml_path: str):
        with open(yaml_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self._districts: dict = cfg.get("districts", {})

    def districts(self) -> list[str]:
        return list(self._districts)

    def _localities(self, district: str) -> list[str]:
        return list(self._districts.get(district, {}).get("localities", []))

    def subs_for(self, district: str, locality: str) -> list[str]:
        return list(self._districts.get(district, {}).get("subs", {}).get(locality, []))

    def initial_queries(self, district: str | None = None, locality: str | None = None) -> list[Query]:
        queries: list[Query] = []
        names = [district] if district else self.districts()
        for d in names:
            locs = [locality] if locality else self._localities(d)
            for loc in locs:
                queries.extend(self.queries_for(d, loc))
        return queries

    def queries_for(self, district: str, locality: str, parent: str = "") -> list[Query]:
        return [
            Query(text=t.format(loc=locality), district=district, locality=locality, parent=parent)
            for t in QUERY_TEMPLATES
        ]

    def expansion_for(self, query: Query, result_count: int) -> list[Query]:
        """If a query hit the ~60 cap, split into its sub-localities."""
        if result_count < CAP_THRESHOLD:
            return []
        subs = self.subs_for(query.district, query.locality)
        if subs:
            extra: list[Query] = []
            for sub in subs:
                if sub == query.locality:
                    continue
                extra.extend(self.queries_for(query.district, sub, parent=query.locality))
            return extra
        # no subs listed -> alternate wordings instead, once per locality
        if query.text != QUERY_TEMPLATES[0].format(loc=query.locality):
            return []
        return [
            Query(
                text=t.format(loc=query.locality),
                district=query.district,
                locality=query.locality,
                parent=query.locality,
            )
            for t in FALLBACK_TEMPLATES
        ]
