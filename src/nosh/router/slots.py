"""LLM slot extraction: one structured call per user turn; code, not the LLM, decides what to do with the slots."""

from typing import Literal

from pydantic import BaseModel

from nosh import llm
from nosh.config import PROMPTS_DIR, SLOTS_TEMPERATURE
from nosh.session import SessionState


class Slots(BaseModel):
    dish: str | None = None
    query_text: str | None = None
    have_ingredients: list[str] = []
    remove_ingredients: list[str] = []
    add_constraints: list[str] = []
    drop_constraints: list[str] = []
    max_steps: int | None = None
    preferences: list[str] = []
    aggregate: (
        Literal["count", "percent", "rank", "mean", "median", "top_ingredients"] | None
    ) = None
    aggregate_detail: str | None = None
    ask_attribute: str | None = None
    recipe_ref: str | None = None
    dataset_only: bool = False
    is_followup: bool = False
    is_affirmation: bool = False


def _context(session: SessionState, text: str) -> str:
    filters = session.filters
    lines = [
        f"Current filters: exclude {filters.exclude_groups or 'none'}, max steps {filters.max_steps}",
        "Last results: "
        + (
            " | ".join(f"{i + 1}. {t}" for i, t in enumerate(session.last_titles))
            or "none"
        ),
        "Recent turns:",
    ]
    lines += [f"  {role}: {content}" for role, content in session.history] or [
        "  (none)"
    ]
    return "\n".join(lines + [f"User message: {text}"])


def extract_slots(text: str, session: SessionState) -> Slots:
    system = (PROMPTS_DIR / "slots.txt").read_text(encoding="utf-8")
    return llm.structured(system, _context(session, text), Slots, SLOTS_TEMPERATURE)
