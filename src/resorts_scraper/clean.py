"""Filter the raw checkpoint export into a usable Kerala resort report.

The scraper is recall-first: query "resorts in X" and Google returns whatever
it thinks is nearby, including things that are not resorts, and "nearby"
crosses state lines. This module is the precision pass, applied at export time
so nothing has to be re-scraped.

Four filters, each reported so the losses are auditable:

1. geo      - Kerala bounding box, padded; catches places Google returned for
              a Kerala locality name but which sit far outside the state.
2. state    - the address must not name a different Indian state/UT. Catches
              border-town contamination that the bbox cannot (Coorg, Mahé).
3. category - keep a known list of lodging types. Recall beats precision while
              searching; the reverse is true once you have a report.
4. dupes    - the same resort listed twice by Google under different feature
              ids. Collapsed by normalized name + close coordinates.

Nothing is deleted from the checkpoints: this only shapes the CSV.
"""

import re
import unicodedata

# Kerala's extent, padded ~0.15 deg on each side so genuine edge-of-state
# properties (Kasaragod's Kannur border, Vypin) are not clipped.
KERALA_BOX = (8.05, 12.95, 74.75, 77.65)  # lat_min, lat_max, lon_min, lon_max

# States/UTs adjacent to, or confused with, Kerala in Google's results.
OTHER_STATES = (
    "Karnataka", "Tamil Nadu", "Puducherry", "Maharashtra", "Goa",
    "Andhra Pradesh", "Telangana", "Lakshadweep",
)

# Lodging categories Google assigns to places offering a bed. Anything outside
# this list is a restaurant, a venue, a shop or an attraction.
LODGING_TITLES = {
    "resort", "resort hotel", "health resort", "beach resort", "wellness hotel",
    "hotel", "extended stay hotel", "serviced accommodation", "guest house",
    "home stay", "homestay", "hostel", "backpacker hostel", "bed & breakfast",
    "lodge", "inn", "villa", "cottage", "chalet", "bungalow", "house",
    "holiday home", "holiday apartment rental", "service apartment", "apartment",
    "condominium", "farmstay", "farm stay", "camping cabin", "camping",
    "tented camp", "campground", "glamping", "tree house", "mountain hut",
    "backpacker hostal", "hostal", "tourist home", "guesthouse",
}

# Social/OTA pages: not the resort's own site, so they are poor email targets.
SOCIAL_HOSTS = (
    "instagram.com", "facebook.com", "twitter.com", "x.com", "youtube.com",
    "linkedin.com", "pinterest.com", "wa.me", "whatsapp.com", "t.me",
    "booking.com", "tripadvisor.", "airbnb.", "agoda.", "expedia.", "olx.",
    "justdial.", "sulekha.", "makemytrip.", "linktr.ee", "tripoto.",
    "google.com", "wikipedia.org", "tripadvisor.", "holidayiq.com",
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _coords(place) -> tuple[float, float] | None:
    if place.latitude is None or place.longitude is None:
        return None
    return float(place.latitude), float(place.longitude)


def in_kerala(place) -> bool:
    c = _coords(place)
    if c is None:
        return False
    la, lo = c
    return KERALA_BOX[0] <= la <= KERALA_BOX[1] and KERALA_BOX[2] <= lo <= KERALA_BOX[3]


def address_in_kerala(place) -> bool:
    addr = place.address or ""
    if not addr:
        return True  # no address to judge on; geo filter already applied
    names_other = any(s in addr for s in OTHER_STATES)
    if names_other:
        return False
    return True


def is_lodging(place) -> bool:
    return (place.title or "").strip().lower() in LODGING_TITLES


def is_own_site(place) -> bool:
    url = (place.website or "").lower()
    return bool(url) and not any(h in url for h in SOCIAL_HOSTS)


def _near(a, b, limit_deg: float = 0.02) -> bool:
    return abs(a[0] - b[0]) < limit_deg and abs(a[1] - b[1]) < limit_deg


def filter_places(places: list, strict: bool = False) -> tuple[list, dict]:
    """Return (kept, report) where report counts what each filter removed.

    strict=True is the final deliverable: a row must carry title, phone and
    coordinates, or it is dropped instead of flagged.
    """
    report = {
        "total": len(places),
        "drop_no_coords": 0,
        "drop_outside_state_geo": 0,
        "drop_other_state_address": 0,
        "drop_not_lodging": 0,
        "drop_duplicate": 0,
        "drop_missing_required": 0,
        "flag_social_site": 0,
        "flag_missing_phone": 0,
        "flag_missing_email": 0,
        "flag_missing_description": 0,
    }

    kept = []
    for p in places:
        if _coords(p) is None:
            report["drop_no_coords"] += 1
            continue
        if not in_kerala(p):
            report["drop_outside_state_geo"] += 1
            continue
        if not address_in_kerala(p):
            report["drop_other_state_address"] += 1
            continue
        if not is_lodging(p):
            report["drop_not_lodging"] += 1
            continue
        if strict and not (p.title and p.phone and p.name):
            report["drop_missing_required"] += 1
            continue
        kept.append(p)

    deduped = _dedupe(kept, report)

    for p in deduped:
        if p.website and not is_own_site(p):
            report["flag_social_site"] += 1
        if not p.phone:
            report["flag_missing_phone"] += 1
        if not p.email:
            report["flag_missing_email"] += 1
        if not p.description:
            report["flag_missing_description"] += 1

    report["kept"] = len(deduped)
    return deduped, report


def _dedupe(kept: list, report: dict) -> list:
    """Two ways one resort survives a scrape twice:

    - identical Google coordinates (name spelled differently by the owner)
    - same normalized name, or one name a prefix of the other, within ~2km
      ("Nisarga Resort" vs "Nisarga Resort Wayanad")

    Keys go in dicts first so this stays O(n) instead of n^2; only places that
    collide on a key pay for a distance check.
    """
    kept.sort(key=lambda x: (x.district, (x.name or "").lower()))
    by_coord: dict[str, tuple] = {}
    by_name: dict[str, tuple] = {}
    by_prefix: dict[str, list] = {}
    out = []
    for p in kept:
        c = _coords(p)
        if c is None:
            out.append(p)
            continue
        ck = f"{c[0]:.5f},{c[1]:.5f}"
        nk = _norm(p.name)
        if ck in by_coord:
            report["drop_duplicate"] += 1
            continue
        dup = False
        if nk:
            prev = by_name.get(nk)
            if prev is not None and _near(prev, c):
                dup = True
            if not dup:
                for head in (nk[:8], nk[:10]):
                    if len(nk) < 8:
                        break
                    for pc, plen in by_prefix.get(head, []):
                        # one name is a prefix of the other (plen/nk share head)
                        if _near(pc, c) and plen != len(nk):
                            dup = True
                            break
                    if dup:
                        break
        if dup:
            report["drop_duplicate"] += 1
            continue
        by_coord[ck] = c
        if nk:
            by_name.setdefault(nk, c)
            if len(nk) >= 8:
                by_prefix.setdefault(nk[:8], []).append((c, len(nk)))
                by_prefix.setdefault(nk[:10], []).append((c, len(nk)))
        out.append(p)
    return out


def format_report(report: dict) -> str:
    t, k = report["total"], report["kept"]
    dropped = t - k
    lines = [
        f"Kept {k} of {t} places ({k / t * 100:.1f}%), dropped {dropped}.",
        "",
        "Dropped:",
        f"  outside Kerala bounding box   {report['drop_outside_state_geo']:>6d}   resorts Google returned for a Kerala locality name but sit elsewhere",
        f"  address names another state   {report['drop_other_state_address']:>6d}   Coorg / Mahé / border-town contamination",
        f"  not a lodging category        {report['drop_not_lodging']:>6d}   restaurants, venues, shops, attractions",
        f"  duplicate listing             {report['drop_duplicate']:>6d}   same resort under two Google ids",
        f"  no coordinates                {report['drop_no_coords']:>6d}",
        f"  missing title/phone/coords    {report.get('drop_missing_required', 0):>6d}   dropped only in strict mode",
        "",
        "Kept but incomplete (not dropped, worth chasing):",
        f"  no phone                      {report['flag_missing_phone']:>6d}",
        f"  no email                      {report['flag_missing_email']:>6d}",
        f"  no description                {report['flag_missing_description']:>6d}   Google writes blurbs for ~15% of places",
        f"  website is social/OTA page    {report['flag_social_site']:>6d}   not the resort's own site",
    ]
    return "\n".join(lines)
