"""T3 output validators (code only): every check the LLM's adapted recipe must pass before it is shown."""

import re

from nosh.config import QUANTITY_ESTIMATE_MARK, SAFETY_PHRASES
from nosh.data.ingredients import parse_line
from nosh.store import connect, resolve_term, term_has_lines, term_hits
from nosh.tasks.adapted import AdaptedRecipe, Substitution
from nosh.tasks.evidence import violates
from nosh.tasks.targets import line_is_target
from nosh.tasks.verify import RANK, TargetEvidence, target_key


def _texts(a: AdaptedRecipe) -> list[str]:
    parts = [a.uncertainty or ""] + a.instructions + a.ingredients
    for s in a.substitutions:
        parts += [s.expected_effects] + [e.note for e in s.evidence]
    return parts


def _contains(recipe_id: str, replacement: str) -> bool:
    hits, params = term_hits([resolve_term(replacement)])
    sql = f"SELECT 1 FROM recipe_ingredients i WHERE i.recipe_id = ? AND {hits} LIMIT 1"
    return connect().execute(sql, [recipe_id, *params]).fetchone() is not None


def _check_substitution(
    s: Substitution, ev: TargetEvidence | None, retrieved: set[str]
) -> list[str]:
    if ev is None:
        return [f"substitution of '{s.original}': not one of the targets"]
    errors = []
    if s.effects_status != "predicted":
        errors.append(
            f"{s.original}: effects_status must be 'predicted' (the dataset has no texture or taste outcomes)"
        )
    if s.replacement is None:
        cand = next((c for c in ev.candidates if c.name == "omit"), None)
    else:
        family = resolve_term(s.replacement).family
        cand = next(
            (
                c
                for c in ev.candidates
                if c.name != "omit" and resolve_term(c.name).family == family
            ),
            None,
        )
    if cand is None:
        return errors + [
            f"{s.original}: replacement '{s.replacement}' is not a verified candidate"
        ]
    for e in s.evidence:
        if e.source == "external":
            errors += [] if e.url else [f"{s.original}: external evidence needs a URL"]
            continue
        if cand.grade == "none":
            errors.append(
                f"{s.original}: '{s.replacement}' has no local evidence; cite nothing and label it predicted"
            )
        elif not e.recipe_id:
            errors.append(f"{s.original}: local evidence needs a recipe_id")
        elif e.recipe_id not in retrieved:
            errors.append(
                f"{s.original}: cited recipe {e.recipe_id} was not retrieved this turn"
            )
        elif s.replacement is not None and not _contains(e.recipe_id, s.replacement):
            errors.append(
                f"{s.original}: cited recipe {e.recipe_id} has no line with '{s.replacement}'"
            )
        if RANK[e.strength] < RANK[cand.grade]:
            errors.append(
                f"{s.original}: evidence strength '{e.strength}' is stronger than the verified grade '{cand.grade}'"
            )
    return errors


def _check_lines(
    a: AdaptedRecipe,
    targets: dict[str, TargetEvidence],
    groups: list[str],
    excluded: list[str],
    dataset_only: bool,
) -> list[str]:
    errors = []
    for line in a.ingredients:
        name = parse_line(line).name
        term = resolve_term(name)
        if why := violates(line, name, term.canonical, term.family, groups):
            errors.append(f"ingredient line '{line}' breaks the {why} restriction")
        if term.family in {resolve_term(x).family for x in excluded}:
            errors.append(
                f"ingredient line '{line}' contains an ingredient the user excluded"
            )
        if any(
            line_is_target(line, resolve_term(ev.target)) for ev in targets.values()
        ):
            errors.append(
                f"ingredient line '{line}' still contains a target ingredient"
            )
        if dataset_only and name and not term_has_lines(term):
            errors.append(
                f"ingredient '{name}' (line '{line}') is not an ingredient in the dataset"
            )
    return errors


# an amount with no cited recipe behind it must say it is an estimate
def _check_estimates(a: AdaptedRecipe) -> list[str]:
    errors = []
    for s in a.substitutions:
        if s.replacement is None or any(e.source == "local" for e in s.evidence):
            continue
        family = resolve_term(s.replacement).family
        for line in a.ingredients:
            if (
                resolve_term(parse_line(line).name).family == family
                and QUANTITY_ESTIMATE_MARK not in line.lower()
            ):
                errors.append(
                    f"ingredient line '{line}' has no cited source for its amount: mark it '({QUANTITY_ESTIMATE_MARK})'"
                )
    return errors


def validate(
    a: AdaptedRecipe,
    base_id: str,
    targets: dict[str, TargetEvidence],
    groups: list[str],
    excluded: list[str],
    dataset_only: bool,
) -> list[str]:
    errors = [] if a.recipe_id == base_id else [f"recipe_id must be {base_id}"]
    if a.can_adapt:
        errors += (
            []
            if a.ingredients and a.instructions
            else ["can_adapt is true but ingredients or instructions are empty"]
        )
        errors += _check_lines(
            a, targets, groups, excluded, dataset_only
        ) + _check_estimates(a)
        covered = {target_key(s.original) for s in a.substitutions}
        errors += [
            f"target '{ev.target}' has no substitution"
            for k, ev in targets.items()
            if k not in covered
        ]
    elif not a.uncertainty:
        errors.append("can_adapt is false: explain why in uncertainty")
    retrieved = {i for ev in targets.values() for i in ev.retrieved_ids}
    for s in a.substitutions:
        errors += _check_substitution(s, targets.get(target_key(s.original)), retrieved)
    text = " ".join(_texts(a)).lower()
    errors += [
        f"safety claim not allowed: '{p}'"
        for p in SAFETY_PHRASES
        if re.search(rf"\b{re.escape(p)}\b", text)
    ]
    return errors
