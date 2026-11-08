"""T1 search: title BM25 with fuzzy fallback, ingredient SQL, hard constraints, and the coverage probe."""

import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel
from rapidfuzz import fuzz, process

from nosh.config import (COVERAGE_EXACT_MIN, COVERAGE_NEAR_MIN, EVIDENCE_LINES, MIN_RANKABLE_STEPS, NEAREST_TITLES,
                         PROBE_POOL, QUERY_STOPWORDS, SEARCH_POOL, SEARCH_TOP_K, STEP_SORT_LAST, TOKEN_MATCH_MIN)
from nosh.data.ingredients import singularize
from nosh.restrictions import constituents
from nosh.store import Term, connect, match_where, resolve_term, sub_recipe_excluded_in, term_hits

Level = Literal["exact", "near", "none"]


class Hit(BaseModel):
    id: str
    title: str
    score: float


class Coverage(BaseModel):
    level: Level
    hits: list[Hit]


class Candidate(BaseModel):
    id: str
    title: str
    n_steps: int
    n_ingredients: int
    ingredients: list[str] = []


class FlaggedLine(BaseModel):
    raw_line: str
    group: str
    level: str


class NearMiss(BaseModel):
    recipe: Candidate
    lines: list[FlaggedLine]
    too_many_steps: bool


class SearchResult(BaseModel):
    query: str | None
    ingredients: list[str]
    exclude_groups: list[str]
    exclude_terms: list[str]
    max_steps: int | None
    results: list[Candidate]
    n_pool: int
    n_matched: int
    via: str
    near_misses: list[NearMiss] = []
    n_sub_recipe_excluded: int | None = None


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _tokens(text: str) -> list[str]:
    return [singularize(w) for w in _norm(text).split() if w not in QUERY_STOPWORDS]


@lru_cache(maxsize=1)
def _titles() -> tuple[list[str], list[str]]:
    rows = connect().execute("SELECT id, title FROM recipes ORDER BY id").fetchall()
    return [r[0] for r in rows], [_norm(r[1]) for r in rows]


def _fuzzy_ids(query: str, limit: int) -> list[str]:
    ids, titles = _titles()
    return [ids[i] for _, _, i in process.extract(_norm(query), titles, scorer=fuzz.token_sort_ratio, limit=limit)]


def _bm25_hits(query: str, limit: int) -> list[tuple[str, str]]:
    sql = ("SELECT id, title FROM (SELECT id, title, fts_main_recipes.match_bm25(id, ?) AS s FROM recipes) "
           "WHERE s IS NOT NULL ORDER BY s DESC, id LIMIT ?")
    return connect().execute(sql, [query, limit]).fetchall()


def _coverage_of(query_tokens: list[str], title: str) -> float:
    title_tokens = _tokens(title)
    found = sum(any(fuzz.ratio(q, t) >= TOKEN_MATCH_MIN for t in title_tokens) for q in query_tokens)
    return found / len(query_tokens)


def probe(query: str) -> Coverage:
    q_tokens = _tokens(query)
    if not q_tokens:
        return Coverage(level="none", hits=[])
    rows = dict(_bm25_hits(query, PROBE_POOL))
    con = connect()
    for rid in _fuzzy_ids(query, PROBE_POOL):
        if rid not in rows:
            rows[rid] = con.execute("SELECT title FROM recipes WHERE id = ?", [rid]).fetchone()[0]
    scored = sorted(((_coverage_of(q_tokens, t), fuzz.token_sort_ratio(_norm(query), _norm(t)), rid, t)
                     for rid, t in rows.items()), key=lambda x: (-x[0], -x[1], x[2]))
    best = scored[0][0]
    level: Level = "exact" if best >= COVERAGE_EXACT_MIN else "near" if best >= COVERAGE_NEAR_MIN else "none"
    return Coverage(level=level, hits=[Hit(id=rid, title=t, score=round(c, 2)) for c, _, rid, t in scored[:NEAREST_TITLES]])


def _ids_sql(where: str, params: list, query: str | None, fuzzy: list[str] | None, max_steps: int | None) -> tuple[str, list]:
    clauses, p = [where], list(params)
    if max_steps is not None:
        clauses.append(f"r.n_steps BETWEEN {MIN_RANKABLE_STEPS} AND ?")
        p.append(max_steps)
    if fuzzy is not None:
        clauses.append("r.id IN (SELECT unnest(?))")
        p.append(fuzzy)
    body = " AND ".join(clauses)
    if query and fuzzy is None:
        return (f"SELECT id FROM (SELECT r.id, fts_main_recipes.match_bm25(r.id, ?, conjunctive := 1) AS s FROM recipes r WHERE {body}) "
                "WHERE s IS NOT NULL", [query, *p])
    return f"SELECT r.id FROM recipes r WHERE {body}", p


STEP_KEY = f"CASE WHEN r.n_steps >= {MIN_RANKABLE_STEPS} THEN r.n_steps ELSE {STEP_SORT_LAST} END"


def _fetch(ids_sql: str, params: list, order_bm25: str | None, limit: int) -> list[Candidate]:
    cols = "r.id, r.title, r.n_steps, r.n_ingredients"
    base = f"SELECT {cols} FROM recipes r WHERE r.id IN ({ids_sql}) ORDER BY "
    if order_bm25:
        sql = base + f"fts_main_recipes.match_bm25(r.id, ?, conjunctive := 1) DESC NULLS LAST, {STEP_KEY}, r.id LIMIT ?"
        rows = connect().execute(sql, [*params, order_bm25, limit]).fetchall()
    else:
        rows = connect().execute(base + f"{STEP_KEY}, r.id LIMIT ?", [*params, limit]).fetchall()
    return [Candidate(id=i, title=t, n_steps=s, n_ingredients=n) for i, t, s, n in rows]


def _count(ids_sql: str, params: list) -> int:
    return connect().execute(f"SELECT COUNT(*) FROM ({ids_sql})", params).fetchone()[0]


def _add_ingredient_names(candidates: list[Candidate]) -> None:
    rows = connect().execute("SELECT recipe_id, canonical FROM recipe_ingredients WHERE recipe_id IN (SELECT unnest(?)) "
                             "ORDER BY recipe_id, pos", [[c.id for c in candidates]]).fetchall()
    names: dict[str, list[str]] = {c.id: [] for c in candidates}
    for rid, canonical in rows:
        if canonical and canonical not in names[rid]:
            names[rid].append(canonical)
    for c in candidates:
        c.ingredients = names[c.id]


def _flagged_lines(ids: list[str], groups: list[str], terms: list[Term]) -> dict[str, list[FlaggedLine]]:
    out: dict[str, list[FlaggedLine]] = {i: [] for i in ids}
    if groups:
        sql = ("SELECT f.recipe_id, i.raw_line, f.grp, f.level FROM line_flags f JOIN recipe_ingredients i "
               "ON i.recipe_id = f.recipe_id AND i.pos = f.pos WHERE f.recipe_id IN (SELECT unnest(?)) "
               f"AND f.grp IN ({', '.join('?' for _ in groups)}) ORDER BY f.recipe_id, f.level, f.pos")
        for rid, line, grp, level in connect().execute(sql, [ids, *groups]).fetchall():
            out[rid].append(FlaggedLine(raw_line=line, group=grp, level=level))
    if terms:
        hits, params = term_hits(terms)
        direct = connect().execute("SELECT i.recipe_id, i.raw_line FROM recipe_ingredients i WHERE i.recipe_id IN "
                                   f"(SELECT unnest(?)) AND {hits} ORDER BY i.recipe_id, i.pos", [ids, *params]).fetchall()
        hidden = connect().execute("SELECT i.recipe_id, i.raw_line FROM sub_recipe_lines s JOIN recipe_ingredients i "
                                   "ON i.recipe_id = s.recipe_id AND i.pos = s.pos WHERE s.recipe_id IN "
                                   "(SELECT unnest(?)) ORDER BY i.recipe_id, i.pos", [ids]).fetchall()
        label = ", ".join(t.text for t in terms)
        out_direct = {rid for rid, _ in direct}
        for rid, line in direct:
            out[rid].append(FlaggedLine(raw_line=line, group=label, level="contains"))
        for rid, line in hidden:
            if rid not in out_direct:
                out[rid].append(FlaggedLine(raw_line=line, group=label, level="may_contain"))
    return out


def _near_misses(pool_sql: str, params: list, query: str | None, groups: list[str], terms: list[Term],
                 max_steps: int | None) -> list[NearMiss]:
    pool = _fetch(pool_sql, params, query, SEARCH_TOP_K)
    lines = _flagged_lines([c.id for c in pool], groups, terms)
    return [NearMiss(recipe=c, lines=lines.get(c.id, [])[:EVIDENCE_LINES],
                     too_many_steps=max_steps is not None and not MIN_RANKABLE_STEPS <= c.n_steps <= max_steps) for c in pool]


def run_search(dish: str | None, query_text: str | None, ingredients: list[str], exclude_groups: list[str],
               exclude_terms: list[str], max_steps: int | None) -> SearchResult:
    query = " ".join(x for x in (dish, query_text) if x) or None
    terms, excluded = [resolve_term(t) for t in ingredients], [resolve_term(t) for t in exclude_terms]
    base_where, base_params = match_where(terms, [], [], [])
    full_where, full_params = match_where(terms, [], exclude_groups, excluded)
    fuzzy, via = None, "title" if query and not ingredients else "ingredients" if not query else "ingredients+title"
    pool_sql, pool_params = _ids_sql(base_where, base_params, query, None, None)
    if query and _count(pool_sql, pool_params) == 0:
        fuzzy, via = _fuzzy_ids(query, SEARCH_POOL), "fuzzy title"
        pool_sql, pool_params = _ids_sql(base_where, base_params, None, fuzzy, None)
    n_pool = _count(pool_sql, pool_params)
    order = query if fuzzy is None else None
    ids_sql, ids_params = _ids_sql(full_where, full_params, query, fuzzy, max_steps)
    results = _fetch(ids_sql, ids_params, order, SEARCH_TOP_K)
    if exclude_groups or excluded:
        _add_ingredient_names(results)
    result = SearchResult(query=query, ingredients=ingredients, exclude_groups=exclude_groups, exclude_terms=exclude_terms,
                          max_steps=max_steps, results=results, n_pool=n_pool, n_matched=_count(ids_sql, ids_params), via=via)
    if exclude_groups or excluded:
        step_sql, step_params = _ids_sql(base_where, base_params, query, fuzzy, max_steps)
        result.n_sub_recipe_excluded = sub_recipe_excluded_in(step_sql, step_params, exclude_groups, excluded)
    if result.n_matched == 0 and n_pool > 0 and (exclude_groups or excluded or max_steps is not None):
        groups = sorted({g for grp in exclude_groups for g in constituents(grp)})
        result.near_misses = _near_misses(pool_sql, pool_params, order, groups, excluded, max_steps)
    return result
