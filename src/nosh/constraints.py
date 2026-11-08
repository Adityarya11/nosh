"""Turns a stated constraint into a restriction group or an ingredient exclusion; says so when it can do neither."""

from typing import Literal, NamedTuple

from nosh.config import NEGATION_PREFIXES
from nosh.data.ingredients import clean_name
from nosh.restrictions import resolve_group
from nosh.store import resolve_term, term_has_lines


class Resolved(NamedTuple):
    kind: Literal["group", "ingredient", "none"]
    value: str | None
    note: str | None


def strip_negation(text: str) -> str:
    words = text.lower().split()
    while words and words[0] in NEGATION_PREFIXES:
        words.pop(0)
    return " ".join(words)


# order: restriction name (exact, then fuzzy), then an ingredient that appears in the dataset, else not applied
def resolve(text: str) -> Resolved:
    bare = strip_negation(text)
    group, note = resolve_group(bare)
    if group:
        return Resolved("group", group, note)
    name = clean_name(bare)
    if name and term_has_lines(resolve_term(name)):
        return Resolved("ingredient", name, None)
    return Resolved("none", None, f"I found no restriction or ingredient line matching '{bare or text}' in the dataset, "
                                  "so I did not apply it.")
