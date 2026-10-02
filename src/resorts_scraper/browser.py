"""Scrapling browser backend: StealthySession + capture_xhr with DOM fallback.

Only usable where Scrapling's browsers are installed (desktop Linux):
    pip install ".[fetchers]" && scrapling install
"""

import re
import urllib.parse

from .backend import Blocked
from .search import parse_search

PLACE_HREF_RE = re.compile(r"/maps/place/([^@/]+)/@(-?\d+\.\d+),(-?\d+\.\d+)")


def browser_status() -> tuple[bool, str]:
    try:
        from scrapling.fetchers import StealthySession  # noqa: F401
    except Exception as e:
        return False, f"scrapling fetchers not importable: {e}"
    import os

    for cache_dir in ("~/.cache/ms-playwright", "~/.cache/camoufox", "~/.cache/scrapling"):
        path = os.path.expanduser(cache_dir)
        if os.path.isdir(path) and os.listdir(path):
            return True, f"browser cache found at {path}"
    return False, "scrapling present but no browser installed - run: scrapling install"


class BrowserBackend:
    name = "browser"

    def __init__(self):
        from scrapling.fetchers import StealthySession

        self._session_cm = StealthySession(headless=True)
        self._session = self._session_cm.__enter__()

    def close(self):
        try:
            self._session_cm.__exit__(None, None, None)
        except Exception:
            pass

    def search(self, query: str, count: int = 60) -> list[dict]:
        """Open the Maps UI for the query, capture its internal search XHRs."""
        url = "https://www.google.com/maps/search/" + urllib.parse.quote_plus(query) + "/"
        captured_payloads: list[str] = []
        resp = None
        try:
            resp = self._session.fetch(url, network_idle=True, capture_xhr="*maps*", wait=3000)
        except TypeError:
            resp = self._session.fetch(url, network_idle=True)
        except Exception as e:
            raise Blocked(f"browser fetch failed: {e}") from e

        for c in getattr(resp, "captured_xhr", None) or []:
            text = getattr(c, "text", None) or getattr(c, "body", "") or ""
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            if text.lstrip().startswith(")]}'"):
                captured_payloads.append(text)

        places: list[dict] = []
        seen: set[str] = set()
        for payload in captured_payloads:
            try:
                for p in parse_search(payload):
                    if p["feature_id"] not in seen:
                        seen.add(p["feature_id"])
                        places.append(p)
            except Exception:
                continue

        if not places:
            places = self._parse_dom(resp)
        return places[:count] if count else places

    @staticmethod
    def _parse_dom(resp) -> list[dict]:
        places: list[dict] = []
        seen: set[str] = set()
        for a in resp.css("a[href*='/maps/place/']"):
            href = a.attrib.get("href", "")
            m = PLACE_HREF_RE.search(href)
            if not m:
                continue
            name = urllib.parse.unquote(m.group(1)).replace("+", " ")
            lat, lng = float(m.group(2)), float(m.group(3))
            key = f"{name}|{lat}|{lng}"
            if key in seen:
                continue
            seen.add(key)
            title_el = a.css("[class*='fontBodyMedium'] ::text").getall()
            places.append(
                {
                    "feature_id": key,
                    "name": name,
                    "title": title_el[0].strip() if title_el else "",
                    "description": "",
                    "website": "",
                    "latitude": lat,
                    "longitude": lng,
                    "rating": "",
                    "review_count": "",
                    "address": "",
                }
            )
        return places

    def details(self, feature_id: str) -> dict:
        """Open the place page and read phone/website/address from the DOM."""
        if re.fullmatch(r"0x[0-9a-f]+:0x[0-9a-f]+", feature_id):
            cid = int(feature_id.split(":")[1], 16)
            url = f"https://www.google.com/maps?cid={cid}"
        else:
            url = feature_id  # DOM-fallback synthetic ids are URLs/query keys

        try:
            page = self._session.fetch(url, network_idle=True, wait=2500)
        except Exception as e:
            raise Blocked(f"browser place fetch failed: {e}") from e

        out: dict = {"feature_id": feature_id}

        phone_el = page.css("[data-item-id^='phone:tel:']").first
        if phone_el:
            label = phone_el.attrib.get("aria-label", "") or phone_el.text.strip()
            out["phone"] = label.replace("Phone:", "").strip()

        web_el = page.css("a[data-item-id='authority']").first
        if web_el:
            out["website"] = web_el.attrib.get("href", "")

        addr_el = page.css("[data-item-id^='address:']").first
        if addr_el:
            label = addr_el.attrib.get("aria-label", "") or addr_el.text.strip()
            out["address"] = label.replace("Address:", "").strip()

        m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", page.url if hasattr(page, "url") else "")
        if not m:
            body_url = " ".join(a.attrib.get("href", "") for a in page.css("a[href*='@']"))
            m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", body_url)
        if m:
            out["latitude"] = float(m.group(1))
            out["longitude"] = float(m.group(2))

        title = page.css("h1").first
        if title:
            out["name"] = title.text.strip()

        return out
