"""Deterministic text rendering of results: every number shown comes from code, never the LLM."""

from nosh.data.steps import no_instructions_note
from nosh.tasks.stats import StatsResult


def _filters(r: StatsResult) -> str:
    req, parts = r.request, []
    if req.all_of:
        parts.append("contains ALL of: " + ", ".join(req.all_of))
    if req.any_of:
        parts.append("contains ANY of: " + ", ".join(req.any_of))
    parts += [f"excludes {g}" for g in req.exclude_groups]
    return "; ".join(parts) or "none (whole dataset)"


def _calculation(r: StatsResult) -> str:
    req = r.request
    if req.kind == "count":
        return f"count = recipes matching the filters; percent = count / {r.n_total:,} x 100"
    if req.kind == "top_ingredients":
        return f"recipes containing each ingredient family among the {r.n_matched:,} matching recipes; ties broken by name A-Z"
    if req.kind == "mean_median":
        return f"mean and median of ingredient-line count over the {r.n_matched:,} matching recipes"
    return f"{req.order} {req.n} by n_steps; ties broken by recipe ID (ascending)"


def _answer(r: StatsResult) -> list[str]:
    req = r.request
    if req.kind == "count":
        pct = 100 * r.n_matched / r.n_total
        return [f"{r.n_matched:,} recipes match ({pct:.2f}% of {r.n_total:,} usable recipes)."]
    if req.kind == "top_ingredients":
        return [f"{i + 1}. {row['family']}: {row['recipes']:,} recipes ({100 * row['recipes'] / r.n_matched:.1f}%)"
                for i, row in enumerate(r.rows)]
    if req.kind == "mean_median":
        return [f"Mean {r.mean:.2f} ingredients per recipe, median {r.median:g}."]
    head = f"{req.order.capitalize()} steps, top {len(r.rows)}:"
    return [head] + [f"{i + 1}. [{row['id']}] {row['title']}: {row['n_steps']} step{'' if row['n_steps'] == 1 else 's'}" for i, row in enumerate(r.rows)]


def sub_recipe_note(count: int) -> str:
    return (f"Sub-recipe lines: {count:,} recipes matching your other filters were excluded only because an ingredient "
            "line points to another recipe ('recipe follows', 'see below', ...) whose ingredients are not listed")


def stats(r: StatsResult) -> str:
    if r.n_matched == 0:
        lines = ["No recipes match these filters."]
    else:
        lines = _answer(r)
    notes = [f"Filters applied: {_filters(r)}",
             f"Recipes analyzed: {r.n_matched:,}"
             + (f" ({r.n_ranked_pool:,} ranked)" if r.n_ranked_pool is not None else ""),
             f"Denominator: {r.n_total:,} usable recipes ({r.n_dropped:,} records dropped at build: no ingredient list)",
             f"Calculation: {_calculation(r)}"]
    if r.n_sub_recipe_excluded is not None:
        notes.append(sub_recipe_note(r.n_sub_recipe_excluded))
    notes += [f"Normalization: {n}" for n in r.norm_notes]
    if r.step_rule:
        notes.append(f"Step rule {r.step_rule}: newline split, sentence fallback, duplicated text and boilerplate lines removed")
        notes.append(no_instructions_note(r.n_no_instructions or 0))
    return "\n".join(lines) + "\n\n" + "\n".join(notes)
