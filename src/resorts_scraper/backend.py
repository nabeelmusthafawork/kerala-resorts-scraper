import random
import threading
import time
import urllib.error
import urllib.request

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class Blocked(RuntimeError):
    """Google served a block/consent/CAPTCHA page instead of an API payload."""


class BackendUnavailable(RuntimeError):
    pass


def force_ipv4() -> bool:
    """Pin DNS to IPv4. urllib has no Happy Eyeballs: it walks getaddrinfo's
    list in order, so on a host that advertises AAAA records it cannot route,
    every fetch pays one timeout per dead IPv6 address first.
    ponytail: process-wide DNS patch. Drop it when the network gains working IPv6."""
    import socket

    if getattr(socket, "_resorts_ipv4_pinned", False):
        return False
    orig = socket.getaddrinfo

    def v4(host, port, family=0, *a, **kw):
        return orig(host, port, socket.AF_INET, *a, **kw)

    socket.getaddrinfo = v4
    socket._resorts_ipv4_pinned = True
    return True


class UrllibBackend:
    name = "urllib"

    def __init__(self):
        force_ipv4()
        self._ua_i = random.randrange(len(USER_AGENTS))

    def get(self, url: str) -> str:
        self._ua_i = (self._ua_i + 1) % len(USER_AGENTS)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENTS[self._ua_i],
                "Accept": "*/*",
                "Accept-Language": "en-IN,en;q=0.9",
                "Referer": "https://www.google.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.read().decode("utf-8", "replace")


class ScraplingBackend:
    """Uses Scrapling's FetcherSession (TLS impersonation) when available."""

    name = "scrapling"

    def __init__(self):
        try:
            from scrapling.fetchers import FetcherSession
        except Exception as e:
            raise BackendUnavailable(f"scrapling fetchers not importable: {e}")
        self._session = FetcherSession(impersonate="chrome")
        self._lock = threading.Lock()

    def get(self, url: str) -> str:
        with self._lock:
            resp = self._session.get(url, stealthy_headers=True)
        text = getattr(resp, "text", None)
        if not text:
            body = getattr(resp, "body", b"")
            text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
        return text


def make_backend(mode: str = "auto"):
    """mode: auto | http | scrapling | urllib"""
    errors = []
    if mode in ("auto", "http", "scrapling"):
        try:
            return ScraplingBackend()
        except BackendUnavailable as e:
            errors.append(str(e))
            if mode == "scrapling":
                raise
    if mode in ("auto", "http", "urllib"):
        return UrllibBackend()
    raise ValueError(f"unknown mode {mode}")


def ensure_api_payload(text: str) -> str:
    """API endpoints answer with a )]}' anti-XSSI prefix; anything else is a block."""
    if text.lstrip().startswith(")]}'"):
        return text
    low = text[:2000].lower()
    if "consent.google.com" in low or "captcha" in low or "/sorry/" in low:
        raise Blocked("consent/CAPTCHA page served")
    raise Blocked(f"unexpected payload (first 120 chars): {text[:120]!r}")


def fetch_api(backend, url: str, attempts: int = 4) -> str:
    last = None
    for i in range(attempts):
        try:
            return ensure_api_payload(backend.get(url))
        except Blocked as e:
            last = e
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            code = getattr(e, "code", None)
            if code in (429, 503):
                last = Blocked(f"HTTP {code}")
            else:
                last = e
        if i < attempts - 1:
            time.sleep((2**i) * 1.5 + random.uniform(0, 1.5))
    raise last if last else Blocked("fetch failed")


def polite_sleep(base: float) -> None:
    time.sleep(base + random.uniform(0.15 * base, 0.6 * base))
