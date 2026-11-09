"""T3 LLM steps: role-tag the targets in this recipe, then propose replacement names; code verifies everything later."""

from typing import Literal

from pydantic import BaseModel

from nosh import llm
from nosh.config import PROMPTS_DIR, PROPOSE_TEMPERATURE, RECIPE_TEXT_CHARS, ROLES, ROLES_TEMPERATURE

Role = Literal["binding", "moisture", "leavening", "fat", "thickening", "flavor", "texture", "main_protein"]


class TargetRoles(BaseModel):
    ingredient: str
    roles: list[Role]


class RoleTags(BaseModel):
    targets: list[TargetRoles]
    dish_classes: list[str]


class TargetProposals(BaseModel):
    ingredient: str
    substitutes: list[str]


class Proposals(BaseModel):
    proposals: list[TargetProposals]


def recipe_text(title: str, lines: list[str], instructions: str) -> str:
    return "\n".join([f"Title: {title}", "Ingredient lines:"] + [f"- {x}" for x in lines]
                     + ["Instructions (start):", instructions[:RECIPE_TEXT_CHARS]])


def tag_roles(text: str, targets: list[str]) -> RoleTags:
    system = (PROMPTS_DIR / "roles.txt").read_text(encoding="utf-8")
    user = f"{text}\nTargets: {', '.join(targets)}\nAllowed roles: {', '.join(ROLES)}"
    return llm.structured(system, user, RoleTags, ROLES_TEMPERATURE)


def propose(text: str, tags: RoleTags, groups: list[str], dataset_only: bool) -> Proposals:
    system = (PROMPTS_DIR / "propose.txt").read_text(encoding="utf-8")
    roles = "\n".join(f"- {t.ingredient}: {', '.join(t.roles)}" for t in tags.targets)
    user = (f"{text}\nTargets and roles:\n{roles}\nRestrictions: {', '.join(groups) or 'none'}\n"
            f"Only dataset ingredients: {'yes' if dataset_only else 'no'}")
    return llm.structured(system, user, Proposals, PROPOSE_TEMPERATURE)
