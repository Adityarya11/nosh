"""T3 verification (code only): grades each LLM-proposed substitute against the dataset and filters it by restrictions."""

import re
from typing import Literal

from pydantic import BaseModel

from nosh.config import CUE_REGEX, MENTION_CHARS, MENTION_WINDOW, MIN_LIFT, MIN_SUPPORT, OMIT, SUPPORT_SHOWN, WIDEN_MAX_CLASSES
from nosh.data.ingredients import clean_name
from nosh.store import Term, connect, raw_pattern, resolve_term, term_has_lines, term_hits
from nosh.tasks.evidence import SimilarSet, Support, similar_dishes, split_by_target, violates
from nosh.tasks.evidence_explicit import Explicit, TextMention, explicit_or, text_mentions

Grade = Literal["direct", "indirect", "none"]
RANK = {"direct": 0, "indirect": 1, "none": 2}


class ClassSet(BaseModel):
    name: str
    size: int


class Candidate(BaseModel):
    name: str
    grade: Grade
    reason: str
    basis: list[str]
    support: list[Support]


class TargetEvidence(BaseModel):
    target: str
    similar: SimilarSet
    classes: list[ClassSet]
    candidates: list[Candidate]
    filtered: list[tuple[str, str]]
    retrieved_ids: list[str]


class _Group(BaseModel):
    label: str
    with_ids: list[str]
    without_ids: list[str]


def target_key(name: str) -> str:
    return resolve_term(name).canonical


def _lines(ids: list[str], term: Term) -> dict[str, str]:
    hits, params = term_hits([term])
    rows = connect().execute("SELECT i.recipe_id, i.raw_line FROM recipe_ingredients i WHERE i.recipe_id IN (SELECT unnest(?)) "
                             f"AND {hits} ORDER BY i.recipe_id, i.pos", [ids, *params]).fetchall()
    out: dict[str, str] = {}
    for rid, line in rows:
        out.setdefault(rid, line)
    return out


def _titles(ids: list[str]) -> dict[str, str]:
    return dict(connect().execute("SELECT id, title FROM recipes WHERE id IN (SELECT unnest(?))", [ids]).fetchall())


def _contrast(group: _Group, term: Term) -> tuple[str, bool, dict[str, str]]:
    wo, w = _lines(group.without_ids, term), _lines(group.with_ids, term)
    over = len(wo) >= MIN_SUPPORT and len(wo) / max(len(group.without_ids), 1) >= MIN_LIFT * len(w) / max(len(group.with_ids), 1)
    text = f"{len(wo)}/{len(group.without_ids)} without the target vs {len(w)}/{len(group.with_ids)} with, in {group.label}"
    return text, over, wo


def _explicit_lines(ex: Explicit | None) -> list[Support]:
    if not ex:
        return []
    return [Support(recipe_id=x.recipe_id, title=x.title, raw_line=x.raw_line) for x in sorted(ex.lines, key=lambda x: not x.in_similar)]


def _grade(name: str, term: Term, ex: Explicit | None, sim_group: _Group, class_group: _Group | None, mentions: list[TextMention]) -> Candidate:
    basis, support = [], _explicit_lines(ex)
    if ex:
        basis.append(f"{ex.n_in_similar} similar-dish and {ex.n_total - ex.n_in_similar} other recipe(s) have a '<target> or {name}' line")
    sim_text, sim_ok, sim_lines = _contrast(sim_group, term)
    basis.append(sim_text)
    cls_ok, cls_lines = False, {}
    if class_group:
        cls_text, cls_ok, cls_lines = _contrast(class_group, term)
        basis.append(cls_text)
    if mentions:
        basis.append(f"{len(mentions)} instruction sentence(s) mention substituting it")
        basis += [f"mention in [{m.recipe_id}] {m.title}: \"{m.sentence[:MENTION_CHARS]}\"" for m in mentions[:SUPPORT_SHOWN]]
    # only the groups that actually satisfied the criterion may supply citable lines
    shown = {**(cls_lines if cls_ok else {}), **(sim_lines if sim_ok else {})}
    titles = _titles(list(shown))
    support += [Support(recipe_id=r, title=titles[r], raw_line=line) for r, line in shown.items()]
    if ex and ex.n_in_similar:
        grade, reason = "direct", "explicit '<target> or substitute' line in a similar dish"
    elif ex:
        grade, reason = "indirect", "explicit line, but in another kind of dish"
    elif sim_ok:
        grade, reason = "indirect", "appears more often in similar dishes without the target than with it"
    elif cls_ok:
        grade, reason = "indirect", "same pattern in the widened dish class"
    elif mentions:
        grade, reason, support = "indirect", "mentioned as a substitution in instructions", []
    else:
        grade, reason, support = "none", "predicted, no local evidence", []
    return Candidate(name=name, grade=grade, reason=reason, basis=basis, support=support)


# the target, a substitution cue and the candidate must sit close together in one sentence
def _near_regex(target: str, name: str) -> re.Pattern:
    t, c, gap = raw_pattern(clean_name(target)), raw_pattern(clean_name(name)), rf".{{0,{MENTION_WINDOW}}}"
    return re.compile(rf"{t}{gap}(?:{CUE_REGEX}){gap}{c}|{c}{gap}(?:{CUE_REGEX}){gap}{t}", re.I)


def _omission(target: str, group: _Group) -> Candidate:
    n, total = len(group.without_ids), len(group.with_ids) + len(group.without_ids)
    if n < MIN_SUPPORT:
        return Candidate(name=OMIT, grade="none", reason="predicted, too few similar dishes omit it", basis=[f"{n} of {total} similar dishes omit it"], support=[])
    support = [Support(recipe_id=r, title=t, raw_line=f"(no {target} line)") for r, t in _titles(group.without_ids[:SUPPORT_SHOWN]).items()]
    reason = f"{n} of {total} similar dishes do not use it"
    return Candidate(name=OMIT, grade="indirect", reason=reason, basis=[reason], support=support)


def _blocked(name: str, target: Term, groups: list[str], excluded: list[str], dataset_only: bool) -> str | None:
    term = resolve_term(name)
    if term.canonical == target.canonical:
        return "same ingredient as the target"
    if term.family in {resolve_term(x).family for x in excluded}:
        return "excluded by the user"
    if why := violates(name, clean_name(name), term.canonical, term.family, groups):
        return f"breaks the {why} restriction"
    if dataset_only and not term_has_lines(term):
        return "not an ingredient in the dataset"
    return None


def verify_target(dish: str, target_text: str, class_names: list[str], proposed: list[str], groups: list[str], excluded: list[str],
                  dataset_only: bool) -> TargetEvidence:
    target = resolve_term(target_text)
    ids, total, level, query = similar_dishes(dish)
    with_ids, without_ids, unknown = split_by_target(ids, target)
    sim = SimilarSet(level=level, query=query, ids=ids, total_matching=total, with_ids=with_ids, without_ids=without_ids, unknown_ids=unknown)
    explicit, _ = explicit_or(target, ids, groups, violates, dataset_only)
    sim_group = _Group(label=f"{len(ids)} '{query}' recipes", with_ids=with_ids, without_ids=without_ids)
    classes = [(c, similar_dishes(c)[0]) for c in class_names[:WIDEN_MAX_CLASSES] if c.lower() != query.lower()]
    union = sorted({i for _, cids in classes for i in cids})
    class_group = None
    if union:
        cw, cwo, _ = split_by_target(union, target)
        class_group = _Group(label=f"{len(union)} recipes of the widened classes ({', '.join(c for c, _ in classes)})", with_ids=cw, without_ids=cwo)
    mentions = text_mentions(target, ids)
    cands, filtered, seen = [], [], set()
    for name in proposed:
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        if name.lower() == OMIT:
            cands.append(_omission(target_text, sim_group))
        elif why := _blocked(name, target, groups, excluded, dataset_only):
            filtered.append((name, why))
        else:
            term = resolve_term(name)
            near = _near_regex(target_text, name)
            cands.append(_grade(name, term, explicit.get(term.family), sim_group, class_group, [m for m in mentions if near.search(m.sentence)]))
    cands.sort(key=lambda c: RANK[c.grade])
    retrieved = set(ids) | set(union) | {x.recipe_id for ex in explicit.values() for x in ex.lines}
    return TargetEvidence(target=target_text, similar=sim, classes=[ClassSet(name=c, size=len(i)) for c, i in classes], candidates=cands,
                          filtered=filtered, retrieved_ids=sorted(retrieved))
