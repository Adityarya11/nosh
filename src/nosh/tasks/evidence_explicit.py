"""Explicit T3 evidence: "X or Y" ingredient lines where X is the target, and instruction text that mentions substituting it."""

import re
from typing import Callable

from pydantic import BaseModel

from nosh.config import EXPLICIT_OR_STOP, SUBSTITUTION_CUES, TEXT_MENTIONS_SHOWN
from nosh.data.ingredients import clean_name, normalize, parse_line
from nosh.store import Term, connect, free_modifiers, raw_pattern, resolve_term, term_has_lines

Violates = Callable[[str, str, str, str, list[str]], str | None]


class OrLine(BaseModel):
    recipe_id: str
    title: str
    raw_line: str
    in_similar: bool


class Explicit(BaseModel):
    name: str
    n_total: int
    n_in_similar: int
    lines: list[OrLine]


class TextMention(BaseModel):
    recipe_id: str
    title: str
    sentence: str
    in_similar: bool


def _or_pattern(target: Term) -> str:
    return r"^(.*?" + raw_pattern(clean_name(target.text)) + r")\s*,?\s+or\s+(.+)$"


# the text before "or" must itself be the target ("8 eggs"), not "Easter egg" or "ice cream"
def _is_target(head: str, target: Term) -> bool:
    canonical, family, mapped = normalize(parse_line(head).name)
    return canonical == target.canonical or (mapped and target.bare and family == target.family
                                             and not set(canonical.split()) & set(free_modifiers(target)))


def _stopped(tail: str) -> bool:
    return any(tail.startswith(s) for s in EXPLICIT_OR_STOP)


def explicit_or(target: Term, similar_ids: list[str], groups: list[str], violates: Violates,
                dataset_only: bool) -> tuple[dict[str, Explicit], list[tuple[str, str]]]:
    pattern = _or_pattern(target)
    rows = connect().execute(
        "SELECT i.recipe_id, r.title, i.raw_line, regexp_extract(strip_accents(lower(i.raw_line)), ?, 1), "
        "regexp_extract(strip_accents(lower(i.raw_line)), ?, 2) "
        "FROM recipe_ingredients i JOIN recipes r ON r.id = i.recipe_id WHERE regexp_matches(strip_accents(lower(i.raw_line)), ?) "
        "ORDER BY i.recipe_id, i.pos", [pattern, pattern, pattern]).fetchall()
    sim, out, filtered = set(similar_ids), {}, []
    for rid, title, raw, head, tail in rows:
        if not _is_target(head, target):
            continue
        tail = re.split(r"[,;(]", tail)[0].strip()
        if not tail or _stopped(tail):
            continue
        name = parse_line(tail).name
        canonical, family, mapped = normalize(name)
        if not mapped or family == target.family:
            continue
        if why := violates(tail, name, canonical, family, groups):
            filtered.append((family, why))
        elif dataset_only and not term_has_lines(resolve_term(name)):
            filtered.append((family, "not an ingredient in the dataset"))
        else:
            out.setdefault(family, Explicit(name=canonical, n_total=0, n_in_similar=0, lines=[]))
            out[family].lines.append(OrLine(recipe_id=rid, title=title, raw_line=raw, in_similar=rid in sim))
    for ex in out.values():
        ex.n_total = len({line.recipe_id for line in ex.lines})
        ex.n_in_similar = len({line.recipe_id for line in ex.lines if line.in_similar})
    return out, filtered


def text_mentions(target: Term, similar_ids: list[str]) -> tuple[list[TextMention], int]:
    word = raw_pattern(clean_name(target.text))
    pattern = rf"(?i)(?:[^.]*\b(?:{SUBSTITUTION_CUES})[^.]*{word}[^.]*|[^.]*{word}[^.]*\b(?:{SUBSTITUTION_CUES})[^.]*)"
    con = connect()
    rows = con.execute("SELECT id, title, regexp_extract(instructions_raw, ?, 0) FROM recipes WHERE regexp_matches(instructions_raw, ?) "
                       "ORDER BY id", [pattern, pattern]).fetchall()
    sim = set(similar_ids)
    mentions = [TextMention(recipe_id=r, title=t, sentence=" ".join(s.split()), in_similar=r in sim) for r, t, s in rows if s.strip()]
    mentions.sort(key=lambda m: not m.in_similar)
    return mentions[:TEXT_MENTIONS_SHOWN], len(mentions)
