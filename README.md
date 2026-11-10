# nosh

Intent-first, not retrieval-first: the LLM turns the request into a typed spec, code runs exact searches over storage built from analysing this dataset, and the LLM writes only from the verified results.

A grounded recipe chatbot over the Recipe Box dataset (about 125,000 recipes). It does three things in one conversation: search and answer factual questions, match recipes to the ingredients you have, and adapt a recipe when an ingredient is unavailable or restricted, citing recipes from the dataset as evidence.

**The principle behind every design choice: the prompt tells the LLM what to do; the code checks that it did it.** The LLM extracts fields from what you say and writes text. Code does retrieval, filtering, counting, ranking, routing and validation. Every number you see (counts, percentages, steps, rankings) comes from SQL or Python, never from the model.

## Setup

**Requirements:** Python 3.11.9 or newer (`.python-version` pins 3.11.9) and [uv](https://docs.astral.sh/uv/), which creates the virtual environment and installs the locked dependencies (`uv.lock`).

```bash
# 0. Install uv and a matching Python if you do not have them (skip if `uv --version` works)
pip install uv            # or: https://docs.astral.sh/uv/getting-started/installation/
uv python install 3.11    # uv downloads Python 3.11 itself; no system Python needed
```

If you do not want to use uv, the plain-pip equivalent is below (not tested here; uv is the supported path). Then run the commands in the next block without the `uv run` prefix, for example `nosh build`.

```bash
python3.11 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
```

```bash
uv sync                          # install dependencies
uv run python scripts/get_data.py   # download recipes_raw/ (three JSON files)
cp .env.example .env             # then put your key and model name in .env
uv run nosh build                # build data/nosh.duckdb (about 3-4 minutes, no LLM, runs offline)
uv run nosh chat                 # terminal chat  (--mode auto|search|match|substitute)
uv run nosh ui                   # Streamlit app
uv run nosh eval                 # run evals/cases.yaml, write evals/results.md
```

### Folder structure (and where the dataset goes)

The dataset is not committed. `get_data.py` puts the three JSON files in `recipes_raw/` at the repository root, and `nosh build` writes the database into `data/`:

```text
nosh/
  recipes_raw/                          # created by scripts/get_data.py (gitignored, ~205 MB)
    recipes_raw_nosource_ar.json        #   Allrecipes   (source "ar")
    recipes_raw_nosource_epi.json       #   Epicurious   (source "epi")
    recipes_raw_nosource_fn.json        #   Food Network (source "fn")
  data/                                 # created by `nosh build` (gitignored)
    nosh.duckdb                         #   the database (~480 MB)
    llm_cache/                          #   cached LLM calls (created on first chat)
  resources/  src/nosh/  evals/  docs/  scripts/  .env  (your keys, never committed)
```

The three file names must match exactly, because the build looks for them by name (`RAW_FILES` in `src/nosh/config.py`). Running `uv run python scripts/get_data.py <folder>` unpacks them into another folder, but `nosh build` only reads `recipes_raw/`.

- **Dataset:** `get_data.py` downloads `recipes_raw.zip` from `DATASET_URL` in `src/nosh/config.py` (https://eightportions.com/recipes_raw.zip, 53 MB) and unpacks the three `recipes_raw_nosource_*.json` files into `recipes_raw/`. `python scripts/get_data.py <folder>` unpacks somewhere else.
- **LLM provider:** Gemini is the default (`GEMINI_API_KEY`, `NOSH_GEMINI_MODEL`). The Gemini free tier allows only about 20 requests per day per model, and one substitution turn uses 4-6 calls, so use Anthropic as the fallback: `NOSH_LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, `NOSH_ANTHROPIC_MODEL=claude-haiku-5-5`. Current model names: the provider docs linked in `.env.example`.
- **Cache:** every LLM call is cached in `data/llm_cache/` (keyed by provider, model, prompt and input), so repeated runs and the evals replay without new requests.
- **Without a key:** build, stats and the structured paths still work; chat tells you the key is missing.
- **Machine safety:** the build and live runs can be heavy on a small laptop. See `AGENTS.md` section 13 (DuckDB is capped at 2 GB; run heavy jobs under `scripts/guard.py`).

## How it works

1. **Build once (`nosh build`, no LLM).** The three JSON files are cleaned (ads removed, records without ingredients dropped and counted), steps are counted, and every ingredient line is parsed into quantity, unit and name, then normalized to a `canonical` product ("heavy cream") and a `family` ("cream") through `resources/canonical_map.json` (84.9% of the 1,273,589 ingredient lines map directly; the rest fall back to the parsed name). Everything goes into one DuckDB file with a full-text index on titles. 122,968 recipes are usable; 2,196 records were dropped for having no ingredient list, and that number is part of every denominator statement.
2. **Restrictions are data.** `resources/restrictions.json` lists, per group (dairy, egg, gluten, nuts, peanut, soy, shellfish, fish, pork, vegetarian, vegan), banned families and products, definite and "may contain" keywords, and safe phrases. Each line gets a per-group tag at build time, a raw-line scan catches what the parser loses, and a line like "recipe follows" counts as may-contain for every group. Hard constraints are applied in SQL before any text is generated and checked again on the output.
3. **One conversation turn.** The LLM turns your message into slots (dish, ingredients, constraints, ...). The pure function `decide()` picks the task from the slots, the session and a title-coverage probe. The task runs in code; `render*.py` turns the result into Markdown. Every reply starts with a status line (task, active filters).
4. **T1 search and stats.** Title search is BM25 with a fuzzy fallback; stats are SQL over the complete filtered set and always state filters, number analyzed, denominator, calculation, normalization and step rule.
5. **T2 matching.** Hard filters first, then a lexicographic order: more of your ingredients matched, fewer non-staple extras (pantry staples are shown, not counted), fewer steps, recipe ID. Preferences reorder only the keys below "matched". Each result says why it is where it is.
6. **T3 substitution: the LLM proposes, the data verifies.** The LLM tags what the ingredient does in this recipe and proposes substitutes; code adds substitutes from the data (explicit "X or Y" lines, ingredients over-represented in similar dishes without the target) and grades each as direct, indirect or none against similar dishes (WITH vs WITHOUT the target, with counts), after the restriction check. The LLM writes the adapted recipe from that evidence only; validators check restrictions, that every cited recipe was retrieved and contains the substitute, evidence strength, safety-claim phrases and that amounts are scaled from cited recipes or marked "(estimate)". On failure it regenerates (max 2 retries) and otherwise refuses honestly.
7. **Step rule v2** (`src/nosh/data/steps.py`): duplicated Epicurious text is halved, instructions split on newlines, then sentences when one block remains; boilerplate lines are dropped; recipes with no instructions are never ranked as "fewest steps". The rule version is stated in every answer that uses it.

The reasoning behind each choice, including the ones that were reversed, is in [`docs/decisions.md`](docs/decisions.md) (D1-D29); every bug found while testing against real data, with the numbers, is in [`docs/findings.md`](docs/findings.md) (F1-F32). The stack choices are in [`docs/stack.md`](docs/stack.md). The spec and working rules are in [`AGENTS.md`](AGENTS.md).

## Evaluation

[`evals/results.md`](evals/results.md) is the latest run of `uv run nosh eval`: nine conversations (the spec's penne, garlic+onion, tomato, eggs/onions/tomatoes/rice with "Exclude eggs too", banana bread, cream pasta; a no-match; an unsupported attribute; a weak-evidence substitution) and ten routing cases. Computed numbers are compared with independent recomputations; cited recipe IDs are re-checked against the dataset. All 9 conversations and 10 routing cases pass in the last run. [`evals/baseline.md`](evals/baseline.md) puts one plain LLM call next to nosh on the garlic+onion question: the one-shot model answered 31,250 recipes (25%); the real answer is 15,877 (12.91% of 122,968 usable recipes).

## Limitations

- **Allergen and diet detection is vocabulary based** (restriction lists, canonical tags, a raw-line scan, sub-recipe lines treated as may-contain). The independent probe scan finds 0 unflagged lines in the build, but a vocabulary can never prove zero. nosh never claims a substitution is safe for an allergy or medical condition; check product labels.
- **T3 evidence is often thin.** The dataset has 8 egg-free banana breads out of 139 and no gelatin-free panna cotta, so many answers are labelled "predicted, no local evidence". The validators do not check quantities against the cited recipes (F29), and the role of an ingredient is tagged by the LLM and only used in the prompts, not verified in code.
- **Dairy safe phrases are narrow.** "cashew cream" and "vegan sour cream" are accepted; "plant-based milk" and "dairy-free cheese" are still flagged (F31).
- **The dataset has no nutrition, cooking time, servings, cuisine or ratings.** Asking for them gets an explicit "the dataset does not contain X". Times mentioned in instruction text can only be quoted.
- **Coverage thresholds** (exact 0.99, near 0.70) were calibrated on eight probe queries.
- **Equipment lines** ("wooden skewer") are parsed as ingredients and count as extras in T2 for about 1.6% of recipes (D23).
- **The Gemini path** is the default but the evals were replayed with Anthropic because of the free-tier quota; the Anthropic structured-output path is the one exercised live.
- **UI:** the Streamlit app is minimal and was tested headless (Streamlit AppTest), not in a browser.

## AI-tool disclosure

All code in this repository was written by Claude Code (Anthropic's CLI agent). The spec (`AGENTS.md`), the direction, the decisions and the review of every checkpoint are mine: I specified the work step by step, stopped at each checkpoint, read the real output, and sent back corrections that are recorded in `docs/findings.md`. The LLMs inside the product (Gemini or Anthropic) are used only for slot extraction, role tagging, substitute proposals and adapted-recipe text; they never produce the numbers shown. No orchestration or memory framework is used.
