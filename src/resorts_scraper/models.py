from dataclasses import dataclass, field, asdict

CSV_FIELDS = [
    "name",
    "title",
    "description",
    "phone",
    "email",
    "website",
    "latitude",
    "longitude",
    "place_id",
    "address",
    "rating",
    "review_count",
    "district",
    "search_query",
]


@dataclass
class Place:
    feature_id: str
    name: str
    title: str = ""
    description: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    latitude: float | None = None
    longitude: float | None = None
    address: str = ""
    rating: str = ""
    review_count: str = ""
    district: str = ""
    search_query: str = ""
    detail_tries: int = 0
    email_tries: int = 0

    def row(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "phone": self.phone,
            "email": self.email,
            "website": self.website,
            "latitude": self.latitude if self.latitude is not None else "",
            "longitude": self.longitude if self.longitude is not None else "",
            "place_id": self.feature_id,
            "address": self.address,
            "rating": self.rating,
            "review_count": self.review_count,
            "district": self.district,
            "search_query": self.search_query,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Place":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def merge(self, other: "Place") -> None:
        for f in self.__dataclass_fields__:
            if f in ("detail_tries", "email_tries"):
                continue
            cur = getattr(self, f)
            new = getattr(other, f)
            if (cur is None or cur == "") and new not in (None, ""):
                setattr(self, f, new)
        self.detail_tries = max(self.detail_tries, other.detail_tries)
        self.email_tries = max(self.email_tries, other.email_tries)
