"""Scrape every community from the Bellhaven website.

Sources:
  * /communities?page=N        (paginated directory)
  * /                          (home page may link communities not yet in the directory,
                                e.g. a "new this year" announcement)
  * /communities/<slug>        (detail page: address, care offerings, administrator, phone)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

import requests
from bs4 import BeautifulSoup

from .config import SITE_BASE_URL

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "bellhaven-sync/1.0 (+ownership sync pipeline)"


@dataclass
class Location:
    slug: str
    name: str
    street: str
    city: str
    state: str
    zip: str
    care_offerings: list[str] = field(default_factory=list)
    administrator: str = ""
    phone: str = ""
    url: str = ""
    source: str = "directory"   # directory | homepage

    def to_dict(self) -> dict:
        return asdict(self)


def _get(path: str) -> str:
    r = _SESSION.get(f"{SITE_BASE_URL}{path}", timeout=30)
    r.raise_for_status()
    return r.text


def discover_slugs() -> dict[str, str]:
    """Return {slug: source} for every community link we can find."""
    slugs: dict[str, str] = {}
    page = 1
    while True:
        html = _get(f"/communities?page={page}")
        soup = BeautifulSoup(html, "html.parser")
        found = 0
        for a in soup.select(".card h3 a[href^='/communities/']"):
            slug = a["href"].split("/communities/")[1].strip("/")
            if slug not in slugs:
                slugs[slug] = "directory"
            found += 1
        nxt = soup.find("a", string=re.compile(r"Next"))
        if not found or not nxt:
            break
        page += 1
        if page > 50:
            raise RuntimeError("directory pagination did not terminate")

    # Home page: announcements can link to communities missing from the directory.
    soup = BeautifulSoup(_get("/"), "html.parser")
    for a in soup.select("a[href^='/communities/']"):
        slug = a["href"].split("/communities/")[1].strip("/")
        if slug and slug not in slugs:
            slugs[slug] = "homepage"
    return slugs


def parse_detail(slug: str, html: str, source: str = "directory") -> Location:
    soup = BeautifulSoup(html, "html.parser")
    name = soup.find("h1").get_text(" ", strip=True)
    fields: dict[str, object] = {}
    for dt in soup.select("dl.detail dt"):
        key = dt.get_text(strip=True).lower()
        dd = dt.find_next_sibling("dd")
        if key == "care offerings":
            fields[key] = [b.get_text(strip=True) for b in dd.select(".badge")]
        else:
            fields[key] = dd.get_text("\n", strip=True) if dd else ""

    addr_lines = [ln.strip() for ln in str(fields.get("address", "")).split("\n") if ln.strip()]
    street = addr_lines[0] if addr_lines else ""
    city = state = zip_ = ""
    if len(addr_lines) > 1:
        m = re.match(r"^(.*?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?$", addr_lines[-1])
        if m:
            city, state, zip_ = m.group(1), m.group(2), m.group(3)
        else:
            city = addr_lines[-1]
    return Location(
        slug=slug, name=name, street=street, city=city, state=state, zip=zip_,
        care_offerings=list(fields.get("care offerings", [])),
        administrator=str(fields.get("administrator", "")),
        phone=str(fields.get("phone", "")),
        url=f"{SITE_BASE_URL}/communities/{slug}",
        source=source,
    )


def scrape() -> list[Location]:
    slugs = discover_slugs()
    out = []
    for slug, source in slugs.items():
        out.append(parse_detail(slug, _get(f"/communities/{slug}"), source))
    return out


if __name__ == "__main__":
    import json
    print(json.dumps([l.to_dict() for l in scrape()], indent=2))
