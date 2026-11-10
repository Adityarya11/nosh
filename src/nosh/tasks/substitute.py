"""T3 pipeline: resolve the base recipe, LLM roles and proposals, code verification, LLM adaptation, validation with retries."""

import json

from pydantic import BaseModel

from nosh import llm
from nosh.config import (
    ADAPT_TEMPERATURE,
    BASE_POOL,
    MAX_RETRIES,
    MAX_TARGETS,
    PROMPTS_DIR,
    SUPPORT_SHOWN,
)
from nosh.router.actions import Substitute
from nosh.session import Filters
from nosh.store import connect
from nosh.tasks.adapted import AdaptedRecipe
from nosh.tasks.evidence import similar_dishes, violates
from nosh.tasks.propose import RoleTags, propose, recipe_text, tag_roles
from nosh.tasks.targets import target_sql
from nosh.tasks.validate import validate
from nosh.tasks.verify import TargetEvidence, target_key, verify_target


class Base(BaseModel):
    id: str
    title: str
    lines: list[str]
    steps: list[str]
    instructions: str


class SubstituteResult(BaseModel):
    base: Base | None = None
    note: str | None = None
    error: str | None = None
    targets: dict[str, list[str]] = {}
    roles: RoleTags | None = None
    evidence: list[TargetEvidence] = []
    adapted: AdaptedRecipe | None = None
    errors: list[str] = []
    attempts: int = 0
    rejected: list[list[str]] = []
    groups: list[str] = []
    dataset_only: bool = False


def _load(recipe_id: str) -> Base | None:
    row = (
        connect()
        .execute(
            "SELECT title, instructions_raw, steps_json FROM recipes WHERE id = ?",
            [recipe_id],
        )
        .fetchone()
    )
    if row is None:
        return None
    lines = [
        r[0]
        for r in connect()
        .execute(
            "SELECT raw_line FROM recipe_ingredients WHERE recipe_id = ? ORDER BY pos",
            [recipe_id],
        )
        .fetchall()
    ]
    return Base(
        id=recipe_id,
        title=row[0],
        lines=lines,
        steps=json.loads(row[2] or "[]"),
        instructions=row[1] or "",
    )


# the targets: every line of the removed ingredient, plus every other line that breaks a restriction (one target per name)
def _targets(recipe_id: str, remove: str, groups: list[str]) -> dict[str, list[str]]:
    hits, params = target_sql(remove)
    con = connect()
    main = [
        r[0]
        for r in con.execute(
            f"SELECT i.raw_line FROM recipe_ingredients i WHERE i.recipe_id = ? AND {hits} ORDER BY i.pos",
            [recipe_id, *params],
        ).fetchall()
    ]
    out: dict[str, list[str]] = {remove: main} if main else {}
    for raw, name, canonical, family in con.execute(
        "SELECT raw_line, name, canonical, family FROM recipe_ingredients WHERE recipe_id = ? ORDER BY pos",
        [recipe_id],
    ).fetchall():
        if (
            raw not in main
            and canonical
            and violates(raw, name, canonical, family, groups)
        ):
            out.setdefault(canonical, []).append(raw)
    return out


# recipe ids (in the given order) that have a line of the target itself
def _with_target(ids: list[str], remove: str) -> list[str]:
    hits, params = target_sql(remove)
    found = {
        r[0]
        for r in connect()
        .execute(
            f"SELECT DISTINCT i.recipe_id FROM recipe_ingredients i WHERE i.recipe_id IN (SELECT unnest(?)) AND {hits}",
            [ids, *params],
        )
        .fetchall()
    }
    return [i for i in ids if i in found]


def _resolve_base(
    action: Substitute, remove: str, groups: list[str]
) -> tuple[Base | None, dict[str, list[str]], str | None, str | None]:
    if action.recipe_id:
        base = _load(action.recipe_id)
        if base is None:
            return None, {}, None, f"I could not find recipe {action.recipe_id}."
        targets = _targets(base.id, remove, groups)
        return (
            (base, targets, None, None)
            if remove in targets
            else (
                None,
                {},
                None,
                f"Recipe {base.id} ({base.title}) has no '{remove}' line to replace.",
            )
        )
    if not action.dish:
        return (
            None,
            {},
            None,
            "Which recipe should I adapt? Name a dish or pick one from the last results.",
        )
    options = []
    for rid in _with_target(similar_dishes(action.dish)[0], remove)[:BASE_POOL]:
        if remove in (t := _targets(rid, remove, groups)):
            options.append((rid, t))
    if not options:
        return (
            None,
            {},
            None,
            f"No '{action.dish}' recipe with a '{remove}' line was found to adapt.",
        )
    chosen, targets = min(options, key=lambda o: len(o[1]))
    base = _load(chosen)
    note = (
        f"Several '{action.dish}' recipes use {remove}; I used the one needing the fewest replacements: "
        f"[{base.id}] {base.title}."
    )
    return base, targets, note, None


# the full ingredient lines of every citable recipe, so amounts can be scaled from them
def _quantity_context(evidence: list[TargetEvidence]) -> list[str]:
    ids = sorted(
        {
            s.recipe_id
            for ev in evidence
            for c in ev.candidates
            for s in c.support[:SUPPORT_SHOWN]
        }
    )
    if not ids:
        return []
    rows = (
        connect()
        .execute(
            "SELECT recipe_id, raw_line FROM recipe_ingredients WHERE recipe_id IN (SELECT unnest(?)) ORDER BY recipe_id, pos",
            [ids],
        )
        .fetchall()
    )
    out = ["QUANTITY CONTEXT (ingredient lines of the citable recipes):"]
    for rid in ids:
        out.append(f"  [{rid}]: " + " | ".join(line for r, line in rows if r == rid))
    return out


def _adapt_input(
    base: Base,
    evidence: list[TargetEvidence],
    groups: list[str],
    dataset_only: bool,
    errors: list[str],
) -> str:
    parts = (
        [f"Base recipe [{base.id}] {base.title}", "Ingredient lines:"]
        + [f"- {x}" for x in base.lines]
        + ["Steps:"]
    )
    parts += [f"{i}. {s}" for i, s in enumerate(base.steps, 1)]
    parts += [
        f"Restrictions: {', '.join(groups) or 'none'}",
        f"Only dataset ingredients: {'yes' if dataset_only else 'no'}",
        "VERIFIED CANDIDATES:",
    ]
    for ev in evidence:
        parts.append(f"Target: {ev.target}")
        for c in ev.candidates:
            parts.append(
                f"  - {c.name}: grade {c.grade} ({c.reason}); " + "; ".join(c.basis)
            )
            parts += [
                f"      cite [{s.recipe_id}] {s.title}: {s.raw_line}"
                for s in c.support[:SUPPORT_SHOWN]
            ]
            parts += [f"      origin: {c.origin}"]
        parts += [f"  - (not allowed) {n}: {why}" for n, why in ev.filtered]
    parts += _quantity_context(evidence)
    if errors:
        parts += ["Validation errors from your previous attempt, fix all of them:"] + [
            f"- {e}" for e in errors
        ]
    return "\n".join(parts)


def run_substitute(action: Substitute, filters: Filters) -> SubstituteResult:
    groups, excluded = list(filters.exclude_groups), list(filters.exclude_ingredients)
    result = SubstituteResult(groups=groups, dataset_only=action.dataset_only)
    if not action.remove:
        result.error = "Which ingredient should I replace or remove?"
        return result
    remove = action.remove[0]
    base, targets, result.note, result.error = _resolve_base(action, remove, groups)
    if base is None:
        return result
    result.base, result.targets = base, targets
    if len(targets) > MAX_TARGETS:
        result.error = f"[{base.id}] {base.title} needs {len(targets)} replacements, more than the {MAX_TARGETS} I handle at once."
        return result
    text = recipe_text(base.title, base.lines, base.instructions)
    result.roles = tag_roles(text, list(targets))
    proposals = {
        target_key(p.ingredient): p.substitutes
        for p in propose(text, result.roles, groups, action.dataset_only).proposals
    }
    dish = action.dish or base.title
    result.evidence = [
        verify_target(
            dish,
            t,
            result.roles.dish_classes,
            proposals.get(target_key(t), []),
            groups,
            excluded,
            action.dataset_only,
        )
        for t in targets
    ]
    by_target = {target_key(ev.target): ev for ev in result.evidence}
    if any(not ev.candidates for ev in result.evidence):
        result.error = (
            "No usable substitutes were found for: "
            + ", ".join(ev.target for ev in result.evidence if not ev.candidates)
            + " (every proposal was rejected or none was made)."
        )
        return result
    system = (PROMPTS_DIR / "adapt.txt").read_text(encoding="utf-8")
    errors: list[str] = []
    for attempt in range(1, MAX_RETRIES + 2):
        adapted = llm.structured(
            system,
            _adapt_input(base, result.evidence, groups, action.dataset_only, errors),
            AdaptedRecipe,
            ADAPT_TEMPERATURE,
        )
        errors = validate(
            adapted, base.id, by_target, groups, excluded, action.dataset_only
        )
        result.attempts = attempt
        if errors:
            result.rejected.append(errors)
        if not errors:
            result.adapted, result.errors = adapted, []
            return result
    result.errors = errors
    return result
