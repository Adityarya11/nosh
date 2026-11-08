"""Action models returned by decide(): what the code will do for a turn."""

from typing import Literal

from pydantic import BaseModel

from nosh.tasks.search import Hit
from nosh.tasks.stats import StatsRequest


class Search(BaseModel):
    kind: Literal["search"] = "search"
    dish: str | None = None
    query_text: str | None = None
    ingredients: list[str] = []


class Substitute(BaseModel):
    kind: Literal["substitute"] = "substitute"
    recipe_id: str | None = None
    dish: str | None = None
    remove: list[str] = []
    goal: str | None = None


class SearchNoMatch(BaseModel):
    kind: Literal["search_no_match"] = "search_no_match"
    query: str
    nearest: list[Hit]
    offer: Substitute | None = None


class Stats(BaseModel):
    kind: Literal["stats"] = "stats"
    request: StatsRequest


class Match(BaseModel):
    kind: Literal["match"] = "match"
    have: list[str]


class Unsupported(BaseModel):
    kind: Literal["unsupported"] = "unsupported"
    attribute: str


class Clarify(BaseModel):
    kind: Literal["clarify"] = "clarify"
    question: str


Action = Search | SearchNoMatch | Stats | Match | Substitute | Unsupported | Clarify
