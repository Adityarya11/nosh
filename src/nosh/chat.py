"""One chat turn: extract slots, update the session, probe coverage, decide, execute, render."""

from nosh import render
from nosh.llm import LLMUnavailable
from nosh.router.actions import (
    Action,
    Clarify,
    Search,
    SearchNoMatch,
    Stats,
    Unsupported,
)
from nosh.router.decide import decide
from nosh.router.slots import Slots, extract_slots
from nosh.session import SessionState
from nosh.tasks.search import Coverage, probe, run_search
from nosh.tasks.stats import compute

NOT_BUILT = "This task is not available yet in this build."


def _coverage(slots: Slots) -> Coverage | None:
    query = slots.dish or slots.query_text
    return probe(query) if query and not slots.have_ingredients else None


def _search(action: Search, session: SessionState, coverage: Coverage | None) -> str:
    f = session.filters
    result = run_search(
        action.dish,
        action.query_text,
        action.ingredients,
        f.exclude_groups,
        f.exclude_ingredients,
        f.max_steps,
    )
    session.last_results = [c.id for c in result.results]
    session.last_titles = [c.title for c in result.results]
    return render.search(result, coverage)


def _execute(action: Action, session: SessionState, coverage: Coverage | None) -> str:
    if isinstance(action, Search):
        session.active_action = action
        return _search(action, session, coverage)
    if isinstance(action, Stats):
        session.active_action = action
        request = action.request.model_copy(
            update={
                "exclude_groups": list(session.filters.exclude_groups),
                "exclude_terms": list(session.filters.exclude_ingredients),
            }
        )
        return render.stats(compute(request))
    if isinstance(action, SearchNoMatch):
        session.active_action, session.pending_offer = action, action.offer
        return render.no_match(action.query, action.nearest, action.offer is not None)
    if isinstance(action, Unsupported):
        return render.unsupported(action.attribute)
    if isinstance(action, Clarify):
        return action.question
    return f"{NOT_BUILT} ({action.kind})"


def turn(text: str, session: SessionState) -> str:
    try:
        slots = extract_slots(text, session)
    except LLMUnavailable as exc:
        return str(exc)
    notes = session.apply_constraints(
        slots.add_constraints,
        slots.drop_constraints,
        slots.max_steps,
        slots.preferences,
    )
    coverage = _coverage(slots)
    action = decide(slots, coverage, session)
    session.pending_offer = (
        None  # an offer lives for one turn; a new no-match sets it again
    )
    body = _execute(action, session, coverage)
    task = "search" if action.kind == "search_no_match" else action.kind
    reply = "\n".join([session.status_line(task), body] + notes)
    session.remember("user", text)
    session.remember(
        "assistant", reply.splitlines()[1] if len(reply.splitlines()) > 1 else reply
    )
    return reply
