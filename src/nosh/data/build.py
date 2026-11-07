"""Build step: writes data/nosh.duckdb from the raw dataset, with no LLM calls."""

import json
import statistics
import time

import duckdb
import pandas as pd

from nosh.config import DATA_DIR, DB_PATH, MAP_VERSION, STEP_RULE
from nosh.data.ingredients import normalize, parse_line
from nosh.data.load import RawRecipe, load_all
from nosh.data.steps import split_steps


def _rows(recipes: list[RawRecipe]) -> tuple[list[dict], list[dict], int, int]:
    rec_rows, ing_rows, mapped, total = [], [], 0, 0
    for r in recipes:
        steps = split_steps(r.instructions)
        rec_rows.append(
            dict(id=r.id, source=r.source, title=r.title, instructions_raw=r.instructions,
                 steps_json=json.dumps(steps), n_steps=len(steps), n_ingredients=len(r.ingredient_lines))
        )
        for pos, line in enumerate(r.ingredient_lines):
            p = parse_line(line)
            canonical, family, hit = normalize(p.name)
            mapped += hit
            total += 1
            ing_rows.append(dict(recipe_id=r.id, pos=pos, raw_line=line, name=p.name, canonical=canonical,
                                 family=family, qty=p.qty, unit=p.unit))
    return rec_rows, ing_rows, mapped, total


def build() -> None:
    start = time.time()
    recipes, stats = load_all()
    rec_rows, ing_rows, mapped, total = _rows(recipes)
    DATA_DIR.mkdir(exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    con = duckdb.connect(str(DB_PATH))
    recipes_df, ing_df = pd.DataFrame(rec_rows), pd.DataFrame(ing_rows)
    con.execute("CREATE TABLE recipes AS SELECT * FROM recipes_df")
    con.execute("CREATE TABLE recipe_ingredients AS SELECT * FROM ing_df")
    con.execute("CREATE INDEX idx_ing_family ON recipe_ingredients(family)")
    con.execute("CREATE INDEX idx_ing_recipe ON recipe_ingredients(recipe_id)")
    con.execute("INSTALL fts")
    con.execute("LOAD fts")
    con.execute("PRAGMA create_fts_index('recipes', 'id', 'title', overwrite=1)")
    meta = {**stats, "step_rule": STEP_RULE, "map_version": MAP_VERSION, "usable_recipes": len(recipes),
            "ingredient_lines": total, "lines_mapped": mapped, "built_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    con.execute("CREATE TABLE build_meta(key VARCHAR, value VARCHAR)")
    con.executemany("INSERT INTO build_meta VALUES (?, ?)", [(k, str(v)) for k, v in meta.items()])
    con.close()
    _report(meta, rec_rows, time.time() - start)


def _report(meta: dict, rec_rows: list[dict], seconds: float) -> None:
    for s in ("ar", "epi", "fn"):
        print(f"{s:>3}: raw {meta[f'{s}_raw']:>6}  dropped {meta[f'{s}_dropped']:>4}  kept {meta[f'{s}_kept']:>6}")
    print(f"usable recipes: {meta['usable_recipes']}")
    print(f"ingredient lines: {meta['ingredient_lines']}, mapped via canonical_map: "
          f"{meta['lines_mapped'] / meta['ingredient_lines']:.1%} (map {MAP_VERSION})")
    steps = sorted(r["n_steps"] for r in rec_rows)
    print(f"steps ({STEP_RULE}): min {steps[0]}, median {statistics.median(steps)}, max {steps[-1]}, "
          f"zero-step recipes {sum(1 for s in steps if s == 0)}")
    print(f"built {DB_PATH} in {seconds:.0f}s")
