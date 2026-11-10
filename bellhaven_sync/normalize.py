"""Text normalisation used by the matcher.

The goal is that two strings describing the same street / facility collapse to the
same key, while genuinely different ones don't. Everything here is deterministic and
has unit tests in tests/test_normalize.py.
"""
import re
from difflib import SequenceMatcher

_STREET_TOKENS = {
    "street": "st", "str": "st",
    "avenue": "ave", "av": "ave",
    "road": "rd",
    "lane": "ln",
    "boulevard": "blvd", "boul": "blvd",
    "drive": "dr", "drv": "dr",
    "pike": "pike", "pk": "pike",
    "court": "ct",
    "place": "pl",
    "parkway": "pkwy",
    "highway": "hwy",
    "circle": "cir",
    "terrace": "ter",
    "trail": "trl",
    "way": "way",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
    "saint": "st",
}

_NAME_TOKENS = {
    "rehab": "rehabilitation",
    "centre": "center",
    "ctr": "center",
    "healthcare": "healthcare",
    "and": "and",
    "snf": "skilled nursing",
}
_NAME_STOPWORDS = {"the", "of", "at", "in", "on", "a", "an"}


def _basic(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_po_box(street: str) -> bool:
    return bool(re.match(r"^\s*p\.?\s*o\.?\s*box", (street or "").lower()))


def norm_street(street: str) -> str:
    """'4850 Northwest Sylvania Avenue' -> '4850 nw sylvania ave'."""
    toks = [_STREET_TOKENS.get(t, t) for t in _basic(street).split()]
    return " ".join(toks)


def norm_city(city: str) -> str:
    return _basic(city)


def norm_zip(z: str) -> str:
    digits = re.sub(r"\D", "", z or "")
    return digits[:5]


def norm_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def norm_name(name: str) -> str:
    """Collapse cosmetic variants: 'The Arbors at Bellhaven - Dayton' and
    'Arbors at Bellhaven Dayton' both -> 'arbors bellhaven dayton'."""
    s = _basic(name)
    s = s.replace("health care", "healthcare")
    toks = []
    for t in s.split():
        t = _NAME_TOKENS.get(t, t)
        if t in _NAME_STOPWORDS:
            continue
        toks.append(t)
    return " ".join(toks)


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_name(a), norm_name(b)).ratio()


_GENERIC_TOKENS = {"bellhaven", "senior", "living", "care", "center", "health", "healthcare", "campus",
                   "nursing", "rehabilitation", "manor", "village", "gardens", "estates", "commons", "place",
                   "assisted", "memory", "skilled", "home", "community", "communities", "group", "trail", "cedar",
                   "harborview", "and"}


def distinctive_overlap(a: str, b: str) -> int:
    """Number of shared name tokens that are not brand/generic words.
    'Bellhaven at Union Square' vs 'Union Square Senior Living' -> 2."""
    ta = set(norm_name(a).split()) - _GENERIC_TOKENS
    tb = set(norm_name(b).split()) - _GENERIC_TOKENS
    return len(ta & tb)


def norm_care(offering: str) -> str:
    return _basic(offering).replace(" and ", " & ")
