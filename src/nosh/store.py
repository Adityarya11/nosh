"""Read-only query helpers over DuckDB: connection, build metadata, ingredient-term matching."""

import re
from functools import lru_cache
from typing import NamedTuple

import duckdb

from nosh.config import DB_PATH, STATS_NOTE_TOP
from nosh.data.ingredients import clean_name, fold, modifier_words, normalize
from nosh.restrictions import constituents


class Term(NamedTuple):
    text: str
    canonical: str
    family: str
    bare: bool


@lru_cache(maxsize=1)
def connect() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise FileNotFoundError("data/nosh.duckdb not found; run `uv run nosh build` first")
    return duckdb.connect(str(DB_PATH), read_only=True)


def meta() -> dict[str, str]:
    return dict(connect().execute("SELECT key, value FROM build_meta").fetchall())


def totals() -> tuple[int, int]:
    m = meta()
    dropped = sum(int(v) for k, v in m.items() if k.endswith("_dropped"))
    return int(m["usable_recipes"]), dropped


def resolve_term(text: str) -> Term:
    canonical, family, _ = normalize(clean_name(text))
    return Term(text, canonical, family, canonical == family)


def _free_modifiers(term: Term) -> list[str]:
    own = set(term.family.split()) | set(term.canonical.split())
    return [m for m in modifier_words() if m not in own]


def modified_forms_of(term: Term) -> list[tuple[str, int]]:
    sql = ("SELECT canonical, COUNT(DISTINCT recipe_id) c FROM recipe_ingredients "
           "WHERE family = ? AND list_has_any(string_split(canonical, ' '), ?) GROUP BY 1 ORDER BY c DESC, 1 LIMIT ?")
    return connect().execute(sql, [term.family, _free_modifiers(term), STATS_NOTE_TOP]).fetchall()


def term_sql(term: Term) -> tuple[str, list]:
    if not term.bare:
        return "i.canonical = ?", [term.canonical]
    cond = "(i.canonical = ? OR (i.family = ? AND NOT list_has_any(string_split(i.canonical, ' '), ?)))"
    return cond, [term.canonical, term.family, _free_modifiers(term)]


def _exists(cond: str) -> str:
    return f"EXISTS (SELECT 1 FROM recipe_ingredients i WHERE i.recipe_id = r.id AND {cond})"


def _expand(groups: list[str]) -> list[str]:
    return sorted({g for grp in groups for g in constituents(grp)})


# whole-word pattern for the raw ingredient line; "egg" also matches "eggs", "cherry" also matches "cherries"
def raw_pattern(name: str) -> str:
    words = fold(name).lower().split()
    if not words:
        return "(?!)"
    head, last = [re.escape(w) for w in words[:-1]], words[-1]
    tail = re.escape(last[:-1]) + "(?:y|ies)" if last.endswith("y") else re.escape(last) + "(?:e?s)?"
    return r"\b" + r"\s+".join(head + [tail]) + r"\b"


# an ingredient exclusion matches the normalized name OR the raw line, because the parser can lose words
def term_line_sql(term: Term) -> tuple[str, list]:
    sql, params = term_sql(term)
    return (f"({sql} OR regexp_matches(strip_accents(lower(i.raw_line)), ?))",
            [*params, raw_pattern(clean_name(term.text))])


def term_hits(terms: list[Term]) -> tuple[str, list]:
    parts = [term_line_sql(t) for t in terms]
    return "(" + " OR ".join(s for s, _ in parts) + ")", [x for _, p in parts for x in p]


def term_has_lines(term: Term) -> bool:
    sql, params = term_line_sql(term)
    return connect().execute(f"SELECT 1 FROM recipe_ingredients i WHERE {sql} LIMIT 1", params).fetchone() is not None


# recipes in the id set excluded only because a "recipe follows"-type line carries the flag (or hides a term)
def sub_recipe_excluded_in(ids_sql: str, ids_params: list, groups: list[str], terms: list[Term]) -> int:
    if not groups and not terms:
        return 0
    sub = "SELECT 1 FROM sub_recipe_lines s WHERE s.recipe_id = {a}.recipe_id AND s.pos = {a}.pos"
    clauses, params = [f"EXISTS (SELECT 1 FROM sub_recipe_lines s WHERE s.recipe_id = r.id)"], []
    if groups:
        expanded = _expand(groups)
        clauses.append(f"NOT EXISTS (SELECT 1 FROM line_flags f WHERE f.recipe_id = r.id AND f.grp IN "
                       f"({', '.join('?' for _ in expanded)}) AND NOT EXISTS ({sub.format(a='f')}))")
        params += expanded
    if terms:
        hits, hit_params = term_hits(terms)
        clauses.append(f"NOT EXISTS (SELECT 1 FROM recipe_ingredients i WHERE i.recipe_id = r.id AND {hits} "
                       f"AND NOT EXISTS ({sub.format(a='i')}))")
        params += hit_params
    sql = f"SELECT COUNT(*) FROM recipes r WHERE r.id IN ({ids_sql}) AND " + " AND ".join(clauses)
    return connect().execute(sql, [*ids_params, *params]).fetchone()[0]


def sub_recipe_excluded(all_of: list[Term], any_of: list[Term], groups: list[str], terms: list[Term]) -> int:
    where, params = match_where(all_of, any_of, [], [])
    return sub_recipe_excluded_in(f"SELECT r.id FROM recipes r WHERE {where}", params, groups, terms)


def match_where(all_of: list[Term], any_of: list[Term], exclude_groups: list[str],
                exclude_terms: list[Term]) -> tuple[str, list]:
    clauses, params = [], []
    for t in all_of:
        sql, p = term_sql(t)
        clauses.append(_exists(sql))
        params += p
    if any_of:
        parts = [term_sql(t) for t in any_of]
        clauses.append(_exists("(" + " OR ".join(s for s, _ in parts) + ")"))
        params += [x for _, p in parts for x in p]
    if exclude_groups:
        groups = _expand(exclude_groups)
        clauses.append("NOT EXISTS (SELECT 1 FROM line_flags f WHERE f.recipe_id = r.id AND f.grp IN ("
                       + ", ".join("?" for _ in groups) + "))")
        params += groups
    if exclude_terms:
        hits, hit_params = term_hits(exclude_terms)
        clauses.append(f"NOT EXISTS (SELECT 1 FROM recipe_ingredients i WHERE i.recipe_id = r.id AND {hits})")
        clauses.append("NOT EXISTS (SELECT 1 FROM sub_recipe_lines s WHERE s.recipe_id = r.id)")
        params += hit_params
    return (" AND ".join(clauses) or "TRUE"), params


def recipe_exists(recipe_id: str) -> bool:
    return connect().execute("SELECT 1 FROM recipes WHERE id = ?", [recipe_id]).fetchone() is not None
