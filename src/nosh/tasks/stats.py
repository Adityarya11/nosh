"""T1 aggregates: counts, top ingredients, ingredient-count mean/median and step rankings over the full filtered set."""

from typing import Literal

from pydantic import BaseModel

from nosh.config import STATS_NOTE_TOP, STATS_TOP_N, STEP_RULE
from nosh.data.steps import RANKABLE_SQL
from nosh.store import Term, connect, match_where, modified_forms_of, resolve_term, sub_recipe_excluded, term_sql, totals


class StatsRequest(BaseModel):
    kind: Literal["count", "top_ingredients", "mean_median", "rank_steps"]
    all_of: list[str] = []
    any_of: list[str] = []
    exclude_groups: list[str] = []
    n: int = STATS_TOP_N
    order: Literal["fewest", "most"] = "fewest"


class StatsResult(BaseModel):
    request: StatsRequest
    n_matched: int
    n_total: int
    n_dropped: int
    rows: list[dict] = []
    mean: float | None = None
    median: float | None = None
    n_ranked_pool: int | None = None
    n_no_instructions: int | None = None
    n_sub_recipe_excluded: int | None = None
    norm_notes: list[str] = []
    step_rule: str | None = None


def _norm_note(term: Term) -> str:
    sql, params = term_sql(term)
    rows = connect().execute(
        f"SELECT i.canonical, COUNT(DISTINCT i.recipe_id) c FROM recipe_ingredients i WHERE {sql} "
        "GROUP BY 1 ORDER BY c DESC, 1 LIMIT ?", [*params, STATS_NOTE_TOP]).fetchall()
    counted = ", ".join(f"{name} ({c:,})" for name, c in rows)
    note = f'"{term.text}" matched ingredient names across the dataset (top {STATS_NOTE_TOP} by recipes): {counted}'
    apart = ", ".join(f"{name} ({c:,})" for name, c in modified_forms_of(term)) if term.bare else ""
    return note + (f"; not counted as {term.family} (modified forms, top {STATS_NOTE_TOP}): {apart}" if apart else "")


def compute(req: StatsRequest) -> StatsResult:
    con = connect()
    all_of, any_of = [resolve_term(t) for t in req.all_of], [resolve_term(t) for t in req.any_of]
    where, params = match_where(all_of, any_of, req.exclude_groups)
    total, dropped = totals()
    matched = con.execute(f"SELECT COUNT(*) FROM recipes r WHERE {where}", params).fetchone()[0]
    result = StatsResult(request=req, n_matched=matched, n_total=total, n_dropped=dropped,
                         norm_notes=[_norm_note(t) for t in all_of + any_of])
    if req.exclude_groups:
        result.n_sub_recipe_excluded = sub_recipe_excluded(all_of, any_of, req.exclude_groups)
    if req.kind == "top_ingredients":
        result.rows = _top_ingredients(where, params, req.n)
    elif req.kind == "mean_median":
        result.mean, result.median = con.execute(
            f"SELECT AVG(n_ingredients), MEDIAN(n_ingredients) FROM recipes r WHERE {where}", params).fetchone()
    elif req.kind == "rank_steps":
        _rank_steps(result, where, params)
    return result


def _top_ingredients(where: str, params: list, n: int) -> list[dict]:
    sql = ("SELECT i.family, COUNT(DISTINCT i.recipe_id) c FROM recipe_ingredients i JOIN recipes r ON r.id = i.recipe_id "
           f"WHERE {where} AND i.family <> '' GROUP BY 1 ORDER BY c DESC, i.family ASC LIMIT ?")
    return [{"family": f, "recipes": c} for f, c in connect().execute(sql, [*params, n]).fetchall()]


def _rank_steps(result: StatsResult, where: str, params: list) -> None:
    con = connect()
    direction = "ASC" if result.request.order == "fewest" else "DESC"
    result.step_rule = STEP_RULE
    result.n_ranked_pool = con.execute(f"SELECT COUNT(*) FROM recipes r WHERE {where} AND {RANKABLE_SQL}", params).fetchone()[0]
    result.n_no_instructions = result.n_matched - result.n_ranked_pool
    rows = con.execute(f"SELECT id, title, n_steps FROM recipes r WHERE {where} AND {RANKABLE_SQL} "
                       f"ORDER BY n_steps {direction}, id ASC LIMIT ?", [*params, result.request.n]).fetchall()
    result.rows = [{"id": i, "title": t, "n_steps": n} for i, t, n in rows]
