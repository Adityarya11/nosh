"""Session state for one chat: filters, last results, pending offer and short history, with the update rules."""

import re
from typing import Literal

from pydantic import BaseModel

from nosh.config import HISTORY_TURNS, ORDINALS
from nosh.constraints import resolve
from nosh.data.ingredients import clean_name
from nosh.router.actions import Action, Substitute
from nosh.store import recipe_exists


class Filters(BaseModel):
    exclude_groups: list[str] = []
    exclude_ingredients: list[str] = []
    max_steps: int | None = None
    preferences: list[str] = []

    def describe(self) -> str:
        parts = [f"exclude {g}" for g in self.exclude_groups + self.exclude_ingredients]
        if self.max_steps is not None:
            parts.append(f"max {self.max_steps} steps")
        parts += self.preferences
        return ", ".join(parts) or "none"


class SessionState(BaseModel):
    mode: Literal["auto", "search", "match", "substitute"] = "auto"
    active_action: Action | None = None
    filters: Filters = Filters()
    last_results: list[str] = []
    last_titles: list[str] = []
    active_recipe_id: str | None = None
    pending_offer: Substitute | None = None
    history: list[tuple[str, str]] = []

    def apply_constraints(
        self, add: list[str], drop: list[str], max_steps: int | None, prefs: list[str]
    ) -> list[str]:
        notes = []
        for text in add:
            found = resolve(text)
            notes += [found.note] if found.note else []
            target = {
                "group": self.filters.exclude_groups,
                "ingredient": self.filters.exclude_ingredients,
            }.get(found.kind)
            if target is not None and found.value not in target:
                target.append(found.value)
        for text in drop:
            found = resolve(text)
            notes += [found.note] if found.kind == "group" and found.note else []
            for target in (
                self.filters.exclude_groups,
                self.filters.exclude_ingredients,
            ):
                if found.value in target:
                    target.remove(found.value)
            if (
                found.kind == "none"
                and clean_name(text) in self.filters.exclude_ingredients
            ):
                self.filters.exclude_ingredients.remove(clean_name(text))
        if max_steps is not None:
            self.filters.max_steps = max_steps
        self.filters.preferences += [
            p for p in prefs if p not in self.filters.preferences
        ]
        return notes

    def resolve_ref(self, ref: str | None) -> str | None:
        if not ref:
            return None
        low = ref.lower()
        for word, n in ORDINALS.items():
            if re.search(rf"\b{re.escape(word)}\b", low):
                return self.last_results[n - 1] if n <= len(self.last_results) else None
        if m := re.search(r"#\s*(\d+)\b", low):
            n = int(m.group(1))
            return (
                self.last_results[n - 1] if 1 <= n <= len(self.last_results) else None
            )
        if re.search(r"\b(this|it|that|current)\b", low):
            return self.active_recipe_id
        return ref.strip() if recipe_exists(ref.strip()) else None

    def remember(self, role: str, text: str) -> None:
        self.history = (self.history + [(role, text)])[-HISTORY_TURNS:]

    def status_line(self, task: str) -> str:
        return f"[task: {task} | filters: {self.filters.describe()}]"
