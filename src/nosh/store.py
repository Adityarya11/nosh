"""Read-only query helpers over DuckDB: connection, build metadata, ingredient-term matching."""

from functools import lru_cache
from typing import NamedTuple

import duckdb

from nosh.config import DB_PATH, STATS_NOTE_TOP
from nosh.data.ingredients import clean_name, modifier_words, normalize
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


# recipes that pass every filter except that a "recipe follows"-type line is their only flag in the excluded groups
def sub_recipe_excluded(all_of: list[Term], any_of: list[Term], exclude_groups: list[str]) -> int:
    if not exclude_groups:
        return 0
    where, params = match_where(all_of, any_of, [])
    groups = _expand(exclude_groups)
    marks = ", ".join("?" for _ in groups)
    sql = (f"SELECT COUNT(*) FROM recipes r WHERE {where} "
           "AND EXISTS (SELECT 1 FROM sub_recipe_lines s WHERE s.recipe_id = r.id) "
           f"AND NOT EXISTS (SELECT 1 FROM line_flags f WHERE f.recipe_id = r.id AND f.grp IN ({marks}) "
           "AND NOT EXISTS (SELECT 1 FROM sub_recipe_lines s WHERE s.recipe_id = f.recipe_id AND s.pos = f.pos))")
    return connect().execute(sql, [*params, *groups]).fetchone()[0]


def match_where(all_of: list[Term], any_of: list[Term], exclude_groups: list[str]) -> tuple[str, list]:
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
    return (" AND ".join(clauses) or "TRUE"), params
