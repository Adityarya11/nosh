"""Restriction map: classifies an ingredient per restriction group as banned or may-contain, with safe-list overrides."""

import json
import re
from functools import lru_cache
from typing import Literal, NamedTuple

from rapidfuzz import fuzz

from nosh.config import FUZZY_MAX_LEN_DIFF, FUZZY_MIN_RATIO, RESOURCES_DIR, SUB_RECIPE_PHRASES
from nosh.data.ingredients import canonical_map, fold, singularize

Level = Literal["banned", "may_contain"]


class Violation(NamedTuple):
    raw_line: str
    group: str
    level: Level


class _Spec(NamedTuple):
    families: frozenset[str]
    products: tuple[str, ...]
    keywords: tuple[str, ...]
    fuzzy: tuple[str, ...]
    definite_re: re.Pattern
    maybe_re: re.Pattern
    safe_re: re.Pattern


def pad(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", fold(text).lower()).split()
    return " " + " ".join(singularize(w) for w in words) + " "


def phrase_re(phrases: list[str]) -> re.Pattern:
    body = "|".join(re.escape(" ".join(pad(p).split())) for p in phrases) or "(?!)"
    return re.compile(rf"(?<= )({body})(?= )")


@lru_cache(maxsize=1)
def _groups() -> dict[str, dict]:
    return json.loads((RESOURCES_DIR / "restrictions.json").read_text(encoding="utf-8"))


def known_groups() -> list[str]:
    return list(_groups())


def constituents(group: str) -> list[str]:
    out = [group]
    for other in _groups()[group].get("also_excludes", []):
        out += [g for g in constituents(other) if g not in out]
    return out


@lru_cache(maxsize=None)
def _spec(group: str) -> _Spec:
    g = _groups()[group]
    return _Spec(frozenset(g["families"]), tuple(pad(p) for p in g["products"]),
                 tuple(pad(k) for k in g["definite_keywords"] + g["maybe_keywords"]),
                 tuple(pad(k).strip() for k in g.get("fuzzy_keywords", [])),
                 phrase_re(g["definite_keywords"]), phrase_re(g["maybe_keywords"]), phrase_re(g["safe"]))


@lru_cache(maxsize=1)
def _sub_recipe_re() -> re.Pattern:
    return phrase_re(SUB_RECIPE_PHRASES)


def is_sub_recipe(raw_line: str) -> bool:
    return bool(_sub_recipe_re().search(pad(raw_line)))


def _strip_safe(padded: str, spec: _Spec) -> tuple[str, bool]:
    stripped = spec.safe_re.sub("", padded)
    return stripped, stripped != padded


# catches misspellings ("mayonaisse") that the whole-word regex misses
def _fuzzy_hit(text: str, spec: _Spec) -> bool:
    return any(abs(len(tok) - len(term)) <= FUZZY_MAX_LEN_DIFF and fuzz.ratio(tok, term) >= FUZZY_MIN_RATIO
               for tok in text.split() for term in spec.fuzzy)


def _keyword_level(text: str, spec: _Spec) -> Level | None:
    if spec.definite_re.search(text) or _fuzzy_hit(text, spec):
        return "banned"
    return "may_contain" if spec.maybe_re.search(text) else None


def _worse(a: Level | None, b: Level | None) -> Level | None:
    return "banned" if "banned" in (a, b) else a or b


@lru_cache(maxsize=None)
def classify_by_keywords(name: str, canonical: str, family: str, group: str, use_safe: bool = True) -> Level | None:
    spec = _spec(group)
    texts, safe_hit = [], False
    for padded in {pad(name), pad(canonical)}:
        if use_safe:
            padded, hit = _strip_safe(padded, spec)
            safe_hit = safe_hit or hit
        texts.append(padded)
    if not safe_hit and (family in spec.families or any(p in t for t in texts for p in spec.products)):
        return "banned"
    level = None
    for t in texts:
        level = _worse(level, _keyword_level(t, spec))
    return level


# names in canonical_map.json carry reviewed group tags; names outside it fall back to the keyword lists
def classify(name: str, canonical: str, family: str, group: str, use_safe: bool = True) -> Level | None:
    entry = canonical_map().get(name) if use_safe else None
    if entry is not None:
        return entry.get("groups", {}).get(group)
    return classify_by_keywords(name, canonical, family, group, use_safe)


# the parser can lose words ("reduced-fat, reduced-sodium cream of mushroom soup"), so the raw line is scanned too
def _raw_level(padded: str, group: str) -> Level | None:
    if _sub_recipe_re().search(padded):
        return "may_contain"
    spec = _spec(group)
    return _keyword_level(_strip_safe(padded, spec)[0], spec)


def raw_flags(raw_line: str) -> list[tuple[str, Level]]:
    padded = pad(raw_line)
    return [(g, level) for g in known_groups() if (level := _raw_level(padded, g))]


def keyword_inside_word(name: str, group: str) -> str | None:
    tokens = pad(name).split()
    for kw in _spec(group).keywords:
        word = kw.strip()
        if len(word) >= 4 and " " not in word and any(word in t and t != word for t in tokens):
            return word
    return None


def check_line(raw_line: str, name: str, canonical: str, family: str, group: str) -> Violation | None:
    padded = pad(raw_line)
    levels = {g: _worse(classify(name, canonical, family, g), _raw_level(padded, g)) for g in constituents(group)}
    for level in ("banned", "may_contain"):
        for g, found in levels.items():
            if found == level:
                return Violation(raw_line, g, level)
    return None
