"""Deterministic Markdown rendering of results: every number shown comes from code, never the LLM."""

from nosh.config import NOTE_LABEL_MAX
from nosh.data.steps import no_instructions_note
from nosh.tasks.search import Candidate, Coverage, Hit, NearMiss, SearchResult
from nosh.tasks.stats import StatsResult


def bullets(notes: list[str]) -> str:
    out = []
    for n in notes:
        label, sep, rest = n.partition(": ")
        out.append(
            f"- **{label}:** {rest}"
            if sep and len(label) <= NOTE_LABEL_MAX
            else f"- {n}"
        )
    return "\n".join(out)


def _filters(r: StatsResult) -> str:
    req, parts = r.request, []
    if req.all_of:
        parts.append("contains ALL of: " + ", ".join(req.all_of))
    if req.any_of:
        parts.append("contains ANY of: " + ", ".join(req.any_of))
    parts += [f"excludes {g}" for g in req.exclude_groups + req.exclude_terms]
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
        return [
            f"{r.n_matched:,} recipes match ({pct:.2f}% of {r.n_total:,} usable recipes)."
        ]
    if req.kind == "top_ingredients":
        return [
            f"{i + 1}. **{row['family']}**: {row['recipes']:,} recipes ({100 * row['recipes'] / r.n_matched:.1f}%)"
            for i, row in enumerate(r.rows)
        ]
    if req.kind == "mean_median":
        return [f"Mean {r.mean:.2f} ingredients per recipe, median {r.median:g}."]
    head = f"**{req.order.capitalize()} steps, top {len(r.rows)}:**"
    return [head] + [
        f"{i + 1}. **[{row['id']}] {row['title']}**: {row['n_steps']} step{'' if row['n_steps'] == 1 else 's'}"
        for i, row in enumerate(r.rows)
    ]


def sub_recipe_note(count: int) -> str:
    noun, verb = ("recipe", "was") if count == 1 else ("recipes", "were")
    return (
        f"Sub-recipe lines: {count:,} {noun} matching your other filters {verb} excluded only because an ingredient "
        "line points to another recipe ('recipe follows', 'see below', ...) whose ingredients are not listed"
    )


def stats(r: StatsResult) -> str:
    if r.n_matched == 0:
        lines = ["No recipes match these filters."]
    else:
        lines = _answer(r)
    notes = [
        f"Filters applied: {_filters(r)}",
        f"Recipes analyzed: {r.n_matched:,}"
        + (f" ({r.n_ranked_pool:,} ranked)" if r.n_ranked_pool is not None else ""),
        f"Denominator: {r.n_total:,} usable recipes ({r.n_dropped:,} records dropped at build: no ingredient list)",
        f"Calculation: {_calculation(r)}",
    ]
    if r.n_sub_recipe_excluded is not None:
        notes.append(sub_recipe_note(r.n_sub_recipe_excluded))
    notes += [f"Normalization: {n}" for n in r.norm_notes]
    if r.step_rule:
        notes.append(
            f"Step rule {r.step_rule}: newline split, sentence fallback, duplicated text and boilerplate lines removed"
        )
        notes.append(no_instructions_note(r.n_no_instructions or 0))
    return "\n".join(lines) + "\n\n" + bullets(notes)


def _candidate(i: int, c: Candidate, r: SearchResult) -> list[str]:
    head = f"{i}. **[{c.id}] {c.title}** ({c.n_steps} step{'' if c.n_steps == 1 else 's'}, {c.n_ingredients} ingredient lines)"
    names = r.exclude_groups + r.exclude_terms
    if not names:
        return [head]
    checked = "; ".join(f"no {name} match in {c.n_ingredients} lines" for name in names)
    return [
        head,
        f"   - Ingredients: {', '.join(c.ingredients)}",
        f"   - Checked: {checked}",
    ]


def _search_filters(r: SearchResult) -> str:
    parts = [f'title: "{r.query}"'] if r.query else []
    parts += [f"has: {t}" for t in r.ingredients]
    parts += [f"excludes {g}" for g in r.exclude_groups + r.exclude_terms]
    if r.max_steps is not None:
        parts.append(f"at most {r.max_steps} steps")
    return "; ".join(parts) or "none"


def _violations(nm: NearMiss) -> str:
    found = [
        f'{line.group} ({line.level.replace("_", " ")}): "{line.raw_line}"'
        for line in nm.lines
    ]
    if nm.too_many_steps:
        found.append(f"more than the allowed steps ({nm.recipe.n_steps})")
    return "; ".join(found) or "violates a constraint"


def _evidence(r: SearchResult) -> list[str]:
    if not (r.exclude_groups or r.exclude_terms):
        return []
    return [
        "Evidence is a text match against the restriction lists and ingredient lines, not a safety guarantee: "
        "check product labels."
    ]


def _order(r: SearchResult) -> str:
    steps = "fewer steps (recipes without steps last), then recipe ID"
    if r.via == "fuzzy title" or not r.query:
        return f"Order: {steps}"
    return f"Order: title relevance (BM25), then {steps}"


def search(r: SearchResult, coverage: Coverage | None = None) -> str:
    lines: list[str] = []
    if coverage is not None and coverage.level == "near":
        lines.append(
            f'No title matches "{r.query}" exactly; these are the closest titles.'
        )
    if r.results:
        lines += [
            row for i, c in enumerate(r.results) for row in _candidate(i + 1, c, r)
        ]
    elif r.near_misses:
        lines.append(
            f"**No match found under your constraints.** {r.n_pool:,} recipes matched before the constraints."
        )
        lines.append(
            "Closest alternatives (these VIOLATE your constraints, listed with the line that does):"
        )
        lines += [
            f"{i + 1}. **[{nm.recipe.id}] {nm.recipe.title}**: {_violations(nm)}"
            for i, nm in enumerate(r.near_misses)
        ]
    else:
        lines.append("No recipes match.")
    notes = [
        f"Filters applied: {_search_filters(r)}",
        f"Matched: {r.n_matched:,} of {r.n_pool:,} recipes that match the search ({r.via}); showing {len(r.results)}",
        _order(r),
    ]
    notes += _evidence(r) if r.results else []
    if r.n_sub_recipe_excluded is not None:
        notes.append(sub_recipe_note(r.n_sub_recipe_excluded))
    return "\n".join(lines) + "\n\n" + bullets(notes)


def no_match(query: str, nearest: list[Hit], offer_made: bool) -> str:
    lines = [
        f'**No recipe in the dataset has a title that covers "{query}".** Nearest titles:'
    ]
    lines += [
        f"{i + 1}. **[{h.id}] {h.title}** (covers {h.score:.0%} of your words)"
        for i, h in enumerate(nearest)
    ]
    if offer_made:
        lines.append(
            f"I can adapt '{nearest[0].title}' toward '{query}' (labelled as an adaptation of an existing recipe). "
            "Reply yes to do that."
        )
    return "\n".join(lines)


def unsupported(attribute: str) -> str:
    return (
        f"**The dataset does not contain {attribute}.** It has title, ingredient lines, instructions, a picture link "
        "and the source file; I can search by name or ingredient and answer counts and rankings over those."
    )
