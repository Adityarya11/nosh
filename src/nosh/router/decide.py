"""Pure routing: (slots, coverage, session) -> action. The LLM only fills slots; this function picks the task."""

import re

from nosh.config import STATS_TOP_N, SUPPORTED_ATTRIBUTES
from nosh.router.actions import (
    Action,
    Clarify,
    Match,
    Search,
    SearchNoMatch,
    Stats,
    Substitute,
    Unsupported,
)
from nosh.router.slots import Slots
from nosh.session import SessionState
from nosh.tasks.search import Coverage
from nosh.tasks.stats import StatsRequest

ALLOWED = {
    "search": {"search", "search_no_match", "stats"},
    "match": {"match"},
    "substitute": {"substitute"},
}
KIND = {
    "count": "count",
    "percent": "count",
    "rank": "rank_steps",
    "mean": "mean_median",
    "median": "mean_median",
    "top_ingredients": "top_ingredients",
}
CLARIFY = (
    "What would you like to do? You can name a dish or ingredient to search, list what you have to match "
    "recipes, ask a question about the dataset, or ask to adapt a recipe."
)


def stats_request(slots: Slots, session: SessionState) -> StatsRequest:
    detail = (slots.aggregate_detail or "").lower()
    number = re.search(r"\b(\d+)\b", detail)
    any_mode = bool(re.search(r"\b(or|any)\b", detail))
    return StatsRequest(
        kind=KIND[slots.aggregate],
        all_of=[] if any_mode else slots.have_ingredients,
        any_of=slots.have_ingredients if any_mode else [],
        exclude_groups=list(session.filters.exclude_groups),
        exclude_terms=list(session.filters.exclude_ingredients),
        n=int(number.group(1)) if number else STATS_TOP_N,
        order="most" if "most" in detail else "fewest",
    )


def _constraint_only(slots: Slots) -> bool:
    changes = (
        slots.add_constraints
        or slots.drop_constraints
        or slots.max_steps
        or slots.preferences
    )
    return bool(changes) and not (
        slots.dish or slots.query_text or slots.have_ingredients
    )


def _search(slots: Slots, coverage: Coverage | None) -> Action:
    query = slots.dish or slots.query_text
    if (
        coverage is not None
        and coverage.level == "none"
        and query
        and not slots.have_ingredients
    ):
        nearest = coverage.hits
        offer = Substitute(recipe_id=nearest[0].id, goal=query) if nearest else None
        return SearchNoMatch(query=query, nearest=nearest, offer=offer)
    return Search(
        dish=slots.dish, query_text=slots.query_text, ingredients=slots.have_ingredients
    )


def _route(slots: Slots, coverage: Coverage | None, session: SessionState) -> Action:
    if slots.is_affirmation and session.pending_offer:
        return session.pending_offer
    if slots.ask_attribute and slots.ask_attribute.lower() not in SUPPORTED_ATTRIBUTES:
        return Unsupported(attribute=slots.ask_attribute)
    if slots.aggregate:
        return Stats(request=stats_request(slots, session))
    ref = slots.recipe_ref or session.active_recipe_id
    if slots.remove_ingredients and (slots.dish or ref):
        return Substitute(
            recipe_id=session.resolve_ref(slots.recipe_ref),
            dish=slots.dish,
            remove=slots.remove_ingredients,
            dataset_only=slots.dataset_only,
        )
    if len(slots.have_ingredients) >= 2:
        return Match(have=slots.have_ingredients)
    if (
        slots.is_followup
        and _constraint_only(slots)
        and session.active_action is not None
    ):
        return session.active_action
    if slots.dish or slots.query_text or slots.have_ingredients:
        return _search(slots, coverage)
    return Clarify(question=CLARIFY)


def decide(slots: Slots, coverage: Coverage | None, session: SessionState) -> Action:
    action = _route(slots, coverage, session)
    allowed = ALLOWED.get(session.mode)
    if (
        allowed is None
        or action.kind in allowed
        or action.kind in ("unsupported", "clarify")
    ):
        return action
    return Clarify(
        question=f"The mode is pinned to {session.mode}; that request needs a different task. "
        "Switch mode to auto to let me choose."
    )
