"""T3 evidence helpers (code only): similar dishes by title, WITH/WITHOUT-target split, restriction check on a line."""

from typing import Literal

from pydantic import BaseModel

from nosh.config import MIN_SIMILAR, QUERY_STOPWORDS, SIMILAR_N
from nosh.data.ingredients import singularize
from nosh.restrictions import check_line
from nosh.store import connect
from nosh.tasks.targets import target_sql

Grade = Literal["direct", "indirect", "none"]


class Support(BaseModel):
    recipe_id: str
    title: str
    raw_line: str


class SimilarSet(BaseModel):
    level: Literal["dish", "type"]
    query: str
    ids: list[str]
    total_matching: int
    with_ids: list[str]
    without_ids: list[str]
    unknown_ids: list[str]


def _tokens(dish: str) -> list[str]:
    return [singularize(w) for w in dish.lower().split() if w not in QUERY_STOPWORDS]


def _bm25_ids(query: str, head: str | None) -> tuple[list[str], int]:
    sql = (
        "SELECT id, title FROM (SELECT id, title, fts_main_recipes.match_bm25(id, ?, conjunctive := 1) AS s FROM recipes) "
        "WHERE s IS NOT NULL ORDER BY s DESC, id"
    )
    rows = connect().execute(sql, [query]).fetchall()
    if head:
        # "Banana Bread Pudding" and "Banana Bread Trifle" are other dishes: the title must end with the dish's head noun
        ended = [r for r in rows if (t := _tokens(r[1])) and t[-1] == head]
        rows = ended if len(ended) >= MIN_SIMILAR else rows
    return [r[0] for r in rows[:SIMILAR_N]], len(rows)


# first the whole dish name; if that gives too few recipes, the head noun alone (the broader dish type)
def similar_dishes(dish: str) -> tuple[list[str], int, Literal["dish", "type"], str]:
    words = _tokens(dish)
    ids, total = _bm25_ids(" ".join(words), words[-1])
    if len(ids) >= MIN_SIMILAR or len(words) < 2:
        return ids, total, "dish", " ".join(words)
    ids, total = _bm25_ids(words[-1], None)
    return ids, total, "type", words[-1]


def split_by_target(
    ids: list[str], target_text: str
) -> tuple[list[str], list[str], list[str]]:
    con = connect()
    hits, params = target_sql(target_text)
    has = {
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT i.recipe_id FROM recipe_ingredients i WHERE i.recipe_id IN (SELECT unnest(?)) AND {hits}",
            [ids, *params],
        ).fetchall()
    }
    sub = {
        r[0]
        for r in con.execute(
            "SELECT DISTINCT recipe_id FROM sub_recipe_lines WHERE recipe_id IN (SELECT unnest(?))",
            [ids],
        ).fetchall()
    }
    # a recipe that points to another recipe may hide the target, so it is in neither group
    return (
        [i for i in ids if i in has],
        [i for i in ids if i not in has and i not in sub],
        [i for i in ids if i not in has and i in sub],
    )


def violates(
    line: str, name: str, canonical: str, family: str, groups: list[str]
) -> str | None:
    for g in groups:
        if v := check_line(line, name, canonical, family, g):
            return f"{v.group} ({v.level.replace('_', ' ')})"
    return None
