"""Pydantic models for the T3 adapted recipe (the LLM writes these; validate.py checks them)."""

from typing import Literal

from pydantic import BaseModel


class Evidence(BaseModel):
    source: Literal["local", "external"]
    recipe_id: str | None = None
    url: str | None = None
    strength: Literal["direct", "indirect"]
    note: str


class Substitution(BaseModel):
    original: str
    replacement: str | None
    role_covered: list[str]
    evidence: list[Evidence]
    expected_effects: str
    effects_status: Literal["verified_in_dataset", "predicted"]


class AdaptedRecipe(BaseModel):
    recipe_id: str
    substitutions: list[Substitution]
    ingredients: list[str]
    instructions: list[str]
    uncertainty: str | None = None
    can_adapt: bool
