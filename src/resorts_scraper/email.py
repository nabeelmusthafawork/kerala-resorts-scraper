"""Harvest publicly-listed contact emails from resort websites.

Google never exposes email addresses, so the only route is fetching the
business's own site and reading what it publishes. Rules of the road:

- robots.txt is fetched and obeyed (per-host, cached for the process).
- a plain domain fetch is tried first; a /contact-style page is tried only if
  the homepage yields nothing, so most sites cost exactly one request.
- only addresses on the resort's own domain (or an obvious mailto:) are kept,
  so we don't report a third party's address scraped from a footer widget.
- honors a plain domain as-is; https is preferred with a fallback to http.
"""

import re
import urllib.error
import urllib.parse
import urllib.request
from urllib.robotparser import RobotFileParser

from .backend import USER_AGENTS

# Obfuscations seen in the wild: "info (at) site.com", "info[at]site[dot]com",
# "info &#64; site.com" (html entity), and "info@site .com".
AT_PATTERNS = [
    re.compile(r"([\w.+-]+)\s*(?:@|\(at\)|\[at\]|\{at\})\s*([\w-]+(?:\.[\w-]+)+)", re.I),
]
HTML_ENTITY = re.compile(r"&#0*64;|&#x0*40;|\s*\(at\)|\s*\[at\]", re.I)
CONTACT_HINTS = ("contact", "contact-us", "contactus", "reach-us", "get-in-touch")
EMAIL_MAX_LEN = 100

# Junk that matches the pattern but is not an address.
EMAIL_BLOCKLIST = {
    "example.com", "example.org", "domain.com", "yourdomain.com", "email.com",
    "sentry.io", "wixpress.com", "email@example", "name@domain",
}
ROLE_OK = True  # info@, sales@, reservations@ are all useful for this report.


def _deobfuscate(html: str) -> str:
    html = HTML_ENTITY.sub("@", html)
    return re.sub(r"\s*\(\s*at\s*\)\s*", "@", html, flags=re.I)


def find_emails(html: str, base_domain: str) -> list[str]:
    """Extract addresses published on this page, keeping the good ones."""
    text = _deobfuscate(html)
    found: list[str] = []
    for pat in AT_PATTERNS:
        for local, dom in pat.findall(text):
            local = local.strip("._-")
            addr = f"{local}@{dom.lower()}"
            if not (2 < len(local) <= 64) or not EMAIL_MAX_LEN > len(addr) >= 6:
                continue
            if any(b in dom.lower() for b in EMAIL_BLOCKLIST):
                continue
            # keep only the site's own domain, or an explicit mailto: elsewhere
            base = base_domain.lower().removeprefix("www.")
            dom_l = dom.lower()
            if dom_l != base and not dom_l.endswith("." + base):
                if f'mailto:{addr}' not in text.lower():
                    continue
            if not re.fullmatch(r"[a-z0-9]([a-z0-9._%+-]*[a-z0-9])?", local):
                continue
            if addr not in found:
                found.append(addr)
    return found


class EmailCrawler:
    """robots.txt-aware website email harvester.

    The crawl is the slowest stage (third-party hosts, not Google), so it is
    built for width: many workers, a short per-request timeout, and no delay
    between the robots.txt probe and the page fetch on the same host.
    """

    def __init__(self, timeout: float = 8.0, delay: float = 0.0):
        self.timeout = timeout
        self.delay = delay
        self._robots: dict[str, RobotFileParser | None] = {}
        self._ua = USER_AGENTS[0]

    def _robots_allows(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = RobotFileParser()
            rp.set_url(base + "/robots.txt")
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(base + "/robots.txt", headers={"User-Agent": self._ua}),
                    timeout=self.timeout,
                ) as r:
                    rp.parse(r.read().decode("utf-8", "replace").splitlines())
                self._robots[base] = rp
            except Exception:
                # no robots.txt served -> nothing to obey
                self._robots[base] = None
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self._ua, url)

    def _get(self, url: str) -> str | None:
        if not self._robots_allows(url):
            return None
        req = urllib.request.Request(url, headers={"User-Agent": self._ua, "Accept": "text/html,*/*"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                ctype = r.headers.get("Content-Type", "")
                if "html" not in ctype.lower() and "text" not in ctype.lower():
                    return None
                return r.read(400_000).decode("utf-8", "replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None

    @staticmethod
    def _contact_links(html: str, base: str) -> list[str]:
        out: list[str] = []
        for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
            if any(h in href.lower() for h in CONTACT_HINTS):
                u = urllib.parse.urljoin(base, href.split("#")[0])
                if u not in out and urllib.parse.urlsplit(u).scheme in ("http", "https"):
                    out.append(u)
        return out[:2]  # ponytail: 2 contact pages max, else the crawl dominates runtime

    def crawl(self, website: str) -> tuple[str, str]:
        """Return (email, error). Exactly one place is written per website."""
        parts = urllib.parse.urlsplit(website if "//" in website else "https://" + website)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return "", "bad url"
        domain = parts.netloc

        candidates = [f"https://{domain}/"]
        if parts.scheme == "http":
            candidates.append(f"http://{domain}/")
        candidates.append(f"http://{domain}/")

        for root in candidates:
            html = self._get(root)
            if html is None:
                continue
            emails = find_emails(html, domain)
            if emails:
                return _pick(emails), ""
            for link in self._contact_links(html, root):
                sub = self._get(link)
                if sub:
                    emails = find_emails(sub, domain)
                    if emails:
                        return _pick(emails), ""
            return "", "no email found"
        return "", "unreachable"


def _pick(emails: list[str]) -> str:
    """Prefer a contact-ish local part, else the shortest (usually the main one)."""
    for e in emails:
        if re.match(r"^(info|contact|reservations|booking|sales|reception|frontdesk|mail)", e, re.I):
            return e
    return min(emails, key=len)
