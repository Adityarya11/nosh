"""Restriction map: expands a restriction to banned families/products and checks a recipe's lines."""

import json
from functools import lru_cache
from typing import NamedTuple

from nosh.config import RESOURCES_DIR


class Banned(NamedTuple):
    families: frozenset[str]
    products: frozenset[str]
    may_contain: frozenset[str]


class Violation(NamedTuple):
    raw_line: str
    group: str
    level: str


@lru_cache(maxsize=1)
def _groups() -> dict[str, dict]:
    return json.loads((RESOURCES_DIR / "restrictions.json").read_text(encoding="utf-8"))


def known_groups() -> list[str]:
    return list(_groups())


def expand(group: str) -> Banned:
    spec = _groups()[group]
    fams, prods, maybe = set(spec["families"]), set(spec["products"]), set(spec["may_contain"])
    for other in spec.get("also_excludes", []):
        sub = expand(other)
        fams |= sub.families
        prods |= sub.products
        maybe |= sub.may_contain
    return Banned(frozenset(fams), frozenset(prods), frozenset(maybe))


def check_line(raw_line: str, canonical: str, family: str, group: str) -> Violation | None:
    banned = expand(group)
    if family in banned.families or canonical in banned.products:
        return Violation(raw_line, group, "banned")
    if family in banned.may_contain or canonical in banned.may_contain:
        return Violation(raw_line, group, "may_contain")
    return None
