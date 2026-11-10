"""Raw loader: reads the three JSON files, cleans ads and headers, drops empty records."""

import json
from collections import Counter
from collections.abc import Iterator

from pydantic import BaseModel

from nosh.config import RAW_DIR, RAW_FILES, UNTITLED
from nosh.data.ingredients import clean_line, is_header


class RawRecipe(BaseModel):
    id: str
    source: str
    title: str
    ingredient_lines: list[str]
    instructions: str


def load_all() -> tuple[list[RawRecipe], Counter]:
    recipes: list[RawRecipe] = []
    stats: Counter = Counter()
    for source, filename in RAW_FILES.items():
        data = json.loads((RAW_DIR / filename).read_text(encoding="utf-8"))
        stats[f"{source}_raw"] = len(data)
        for rid, rec in data.items():
            recipe = _clean(rid, source, rec or {})
            if recipe is None:
                stats[f"{source}_dropped"] += 1
                continue
            recipes.append(recipe)
        stats[f"{source}_kept"] = stats[f"{source}_raw"] - stats[f"{source}_dropped"]
    return recipes, stats


def _clean(rid: str, source: str, rec: dict) -> RawRecipe | None:
    lines = [clean_line(x) for x in rec.get("ingredients") or []]
    lines = [x for x in lines if x and not is_header(x)]
    if not lines:
        return None
    title = " ".join((rec.get("title") or "").split()) or UNTITLED
    return RawRecipe(
        id=rid,
        source=source,
        title=title,
        ingredient_lines=lines,
        instructions=rec.get("instructions") or "",
    )


def iter_lines(recipes: list[RawRecipe]) -> Iterator[str]:
    for r in recipes:
        yield from r.ingredient_lines
