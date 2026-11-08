"""T2 ingredient matching: hard filters in SQL, then a lexicographic, explainable ranking over existing recipes."""

import json
from functools import lru_cache

from pydantic import BaseModel

from nosh.config import MATCH_PREF_KEYS, MATCH_TOP_K, MIN_RANKABLE_STEPS, RESOURCES_DIR, STEP_SORT_LAST
from nosh.data.ingredients import parse_line
from nosh.restrictions import constituents
from nosh.store import Term, connect, match_where, resolve_term, sub_recipe_excluded_in, term_sql
from nosh.tasks.search import FlaggedLine, flagged_lines

KEYS = {"matched": "DESC", "extra_n": "ASC", "steps_key": "ASC", "id": "ASC"}
DEFAULT_ORDER = ["matched", "extra_n", "steps_key", "id"]
WORDS = {"matched": "fewer of your ingredients matched", "extra_n": "more non-staple extras",
         "steps_key": "more steps", "id": "later recipe ID"}


class UserItem(BaseModel):
    raw: str
    name: str
    qty: float | None = None
    unit: str | None = None


class MatchRow(BaseModel):
    id: str
    title: str
    n_steps: int
    n_ingredients: int
    matched: list[str]
    missing: list[str]
    extra_nonstaple: list[str] = []
    extra_staple: list[str] = []
    quantity_notes: list[str] = []
    violations: list[FlaggedLine] = []
    too_many_steps: bool = False
    why: str = ""


class MatchResult(BaseModel):
    items: list[UserItem]
    skipped: list[str]
    exclude_groups: list[str]
    exclude_terms: list[str]
    max_steps: int | None
    order: list[str]
    rows: list[MatchRow]
    near_misses: list[MatchRow] = []
    n_candidates: int
    n_pool: int
    n_sub_recipe_excluded: int | None = None


@lru_cache(maxsize=1)
def _pantry() -> list[str]:
    return json.loads((RESOURCES_DIR / "pantry.json").read_text(encoding="utf-8"))["canonicals"]


@lru_cache(maxsize=1)
def _units() -> dict:
    return json.loads((RESOURCES_DIR / "units.json").read_text(encoding="utf-8"))


def parse_items(have: list[str]) -> tuple[list[UserItem], list[str]]:
    items, skipped = [], []
    for raw in have:
        p = parse_line(raw)
        if p.name:
            items.append(UserItem(raw=raw, name=p.name, qty=p.qty, unit=p.unit))
        else:
            skipped.append(raw)
    return items, skipped


def order_keys(preferences: list[str]) -> list[str]:
    order = list(DEFAULT_ORDER)
    for pref in preferences:
        for word, key in MATCH_PREF_KEYS.items():
            if word in pref.lower():
                order.remove(key)
                order.insert(1, key)  # matched always stays first
    return order


# one SELECT per recipe: which user ingredients it uses, plus the ranking keys; hard filters go in `where`
def _ranked_sql(terms: list[Term], where: str, where_params: list) -> tuple[str, list]:
    conds = [term_sql(t) for t in terms]
    any_m = " OR ".join(f"l.m{k}" for k in range(len(terms)))
    hits = ", ".join(f"bool_or(l.m{k}) AS h{k}" for k in range(len(terms)))
    matched = " + ".join(f"(CASE WHEN bool_or(l.m{k}) THEN 1 ELSE 0 END)" for k in range(len(terms)))
    line_cols = ", ".join(f"({sql}) AS m{k}" for k, (sql, _) in enumerate(conds))
    extra = "COUNT(DISTINCT CASE WHEN NOT ({}) AND l.canonical <> '' AND {}l.staple THEN l.canonical END)"
    inner = (f"SELECT c.id, c.title, c.n_steps, c.n_ing, "
             f"CASE WHEN c.n_steps >= {MIN_RANKABLE_STEPS} THEN c.n_steps ELSE {STEP_SORT_LAST} END AS steps_key, "
             f"{hits}, {matched} AS matched, {extra.format(any_m, 'NOT ')} AS extra_n "
             f"FROM (SELECT r.id, r.title, r.n_steps, r.n_ingredients AS n_ing FROM recipes r WHERE {where}) c "
             "JOIN (SELECT i.recipe_id, i.canonical, list_contains(?, i.canonical) AS staple, "
             f"{line_cols} FROM recipe_ingredients i) l ON l.recipe_id = c.id GROUP BY c.id, c.title, c.n_steps, c.n_ing")
    return f"SELECT * FROM ({inner}) WHERE matched >= 1", [*where_params, _pantry(), *[x for _, p in conds for x in p]]


def _rows(sql: str, params: list, order: list[str], terms: list[Term], items: list[UserItem]) -> list[MatchRow]:
    cols = ["id", "title", "n_steps", "n_ing", "steps_key", *[f"h{k}" for k in range(len(terms))], "matched", "extra_n"]
    order_sql = ", ".join(f"{key} {KEYS[key]}" for key in order)
    rows = []
    for values in connect().execute(f"{sql} ORDER BY {order_sql} LIMIT ?", [*params, MATCH_TOP_K]).fetchall():
        d = dict(zip(cols, values))
        hit = [d[f"h{k}"] for k in range(len(terms))]
        rows.append(MatchRow(id=d["id"], title=d["title"], n_steps=d["n_steps"], n_ingredients=d["n_ing"],
                             matched=[i.name for i, h in zip(items, hit) if h],
                             missing=[i.name for i, h in zip(items, hit) if not h]))
    return rows


def _fill_extras(rows: list[MatchRow], terms: list[Term]) -> None:
    conds = [term_sql(t) for t in terms]
    sql = ("SELECT i.recipe_id, i.canonical, i.family FROM recipe_ingredients i WHERE i.recipe_id IN (SELECT unnest(?)) "
           f"AND NOT ({' OR '.join(s for s, _ in conds)}) AND i.canonical <> '' ORDER BY i.recipe_id, i.pos")
    staples = _pantry()
    by_id = {r.id: r for r in rows}
    for rid, canonical, _ in connect().execute(sql, [[r.id for r in rows], *[x for _, p in conds for x in p]]).fetchall():
        bucket = by_id[rid].extra_staple if canonical in staples else by_id[rid].extra_nonstaple
        if canonical not in bucket:
            bucket.append(canonical)


def _dimension(unit: str | None) -> str | None:
    return "count" if unit is None else _units().get(unit, {}).get("dim")


def _to_base(qty: float, unit: str | None) -> float:
    return qty * _units()[unit]["to_base"] if unit in _units() else qty


def _quantity_note(item: UserItem, recipe_qty: float | None, recipe_unit: str | None) -> str:
    same = (_dimension(item.unit) is not None and _dimension(item.unit) == _dimension(recipe_unit)
            and (_dimension(item.unit) != "count" or item.unit == recipe_unit))
    if recipe_qty is None or not same:
        return f"{item.name}: name-level match; quantity sufficiency not verified"
    enough = _to_base(item.qty, item.unit) >= _to_base(recipe_qty, recipe_unit)
    unit = f" {item.unit}" if item.unit else ""
    need = f"{recipe_qty:g}{unit if recipe_unit == item.unit else f' {recipe_unit} (converted)'}"
    return f"{item.name}: you have {item.qty:g}{unit}, the recipe needs {need} ({'enough' if enough else 'not enough'})"


def _fill_quantities(rows: list[MatchRow], terms: list[Term], items: list[UserItem]) -> None:
    for item, term in zip(items, terms):
        if item.qty is None:
            continue
        sql, params = term_sql(term)
        found = connect().execute("SELECT i.recipe_id, i.qty, i.unit FROM recipe_ingredients i WHERE i.recipe_id IN "
                                  f"(SELECT unnest(?)) AND ({sql}) AND i.qty IS NOT NULL ORDER BY i.recipe_id, i.pos",
                                  [[r.id for r in rows], *params]).fetchall()
        first: dict[str, tuple] = {}
        for rid, qty, unit in found:
            first.setdefault(rid, (qty, unit))
        for row in rows:
            if item.name in row.matched:
                row.quantity_notes.append(_quantity_note(item, *first.get(row.id, (None, None))))


def _key_value(key: str, row: MatchRow):
    steps = row.n_steps if row.n_steps >= MIN_RANKABLE_STEPS else STEP_SORT_LAST
    return {"matched": len(row.matched), "extra_n": len(row.extra_nonstaple), "steps_key": steps, "id": row.id}[key]


def _show(key: str, value) -> str:
    return "no steps listed" if key == "steps_key" and value == STEP_SORT_LAST else str(value)


def _explain(rows: list[MatchRow], order: list[str], total: int) -> None:
    for i, row in enumerate(rows):
        if i == 0:
            row.why = (f"top of the order: {len(row.matched)}/{total} of your ingredients, "
                       f"{len(row.extra_nonstaple)} non-staple extras, {row.n_steps} step{'' if row.n_steps == 1 else 's'}")
            continue
        prev = rows[i - 1]
        key = next((k for k in order if _key_value(k, prev) != _key_value(k, row)), None)
        row.why = (f"ranked below #{i}: {WORDS[key]} ({_show(key, _key_value(key, row))} vs {_show(key, _key_value(key, prev))})"
                   if key else f"tied with #{i} on every key")


def _finish(rows: list[MatchRow], terms: list[Term], items: list[UserItem], order: list[str]) -> None:
    if rows:
        _fill_extras(rows, terms)
        _fill_quantities(rows, terms, items)
        _explain(rows, order, len(items))


def _sub_recipe_count(terms: list[Term], groups: list[str], excluded: list[Term], max_steps: int | None) -> int:
    any_user = " OR ".join(term_sql(t)[0] for t in terms)
    user_params = [x for t in terms for x in term_sql(t)[1]]
    steps = "" if max_steps is None else f" AND r.n_steps BETWEEN {MIN_RANKABLE_STEPS} AND ?"
    ids_sql = ("SELECT r.id FROM recipes r WHERE EXISTS (SELECT 1 FROM recipe_ingredients i WHERE i.recipe_id = r.id "
               f"AND ({any_user})){steps}")
    return sub_recipe_excluded_in(ids_sql, [*user_params, *([] if max_steps is None else [max_steps])], groups, excluded)


def _near_misses(result: MatchResult, terms: list[Term], items: list[UserItem], groups: list[str], excluded: list[Term]) -> None:
    sql, params = _ranked_sql(terms, "TRUE", [])
    result.n_pool = connect().execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    if result.n_pool == 0 or not (groups or excluded or result.max_steps is not None):
        return
    near = _rows(sql, params, result.order, terms, items)
    lines = flagged_lines([r.id for r in near], sorted({g for grp in groups for g in constituents(grp)}), excluded)
    for row in near:
        row.violations = lines.get(row.id, [])[:2]
        row.too_many_steps = result.max_steps is not None and not MIN_RANKABLE_STEPS <= row.n_steps <= result.max_steps
    result.near_misses = near


def run_match(have: list[str], exclude_groups: list[str], exclude_terms: list[str], max_steps: int | None,
              preferences: list[str]) -> MatchResult:
    items, skipped = parse_items(have)
    terms, excluded = [resolve_term(i.name) for i in items], [resolve_term(t) for t in exclude_terms]
    order = order_keys(preferences)
    where, where_params = match_where([], [], exclude_groups, excluded)
    if max_steps is not None:
        where, where_params = f"{where} AND r.n_steps BETWEEN {MIN_RANKABLE_STEPS} AND ?", [*where_params, max_steps]
    sql, params = _ranked_sql(terms, where, where_params)
    n = connect().execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    result = MatchResult(items=items, skipped=skipped, exclude_groups=exclude_groups, exclude_terms=exclude_terms,
                         max_steps=max_steps, order=order, rows=_rows(sql, params, order, terms, items),
                         n_candidates=n, n_pool=n)
    if exclude_groups or excluded:
        result.n_sub_recipe_excluded = _sub_recipe_count(terms, exclude_groups, excluded, max_steps)
    if n == 0:
        _near_misses(result, terms, items, exclude_groups, excluded)
    _finish(result.rows, terms, items, order)
    _finish(result.near_misses, terms, items, order)
    return result
