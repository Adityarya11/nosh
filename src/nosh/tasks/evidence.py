"""T3 evidence mining (code only): similar dishes, WITH/WITHOUT-target contrast, omission, restriction filtering, grading."""

from typing import Literal

from pydantic import BaseModel

from nosh.config import CONTRAST_TOP, MIN_LIFT, MIN_SIMILAR, MIN_SUPPORT, QUERY_STOPWORDS, SIMILAR_N
from nosh.data.ingredients import singularize
from nosh.restrictions import check_line
from nosh.store import Term, connect, resolve_term, term_hits
from nosh.tasks.evidence_explicit import Explicit, TextMention, explicit_or, text_mentions

Grade = Literal["direct", "indirect", "none"]
Lines = dict[str, dict[str, list[tuple[str, str, str, str]]]]


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


class Candidate(BaseModel):
    family: str
    names: list[str]
    with_n: int
    without_n: int
    explicit: Explicit | None = None
    supports: list[Support]
    grade: Grade = "none"
    reason: str = ""


class Evidence(BaseModel):
    target: str
    dataset_only: bool
    similar: SimilarSet
    candidates: list[Candidate]
    omission_ids: list[str]
    mentions: list[TextMention]
    mentions_total: int
    filtered_out: list[tuple[str, str]]


def _tokens(dish: str) -> list[str]:
    return [singularize(w) for w in dish.lower().split() if w not in QUERY_STOPWORDS]


def _bm25_ids(query: str, head: str | None) -> tuple[list[str], int]:
    sql = ("SELECT id, title FROM (SELECT id, title, fts_main_recipes.match_bm25(id, ?, conjunctive := 1) AS s FROM recipes) "
           "WHERE s IS NOT NULL ORDER BY s DESC, id")
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


def split_by_target(ids: list[str], target: Term) -> tuple[list[str], list[str], list[str]]:
    con = connect()
    hits, params = term_hits([target])
    has = {r[0] for r in con.execute(f"SELECT DISTINCT i.recipe_id FROM recipe_ingredients i WHERE i.recipe_id IN (SELECT unnest(?)) AND {hits}",
                                     [ids, *params]).fetchall()}
    sub = {r[0] for r in con.execute("SELECT DISTINCT recipe_id FROM sub_recipe_lines WHERE recipe_id IN (SELECT unnest(?))", [ids]).fetchall()}
    # a recipe that points to another recipe may hide the target, so it is in neither group
    return ([i for i in ids if i in has], [i for i in ids if i not in has and i not in sub],
            [i for i in ids if i not in has and i in sub])


def _violates(line: str, name: str, canonical: str, family: str, groups: list[str]) -> str | None:
    for g in groups:
        if v := check_line(line, name, canonical, family, g):
            return f"{v.group} ({v.level.replace('_', ' ')})"
    return None


def _family_lines(ids: list[str]) -> Lines:
    rows = connect().execute("SELECT recipe_id, raw_line, name, canonical, family FROM recipe_ingredients "
                             "WHERE recipe_id IN (SELECT unnest(?)) AND canonical <> '' ORDER BY recipe_id, pos", [ids]).fetchall()
    out: Lines = {}
    for rid, raw, name, canonical, family in rows:
        out.setdefault(family, {}).setdefault(rid, []).append((raw, name, canonical, family))
    return out


def _contrast(sim: SimilarSet, target: Term, groups: list[str]) -> tuple[dict[str, Candidate], list[tuple[str, str]]]:
    with_lines, without_lines = _family_lines(sim.with_ids), _family_lines(sim.without_ids)
    titles = dict(connect().execute("SELECT id, title FROM recipes WHERE id IN (SELECT unnest(?))", [sim.ids]).fetchall())
    out, filtered = {}, []
    for family, by_recipe in without_lines.items():
        w_n = len(with_lines.get(family, {}))
        over = len(by_recipe) / max(len(sim.without_ids), 1) >= MIN_LIFT * w_n / max(len(sim.with_ids), 1)
        if family == target.family or len(by_recipe) < MIN_SUPPORT or not over:
            continue
        bad = {rid: why for rid, ls in by_recipe.items() for raw, n, c, f in ls if (why := _violates(raw, n, c, f, groups))}
        if bad:
            filtered.append((family, next(iter(bad.values()))))
            continue
        out[family] = Candidate(family=family, names=sorted({c for ls in by_recipe.values() for _, _, c, _ in ls}), with_n=w_n,
                                without_n=len(by_recipe), supports=[Support(recipe_id=r, title=titles[r], raw_line=ls[0][0])
                                                                    for r, ls in by_recipe.items()])
    return out, filtered


# contrast alone cannot show that a candidate does the target's job, so only an author-written "X or Y" line in a similar dish is direct
def _grade(c: Candidate, level: str) -> None:
    counts = f"{c.without_n} without vs {c.with_n} with"
    if c.explicit and c.explicit.n_in_similar and level == "dish":
        c.grade, c.reason = "direct", f"{c.explicit.n_in_similar} similar-dish recipe(s) list it as the alternative to the target"
    elif c.without_n >= MIN_SUPPORT or c.explicit:
        c.grade, c.reason = "indirect", f"over-represented where the target is absent ({counts}) or listed as an alternative in other dishes; role not verified"
    else:
        c.grade, c.reason = "none", "no local evidence"


def mine(dish: str, target_text: str, exclude_groups: list[str], dataset_only: bool) -> Evidence:
    target = resolve_term(target_text)
    ids, total, level, query = similar_dishes(dish)
    with_ids, without_ids, unknown = split_by_target(ids, target)
    sim = SimilarSet(level=level, query=query, ids=ids, total_matching=total, with_ids=with_ids, without_ids=without_ids, unknown_ids=unknown)
    cands, filtered = _contrast(sim, target, exclude_groups)
    explicit, ex_filtered = explicit_or(target, ids, exclude_groups, _violates, dataset_only)
    for family, ex in explicit.items():
        cands.setdefault(family, Candidate(family=family, names=[ex.name], with_n=0, without_n=0, supports=[])).explicit = ex
    for cand in cands.values():
        _grade(cand, level)

    def key(c: Candidate) -> tuple:
        return (c.grade != "direct", -(c.without_n / max(len(without_ids), 1) - c.with_n / max(len(with_ids), 1)), c.family)
    mentions, mentions_total = text_mentions(target, ids)
    return Evidence(target=target_text, dataset_only=dataset_only, similar=sim, candidates=sorted(cands.values(), key=key)[:CONTRAST_TOP],
                    omission_ids=without_ids, mentions=mentions, mentions_total=mentions_total, filtered_out=sorted(set(filtered + ex_filtered)))
