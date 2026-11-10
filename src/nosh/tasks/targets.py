"""T3 target forms: which lines count as the target itself ("cream" = heavy/whipping/light cream, not sour cream or cream cheese)."""

import json
from functools import lru_cache

from nosh.config import RESOURCES_DIR
from nosh.data.ingredients import normalize, parse_line
from nosh.store import Term, free_modifiers, raw_pattern, resolve_term, term_hits


@lru_cache(maxsize=1)
def _forms() -> dict[str, list[str]]:
    return json.loads((RESOURCES_DIR / "target_forms.json").read_text(encoding="utf-8"))


def own_canonicals(text: str) -> list[str]:
    key = resolve_term(text).canonical
    return _forms().get(key, [key])


# SQL condition (alias i) for "this line is the target itself"; targets without a forms entry use the normal term match
def target_sql(text: str) -> tuple[str, list]:
    term = resolve_term(text)
    forms = _forms().get(term.canonical)
    if forms is None:
        return term_hits([term])
    phrases = [f for f in forms if " " in f]
    if not phrases:
        return "i.canonical IN (SELECT unnest(?))", [forms]
    pattern = "|".join(raw_pattern(p) for p in phrases)
    return (
        "(i.canonical IN (SELECT unnest(?)) OR regexp_matches(strip_accents(lower(i.raw_line)), ?))",
        [forms, pattern],
    )


def line_is_target(line: str, target: Term) -> bool:
    canonical, family, mapped = normalize(parse_line(line).name)
    if target.canonical in _forms():
        return canonical in _forms()[target.canonical]
    return canonical == target.canonical or (
        mapped
        and target.bare
        and family == target.family
        and not set(canonical.split()) & set(free_modifiers(target))
    )
