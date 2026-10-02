import json
import re
import urllib.parse

# Validated against live Google (2026-10): !1s{q}!7i60 requests 60 results;
# anything above 60 gets truncated by Google, so 60 is the hard cap.
SEARCH_PB_TMPL = (
    "!1s{query}!7i{count}!10b1"
    "!12m6!1m1!18b1!2m1!20e3!6m1!114b1"
    "!17m1!3e1"
    "!20m57!2m2!1i203!2i100!3m2!2i4!5b1"
    "!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
    "!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3"
    "!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2"
    "!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
    "!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
)

FID_RE = re.compile(r"^0x[0-9a-f]+:0x[0-9a-f]+$")


def build_search_url(query: str, count: int = 60, hl: str = "en", gl: str = "in") -> str:
    q = urllib.parse.quote_plus(query)
    pb = urllib.parse.quote(
        SEARCH_PB_TMPL.format(query=q, count=min(count, 60)), safe=""
    )
    return f"https://www.google.com/search?tbm=map&hl={hl}&gl={gl}&q={q}&pb={pb}"


def _sentences(el: list, depth: int = 0) -> list[str]:
    """Pull editorial prose out of a description block.

    Google nests the blurb at inconsistent depth: sometimes [null, "sentence", ...]
    one level down, sometimes wrapped again as [null, ..., [["s1", "s2", ...]]].
    Walk until prose turns up rather than hardcoding a level.
    ponytail: length+endstop heuristic; it also fires on a few long address
    fragments, which is harmless because a place gets one description anyway.
    """
    out: list[str] = []
    for sub in el:
        if isinstance(sub, str):
            s = sub.strip()
            if 30 < len(s) < 900 and s.endswith(".") and s.count(" ") > 4:
                out.append(s)
        elif isinstance(sub, list) and depth < 4:
            out.extend(_sentences(sub, depth + 1))
    return out


def _scan_place(entry: list) -> dict | None:
    """entry is the per-place JSON array; find fields by content, not fixed index."""
    fid_i = None
    for i, v in enumerate(entry):
        if isinstance(v, str) and FID_RE.match(v) and i + 1 < len(entry) and isinstance(entry[i + 1], str):
            fid_i = i
            break
    if fid_i is None:
        return None

    place = {
        "feature_id": entry[fid_i],
        "name": entry[fid_i + 1],
        "title": "",
        "description": "",
        "website": "",
        "latitude": None,
        "longitude": None,
        "rating": "",
        "review_count": "",
        "address": "",
        "address_parts": [],
    }

    # types: fid, name, null, [types...]
    if fid_i + 3 < len(entry) and isinstance(entry[fid_i + 3], list):
        types = [t for t in entry[fid_i + 3] if isinstance(t, str)]
        if types:
            place["title"] = types[0]

    for el in entry:
        if isinstance(el, list):
            # website slot: [url, domain, ...]
            if not place["website"] and el and isinstance(el[0], str) and el[0].startswith("http"):
                if not re.search(r"google|gstatic|ggpht", el[0]):
                    place["website"] = el[0]
            # coords slot: [null, null, lat, lng]
            if (
                place["latitude"] is None
                and len(el) >= 4
                and el[0] is None
                and isinstance(el[2], (int, float))
                and isinstance(el[3], (int, float))
                and abs(el[2]) <= 90
                and abs(el[3]) <= 180
            ):
                place["latitude"] = float(el[2])
                place["longitude"] = float(el[3])
            # rating block ends with [..., rating(float<=5), reviews(int)]
            if (
                not place["rating"]
                and len(el) >= 2
                and isinstance(el[-2], float)
                and isinstance(el[-1], int)
                and 0 < el[-2] <= 5
            ):
                place["rating"] = str(el[-2])
                place["review_count"] = str(el[-1])
            # address parts: list of plain strings
            if not place["address_parts"] and el and all(isinstance(x, str) for x in el):
                if any("," in x for x in el):
                    place["address_parts"] = el
            # description: block of sublists each carrying a prose sentence,
            # e.g. [[null, "Upscale property with free WiFi...", ...], ...]
            if not place["description"]:
                sents = _sentences(el)
                if sents:
                    place["description"] = " ".join(sents)

    if place["address_parts"]:
        joined = ", ".join(place["address_parts"])
        if not joined.startswith(place["name"]):
            joined = f"{place['name']}, {joined}"
        place["address"] = joined

    return place


def parse_search(text: str) -> list[dict]:
    data = json.loads(text[4:] if text.startswith(")]}'") else text)
    found: list[dict] = []
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, list):
            for v in node:
                if isinstance(v, list):
                    p = _scan_place(v)
                    if p and p["feature_id"] not in seen:
                        seen.add(p["feature_id"])
                        found.append(p)
                    walk(v)
        return None

    walk(data)
    return found


def search_places(backend, query: str, count: int = 60) -> list[dict]:
    from .backend import fetch_api

    text = fetch_api(backend, build_search_url(query, count))
    return parse_search(text)
