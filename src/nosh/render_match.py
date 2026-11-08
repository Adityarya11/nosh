"""Deterministic rendering of T2 match results: definitions legend, per-recipe breakdown, ranking explanation."""

from nosh.render import sub_recipe_note
from nosh.tasks.match import MatchResult, MatchRow

LEGEND = ("Definitions: matched = your ingredients the recipe uses; missing = your ingredients it does not use; "
          "extra_required = recipe ingredients you did not list, split into non-staples (counted in the ranking) and "
          "pantry staples (shown, not counted). Matching is by normalized ingredient name; a quantity is compared only "
          "when both sides share a unit dimension, otherwise it says 'name-level match; quantity sufficiency not verified'.")
KEY_TEXT = {"matched": "most of your ingredients matched", "extra_n": "fewest non-staple extras",
            "steps_key": "fewest steps (recipes without steps last)", "id": "recipe ID"}


def _row(i: int, row: MatchRow, r: MatchResult, total: int) -> list[str]:
    head = f"{i}. [{row.id}] {row.title} ({row.n_steps} step{'' if row.n_steps == 1 else 's'})"
    lines = [head, f"   Matched ({len(row.matched)}/{total}): {', '.join(row.matched) or 'none'}",
             f"   Missing: {', '.join(row.missing) or 'none'}",
             f"   Extra non-staple ingredients ({len(row.extra_nonstaple)}): {', '.join(row.extra_nonstaple) or 'none'}",
             f"   Pantry staples needed ({len(row.extra_staple)}): {', '.join(row.extra_staple) or 'none'}"]
    lines += [f"   Quantity: {note}" for note in row.quantity_notes]
    names = r.exclude_groups + r.exclude_terms
    if names:
        lines.append("   Checked: " + "; ".join(f"no {n} match in {row.n_ingredients} lines" for n in names))
    return lines + [f"   Why here: {row.why}"]


def _violation(row: MatchRow) -> str:
    found = [f"{v.group} ({v.level.replace('_', ' ')}): \"{v.raw_line}\"" for v in row.violations]
    if row.too_many_steps:
        found.append(f"more than the allowed steps ({row.n_steps})")
    return "; ".join(found) or "violates a constraint"


def _filters(r: MatchResult) -> str:
    parts = [f"have: {', '.join(i.raw for i in r.items)}"] + [f"excludes {n}" for n in r.exclude_groups + r.exclude_terms]
    if r.max_steps is not None:
        parts.append(f"at most {r.max_steps} steps")
    return "; ".join(parts)


def _conflict_note(r: MatchResult) -> list[str]:
    excluded = set(r.exclude_groups + r.exclude_terms)
    clash = [i.name for i in r.items if i.name in excluded]
    return [f"Note: you listed {', '.join(clash)} and also excluded it, so every result is missing it."] if clash else []


def match(r: MatchResult) -> str:
    total = len(r.items)
    lines = [f"Could not read as an ingredient: {', '.join(r.skipped)}"] if r.skipped else []
    if r.rows:
        for i, row in enumerate(r.rows, 1):
            lines += _row(i, row, r, total)
    elif r.near_misses:
        lines.append(f"No match found under your constraints. {r.n_pool:,} recipes use at least one of your ingredients "
                     "before the constraints.")
        lines.append("Closest alternatives (these VIOLATE your constraints, listed with the violation):")
        lines += [f"{i}. [{row.id}] {row.title}: {_violation(row)}" for i, row in enumerate(r.near_misses, 1)]
    else:
        lines.append("No recipe uses any of those ingredients.")
    order = "; ".join(f"{i}) {KEY_TEXT[k]}" for i, k in enumerate(r.order, 1))
    notes = [f"Filters applied: {_filters(r)}",
             f"Candidates: {r.n_candidates:,} recipes use at least one of your ingredients and pass the constraints; showing {len(r.rows)}",
             f"Order: {order}", LEGEND] + _conflict_note(r)
    if r.n_sub_recipe_excluded is not None:
        notes.append(sub_recipe_note(r.n_sub_recipe_excluded))
    return "\n".join(lines) + "\n\n" + "\n".join(notes)
