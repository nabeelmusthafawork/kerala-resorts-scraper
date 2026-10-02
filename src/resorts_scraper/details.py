import re
import urllib.parse

# Validated live: /maps/preview/place with this pb returns phone, website,
# address, coordinates and hours for a feature id.
DETAIL_PB_TMPL = (
    "!1m14!1s{fid}"
    "!3m9!1m3!1d5000!2d0!3d0!2m0!3m2!1i1024!2i768!4f13.1"
    "!4m2!3d0!4d0"
    "!13m1!2m0"
    "!15m47!1m8!4e2!18m5!3b0!6b0!14b1!17b1!20b1!20e2!4b1"
    "!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!29b1"
    "!30m1!2b1!36b1!43b1!52b1!55b1!56m1!1b1"
    "!65m5!3m4!1m3!1m2!1i224!2i298"
    "!22m1!1e81!29m0!30m6!3b1!6m1!2b1!7m1!2b1!9b1!32b1!37i771"
)

PHONE_RE = re.compile(r'"(\+\d[\d\s\-()]{6,}\d)"')
COORDS_RE = re.compile(r"\[null,\s*null,\s*(-?\d+\.\d+),\s*(-?\d+\.\d+)\]")
FID_NAME_RE = re.compile(r'"(0x[0-9a-f]+:0x[0-9a-f]+)",\s*"([^"]{1,120})"')
WEBSITE_RE = re.compile(r'"(https?://[^"]+)",\s*"([a-z0-9][a-z0-9.-]*\.[a-z]{2,})"')


def build_detail_url(feature_id: str, hl: str = "en", gl: str = "in") -> str:
    pb = urllib.parse.quote(DETAIL_PB_TMPL.format(fid=feature_id), safe="")
    return f"https://www.google.com/maps/preview/place?authuser=0&hl={hl}&gl={gl}&pb={pb}"


def parse_details(text: str, feature_id: str) -> dict:
    out = {"feature_id": feature_id}

    names = FID_NAME_RE.findall(text)
    for fid, name in names:
        if fid == feature_id:
            out["name"] = name
            break
    else:
        if names:
            out["name"] = names[0][1]

    phones = PHONE_RE.findall(text)
    if phones:
        out["phone"] = max(phones, key=len)

    for url, _dom in WEBSITE_RE.findall(text):
        if not re.search(r"google|gstatic|ggpht", url):
            out["website"] = url.split("\\u003d")[0]
            break

    m = COORDS_RE.search(text)
    if m:
        out["latitude"] = float(m.group(1))
        out["longitude"] = float(m.group(2))

    name = out.get("name")
    if name:
        am = re.search('"' + re.escape(name) + r',\s*([^"]{10,300})"', text)
        if am:
            out["address"] = f"{name}, {am.group(1)}"

    rm = re.search(r',\s*(\d\.\d),\s*(\d+)\]', text)
    if rm:
        out["rating"] = rm.group(1)
        out["review_count"] = rm.group(2)

    return out


def fetch_details(backend, feature_id: str) -> dict:
    from .backend import fetch_api

    text = fetch_api(backend, build_detail_url(feature_id))
    return parse_details(text, feature_id)
