import json
import os
import threading

from .models import Place


class Store:
    """Append-only JSONL checkpoints with in-memory dedupe and resume support."""

    def __init__(self, checkpoint_dir: str):
        self.dir = checkpoint_dir
        os.makedirs(self.dir, exist_ok=True)
        self.places_path = os.path.join(self.dir, "places.jsonl")
        self.queries_path = os.path.join(self.dir, "queries.jsonl")
        self._lock = threading.Lock()
        self.places: dict[str, Place] = {}
        self.queries: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.places_path):
            with open(self.places_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        p = Place.from_dict(json.loads(line))
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if p.feature_id in self.places:
                        self.places[p.feature_id].merge(p)
                    else:
                        self.places[p.feature_id] = p
        if os.path.exists(self.queries_path):
            with open(self.queries_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "query" in rec:
                        self.queries[rec["query"]] = rec

    def query_done(self, query_text: str) -> bool:
        rec = self.queries.get(query_text)
        return bool(rec and rec.get("status") in ("ok", "cap_hit", "empty"))

    def record_query(self, query_text: str, district: str, locality: str, count: int, status: str) -> None:
        rec = {
            "query": query_text,
            "district": district,
            "locality": locality,
            "count": count,
            "status": status,
        }
        with self._lock:
            self.queries[query_text] = rec
            with open(self.queries_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def save_places(self, places: list[Place]) -> None:
        with self._lock:
            for p in places:
                existing = self.places.get(p.feature_id)
                if existing:
                    existing.merge(p)
                    p = existing
                else:
                    self.places[p.feature_id] = p
                with open(self.places_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(p.__dict__, ensure_ascii=False) + "\n")

    def save_place(self, place: Place) -> None:
        self.save_places([place])

    def all_places(self) -> list[Place]:
        return list(self.places.values())

    def missing_details(self, max_tries: int = 3, shard: str = "") -> list[Place]:
        """Places still missing a phone number.

        `shard` is "i/n": process i of n takes every nth place. Lets several
        phone crawlers run at once without all fetching the same place, since
        the shard is a pure function of the work list, not of what is done.
        """
        todo = [p for p in self.places.values() if not p.phone and p.detail_tries < max_tries]
        return _apply_shard(todo, shard)

    def missing_emails(self, shard: str = "") -> list[Place]:
        """Has a website to crawl but no harvested address yet."""
        todo = [
            p
            for p in self.places.values()
            if p.website and not p.email and not getattr(p, "email_tries", 0)
        ]
        return _apply_shard(todo, shard)


def _apply_shard(items: list, shard: str) -> list:
    """Split a work list across processes: "2/5" takes every 5th, starting at 2."""
    if not shard:
        return items
    try:
        i, n = shard.split("/")
        i, n = int(i), int(n)
    except ValueError:
        raise SystemExit(f"bad --shard {shard!r}, expected i/n like 1/4") from None
    if not 1 <= i <= n:
        raise SystemExit(f"bad --shard {shard!r}, i must be 1..{n}")
    return [x for idx, x in enumerate(sorted(items, key=lambda p: p.feature_id)) if idx % n == i - 1]
