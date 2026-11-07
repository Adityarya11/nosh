"""Ingredient line parser: strips quantities, units and prep notes, then maps names to canonical/family."""

import json
import re
from functools import lru_cache
from typing import NamedTuple

from nosh.config import HEADER_MAX_WORDS, RESOURCES_DIR

VULGAR = {"¼": 0.25, "½": 0.5, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875}
NUM = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?\s*[¼½¾⅓⅔⅛⅜⅝⅞]?|[¼½¾⅓⅔⅛⅜⅝⅞])"
QTY_RE = re.compile(rf"^\s*({NUM})(?:\s*(?:-|to)\s*{NUM})?\s*")
PAREN_RE = re.compile(r"\([^)]*\)")
LEADING_PAREN_RE = re.compile(r"^(?:\s*\([^)]*\))+\s*")
AD_RE = re.compile(r"\bADVERTISEMENT\b")
SIZE_UNITS = "ounce|pound|package|can|jar|bottle|tablespoon|teaspoon|cup|quart|pint"
SIZE_RE = re.compile(rf"\b(?:\d[\d./]*|one|two|three|four|five|six)[ -]?(?:{SIZE_UNITS})s?\b")
NUMBER_RE = re.compile(r"\b\d[\d./-]*\b")
LEADING_UNIT_RE = re.compile(rf"^(?:(?:one|two|three|four|five|six|ml|{SIZE_UNITS})s?\s+)+")


class Parsed(NamedTuple):
    raw_line: str
    name: str
    qty: float | None
    unit: str | None


def _load(name: str) -> dict:
    return json.loads((RESOURCES_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _units() -> dict[str, str]:
    return {a: u for u, spec in _load("units.json").items() for a in spec["aliases"]}


@lru_cache(maxsize=1)
def _drop_re() -> re.Pattern:
    d = _load("descriptors.json")
    phrases = sorted(d["drop_phrases"] + d["drop_words"], key=len, reverse=True)
    return re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(p) for p in phrases) + r")(?![\w-])")


@lru_cache(maxsize=1)
def _singular_exceptions() -> set[str]:
    return set(_load("map_rules.json")["singular_exceptions"])


@lru_cache(maxsize=1)
def _rules() -> dict:
    return _load("map_rules.json")


@lru_cache(maxsize=1)
def _irregular() -> dict[str, str]:
    return _load("map_rules.json")["irregular_singulars"]


def clean_line(line: str) -> str:
    return " ".join(AD_RE.sub(" ", line).split())


def is_header(line: str) -> bool:
    return line.endswith(":")


def _to_float(s: str) -> float:
    total = 0.0
    for tok in s.split():
        if "/" in tok:
            a, b = tok.split("/")
            total += int(a) / int(b)
        elif tok[-1] in VULGAR:
            total += (float(tok[:-1]) if len(tok) > 1 else 0.0) + VULGAR[tok[-1]]
        else:
            total += float(tok)
    return total


def _split_qty(text: str) -> tuple[float | None, str]:
    m = QTY_RE.match(text)
    if not m:
        return None, text
    raw = re.sub(r"(\d)([¼½¾⅓⅔⅛⅜⅝⅞])", r"\1 \2", m.group(1).strip())
    return _to_float(raw), text[m.end() :]


def _split_unit(text: str) -> tuple[str | None, str]:
    text = LEADING_PAREN_RE.sub("", text)
    words = text.split()
    units = _units()
    for n in (2, 1):
        cand = " ".join(words[:n]).lower().rstrip(",")
        if len(words) >= n and cand in units:
            rest = " ".join(words[n:])
            return units[cand], re.sub(r"^of\s+", "", rest)
    return None, text


def singularize(word: str) -> str:
    if word in _irregular():
        return _irregular()[word]
    if word in _singular_exceptions() or len(word) < 4:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("oes", "ches", "shes", "sses", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def clean_name(text: str) -> str:
    text = NUMBER_RE.sub(" ", SIZE_RE.sub(" ", PAREN_RE.sub(" ", text.lower())))
    text = re.sub(r"^[\w ]{1,20}:\s+", "", text) if len(text.split(":")[0].split()) <= HEADER_MAX_WORDS else text
    for part in text.split(","):
        part = re.sub(r"\s+(?:or|such as)\s+.*$", "", part)
        part = _drop_re().sub(" ", part)
        part = re.sub(r"[^a-z0-9' -]", " ", part)
        part = LEADING_UNIT_RE.sub("", " ".join(part.split()))
        words = [w.strip("-'") for w in part.split()]
        words = [w for w in words if w]
        if words:
            words[-1] = singularize(words[-1])
            return " ".join(words)
    return ""


def parse_line(raw_line: str) -> Parsed:
    qty, rest = _split_qty(raw_line)
    unit, rest = _split_unit(rest)
    while rest.lower().startswith("plus "):
        _, rest = _split_qty(rest[5:])
        extra_unit, rest = _split_unit(rest)
        unit = unit or extra_unit
    return Parsed(raw_line, clean_name(rest), qty, unit)


@lru_cache(maxsize=1)
def canonical_map() -> dict[str, dict[str, str]]:
    path = RESOURCES_DIR / "canonical_map.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def family_of(canonical: str) -> str:
    rules = _rules()
    for joiner in (" in ", " with ", " for ", " and "):
        canonical = canonical.split(joiner)[0]
    padded = f" {canonical} "
    for phrase, family in rules["family_phrases"]:
        if f" {phrase} " in padded:
            return family
    words = canonical.split()
    while len(words) > 1 and words[-1] in rules["generic_heads"]:
        words.pop()
    return words[-1] if words else ""


def normalize(name: str) -> tuple[str, str, bool]:
    entry = canonical_map().get(name)
    if entry:
        return entry["canonical"], entry["family"], True
    return name, family_of(name), False
