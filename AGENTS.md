# AGENTS.md: nosh, a grounded recipe chatbot

This file is the spec and the working rules for anyone writing code in this repo, human or AI.
Read it fully before writing code. Design rationale lives in `docs/decisions.md` (D1-D16); do not
re-decide what is recorded there. If something here is ambiguous, pick the simplest option that
satisfies the assignment, implement it, and record the choice as a new entry in `docs/decisions.md`.

## 1. What we are building

A conversational chatbot over the Recipe Box dataset (three JSON files in `recipes_raw/`,
~125k recipes) that does three graded tasks in one session:

| Task                    | Marks | One line                                                                                                             |
| ----------------------- | ----- | -------------------------------------------------------------------------------------------------------------------- |
| T1 Search + factual Q&A | 3/10  | Find recipes by name, ingredient or natural language; answer stats questions by computing over the full filtered set |
| T2 Ingredient matching  | 3/10  | Rank existing recipes against "what I have" + hard constraints + preferences, with an explainable score              |
| T3 Substitution         | 4/10  | Adapt an existing recipe when an ingredient is unavailable or restricted, citing local recipe IDs as evidence        |

The assignment text is the source of truth for requirements (`AIInternTask.pdf`, local only, never
committed). Its key rules are restated in section 7 so you don't need the PDF.

**Core principle: the prompt tells the LLM what to do; the code checks that it did it.**
The LLM extracts fields from what the user said and writes text. Code does retrieval, filtering,
counting, ranking, routing and validation. Numbers shown to the user always come from code.

The author (Aditya) will walk through this code in a live interview and modify it on request.
Every module must be readable and explainable in a few sentences. Clarity beats cleverness.

## 2. Hard rules (non-negotiable)

1. **No frameworks for orchestration or memory.** No LangChain, LlamaIndex, LangGraph, mem0,
   Hindsight, Cognee, agent frameworks. Plain Python + pydantic + one thin LLM client (D3, D4). These are decided based on the scope of the assignment, not a chosen hard way. You may counter the fact to use it, otherwise.
2. **LLMs never produce numbers** that are presented as facts (counts, percentages, step counts,
   rankings, scores). Code computes them; templates render them.
3. **Aggregates use the complete filtered set** from the database, never semantic top-k.
4. **Never fabricate unsupported attributes.** The dataset has: title, ingredient lines, instructions,
   picture link, source file. It has NO nutrition, cooking time, servings, cuisine labels, ratings.
   Asking for those gets an explicit "the dataset does not contain X" answer. Times mentioned inside
   instruction text may be quoted verbatim with the recipe ID, labelled as quoted from instructions.
5. **Never relax a hard constraint silently.** If nothing satisfies it, say "no match" first; show
   near-misses only in a separate, clearly labelled section listing which constraint each violates.
6. **Never claim a substitution is medically or allergy safe.** Tell users to check product labels.
7. **Every recipe shown carries its stable ID** (the JSON key) and original title.
8. **External information is always labelled external**, with a URL if from the web.
9. **Hard constraints are checked in code** against the restriction map, before the LLM sees
   candidates and again after it answers.
10. **Cited recipe IDs are validated**: a cited ID must have been retrieved this turn and must contain
    the cited ingredient. Fail = regenerate (max 2 retries) or refuse.

## 3. Code norms

- **File header:** every `.py` file starts with exactly one line saying what the file does, e.g.
  `"""Ingredient line parser: strips quantities, units and prep notes."""`. Nothing more.
- **No triple-quoted prose blocks anywhere.** No multi-line docstrings, no essay comments. Functions
  get no docstring or a single-line one. Comments are single lines that explain _why_, only where
  the why is not obvious.
- **Prompts** live in `src/nosh/prompts/*.txt` as plain text files, not as triple-quoted strings in code.
- Small units: files ideally under ~200 lines, functions under ~40. Functions over classes unless
  state demands a class. Type hints everywhere. pydantic models at boundaries (LLM I/O, session, actions).
- **No magic numbers in logic.** Thresholds, weights, top-k, retry counts, temperatures live in
  `src/nosh/config.py`. Domain knowledge (canonical names, restriction groups, pantry staples, units)
  lives in JSON files under `resources/`. Changing behaviour should mean editing data or config,
  not logic.
- No dead code, no speculative abstractions, no features beyond this spec.
- Errors: fail loudly at build time; at chat time, degrade to an honest message, never a stack trace
  in the UI.
- Dependencies: keep the list short. Expected: `duckdb`, `pydantic`, `google-genai`, `anthropic`
  (optional provider), `python-dotenv`, `rapidfuzz` (fuzzy title matching), `streamlit`, `pyyaml`.
  Anything else needs a one-line justification in `docs/decisions.md`. Use `uv add`, never pip.

## 4. Repo layout

```
nosh/
  AGENTS.md  CLAUDE.md  README.md  pyproject.toml  uv.lock  .env.example
  recipes_raw/                 # raw dataset (gitignored; README explains download)
  data/                        # built artifacts: nosh.duckdb, llm_cache/ (gitignored)
  resources/                   # committed domain data (JSON), edited by hand or one-off scripts
    canonical_map.json         # raw ingredient name -> {canonical, family}
    restrictions.json          # dairy/egg/gluten/nuts/peanut/vegetarian/... -> families + product names
    pantry.json                # staples excluded from "extra required" counts (shown, not hidden)
    units.json                 # unit aliases + conversion within mass / volume
  src/nosh/
    config.py                  # all tunables
    cli.py                     # entry point: build | chat | ui | eval
    llm.py                     # provider wrapper (Gemini default, Anthropic optional), JSON cache
    prompts/                   # slots.txt, roles.txt, adapt.txt, phrase.txt
    data/load.py               # read the 3 JSON files, clean, assign source
    data/steps.py              # step-counting rule
    data/ingredients.py        # line parser + normalization via canonical_map
    data/build.py              # writes data/nosh.duckdb
    store.py                   # read-only query helpers over DuckDB
    restrictions.py            # expand a restriction to banned families/products; check a recipe
    session.py                 # SessionState model + update rules
    router/slots.py            # LLM slot extraction (Slots model)
    router/decide.py           # pure function: (Slots, Coverage, SessionState) -> Action
    tasks/search.py            # T1 search + coverage probe
    tasks/stats.py             # T1 aggregates
    tasks/match.py             # T2 scoring + ranking
    tasks/substitute.py        # T3 pipeline
    tasks/evidence.py          # T3 contrastive + explicit evidence mining
    tasks/validate.py          # T3 output validators
    render.py                  # deterministic text/table rendering of results
    ui/app.py                  # Streamlit app
  evals/
    cases.yaml                 # graded eval set (committed deliverable)
    run.py                     # runs cases, writes evals/results.md
    results.md                 # latest run, committed
    baseline.md                # one-shot LLM vs nosh comparison, committed
  docs/decisions.md
  scripts/get_data.py          # downloads + unpacks the dataset into recipes_raw/
```

`pyproject.toml` needs a build system and `[project.scripts] nosh = "nosh.cli:main"` so that
`uv run nosh ...` works. Check that all dependencies install on the pinned Python (`.python-version`
is 3.11).

## 5. Data layer (build once: `uv run nosh build`)

Build must be deterministic and must NOT call an LLM, so graders can rebuild offline in a few minutes.

**Load + clean**

- Read all three files. Keep source (`ar`, `epi`, `fn`). ID = JSON key, unchanged.
- Drop records with no ingredients; count them and store the exclusion counts (they are reported
  as part of every denominator statement).
- Strip `ADVERTISEMENT` tokens and empty lines from ingredient lists. Keep the original lines as
  `raw_line` for display and evidence.
- Missing titles: keep the recipe, title = `"(untitled)"`.
- Keep original title, ingredient lines and instructions verbatim (cleaned of ads only) for display.

**Step-counting rule** (`data/steps.py`, versioned as `STEP_RULE = "v1"`, stated in every answer that uses it)

1. Remove exact duplication: many Epicurious texts are the instruction text repeated twice.
   If the text is two identical halves (after whitespace normalization), keep one half.
2. Split on newlines; drop empty lines and boilerplate (e.g. `Watch how to make this recipe.`).
3. If that yields one block, split it into sentences; each sentence is a step.
4. Store `n_steps` and the step list.

**Ingredient parsing + normalization** (`data/ingredients.py`)

- Regex parser per line: leading quantity (ints, fractions, unicode fractions, ranges), parenthetical
  sizes `(10.75 ounce)`, units (from `units.json`), then the name; drop prep notes after the first comma
  and descriptor words (chopped, fresh, large, ...), keeping descriptors that change the product
  (e.g. "condensed", "powdered", "sweetened").
- Normalization is two levels via `resources/canonical_map.json`:
  - `canonical`: the specific product ("heavy cream", "cream of chicken soup", "peanut butter", "chicken breast").
  - `family`: the broader group used for search and matching ("cream", "soup", "peanut", "chicken").
- Forms that change the match stay distinct canonicals (spec T2): "peanut butter" is not "peanuts"
  for matching, but both belong to the `peanut` restriction group for exclusion.
- Building the map: compute name frequencies over all parsed lines; map the most frequent names
  (enough to cover the bulk of lines) by rules + a one-off LLM-assisted script whose output is
  reviewed and committed. Unmapped names fall back to the parsed name as canonical and its last
  noun as family. Report coverage (% of lines mapped) in the README.
- Whole-word matching only. Never substring-match raw text (the substring `hing` hits "washing").

**Restriction map** (`resources/restrictions.json`): plain data, conservative. Each group lists
families and product canonicals, e.g. `dairy` -> milk, cream, butter, cheese, yogurt, ghee, whey,
buttermilk, sour cream, cream of chicken/mushroom soup, ... If a compound product's status is unknown,
treat it as "may contain" and show it as a warning, not as safe.

**DuckDB schema** (`data/nosh.duckdb`)

- `recipes(id, source, title, instructions_raw, steps_json, n_steps, n_ingredients)`
- `recipe_ingredients(recipe_id, pos, raw_line, name, canonical, family, qty, unit)`
- `build_meta(key, value)`: counts loaded/dropped per source, step rule version, map version, build time.
- Title search: DuckDB full-text index (BM25) on title. Fuzzy fallback with rapidfuzz on titles.

Vector search is **optional** (D14) and off by default. Only add it after everything else works, behind
`uv run nosh build --vectors`, and only if it measurably helps the eval cases.

## 6. Conversation: session state and routing

**SessionState** (pydantic, `session.py`): `mode` (auto|search|match|substitute), `active_action`
(last executed action with its full parameters), `filters` (include, exclude/hard constraints,
max_steps, other preferences), `last_results` (ordered IDs), `active_recipe_id`, `pending_offer`
(an action the bot offered, e.g. "adapt recipe X"), `history` (last N turns of text, for slot
extraction only).

Rules:

- A newly stated restriction is added to `filters` and applies to the active action until the user
  removes or changes it ("actually dairy is fine" removes it).
- "the second one", "this recipe" resolve against `last_results` / `active_recipe_id`.
- "yes" / "do it" executes `pending_offer`.
- Every bot reply starts with a one-line status: task used, active filters. State is visible.

**Routing (D11): the LLM extracts slots; code picks the task.**

`router/slots.py` sends the user turn + short history + current filters to the LLM (temperature 0,
structured output) and gets:

```python
class Slots(BaseModel):
    dish: str | None
    query_text: str | None            # free-text need, e.g. "quick vegetarian dinner"
    have_ingredients: list[str]       # "I have eggs, onions..." (quantities kept in raw form)
    remove_ingredients: list[str]     # "without custard powder", "remove eggs"
    add_constraints: list[str]        # "no peanuts", "no dairy"
    drop_constraints: list[str]       # "dairy is fine now"
    max_steps: int | None
    preferences: list[str]            # "fewest extra ingredients", "fewest steps"
    aggregate: Literal["count", "percent", "rank", "mean", "median", "top_ingredients"] | None
    aggregate_detail: str | None      # e.g. "fewest instruction steps, top 5"
    ask_attribute: str | None         # "cooking time", "calories", "ingredients", "instructions"
    recipe_ref: str | None            # "this", "the second one", or an explicit ID
    dataset_only: bool                # "use only ingredients that appear in the dataset"
    is_followup: bool
    is_affirmation: bool              # "yes", "go ahead"
```

`router/decide.py` is a pure function with this order (first match wins):

1. `is_affirmation` and `pending_offer` -> execute the offer.
2. `ask_attribute` not in SUPPORTED_ATTRIBUTES -> `Unsupported` (say what the dataset lacks; offer what it has).
3. `aggregate` set -> `Stats`.
4. `remove_ingredients` and (`dish` or `recipe_ref` or `active_recipe_id`) -> `Substitute`.
5. `len(have_ingredients) >= 2` -> `Match`.
6. `is_followup` with only constraint/preference changes -> re-run `active_action` with updated filters.
7. `dish` or `query_text` or a single ingredient -> `Search`; if the coverage probe says `none`, return
   `SearchNoMatch` with nearest titles and set `pending_offer` to a Substitute adaptation of the nearest
   recipe (offered, never executed automatically).
8. Otherwise -> ask one clarifying question.

If the UI mode is pinned (not auto), decide only among that task's actions plus Unsupported/clarify.

**Coverage probe** (`tasks/search.py`): BM25 + fuzzy title score of the best hit -> `exact | near | none`
using thresholds in `config.py`. Tune the thresholds on the eval cases, not by guessing; record the values.

## 7. Task specs

### T1: Search and factual Q&A

- Search by dish name (title BM25 + fuzzy), by ingredients (SQL on `family`/`canonical`), and natural
  language (LLM slots turn it into dish/ingredients/constraints; then the same structured search).
- Results: ID, title, and on request ingredients and instructions verbatim. Default top `SEARCH_TOP_K` (5).
- Constraint follow-ups ("I cannot have dairy") re-run the active search with the constraint and show
  evidence: for each result, state that no ingredient line maps to the banned group; flag "may contain" lines.
- Stats (`tasks/stats.py`), computed in SQL over the complete filtered set. Must support at least:
  count of recipes matching ingredient conditions (AND/OR), percentage of dataset, most common
  ingredients (by family, with filters), mean/median ingredient count, ranking by `n_steps` (fewest/most, top N).
  Every stats answer states: filters applied, number of recipes analyzed, denominator (total usable
  recipes, with dropped-record count), the calculation, ingredient-normalization note (which names were
  counted as e.g. "garlic"), and the step rule version if steps are used. Ties broken by ID, stated.
- Unsupported attributes (nutrition, time, servings, cuisine, ratings): say it is not in the dataset.

### T2: Ingredient-based matching (never invents recipes)

- Input: ingredient names with optional quantities, hard constraints (exclude X, max steps), preferences.
- Normalize user ingredients with the same map as the dataset.
- Definitions (D15), shown in the output legend:
  - `matched`: user ingredients the recipe uses.
  - `missing`: user ingredients the recipe does not use.
  - `extra_required`: recipe ingredients the user did not list, split into non-staples and pantry staples (`pantry.json`).
- Hard filters first (in SQL): excluded groups via restriction map, `max_steps`.
- Ranking is lexicographic and explainable: (1) more `matched`, (2) fewer non-staple `extra_required`,
  (3) fewer `n_steps`, (4) ID. Preferences reorder these keys (e.g. "fewest steps" moves steps first).
  Each result gets a one-line "why ranked here" generated by code from the keys.
- No eligible recipe: say "no match found under your constraints", then separately "closest
  alternatives (violate: ...)" listing the violated constraint per result.
- Quantities: compare only when both sides parse to the same unit dimension (mass/volume/count via
  `units.json`); otherwise print "name-level match; quantity sufficiency not verified".
- Follow-up "exclude eggs too" updates filters and re-ranks.

### T3: Evidence-based substitution

Input: a base recipe (resolved via search or `recipe_ref`) and an ingredient to replace or a restriction.

Pipeline (`tasks/substitute.py`):

1. Resolve the base recipe; if several candidates, use the top one and say so.
2. Find the target lines in the recipe (all lines whose family/canonical is in the target or banned group).
3. Role tagging (LLM, temperature 0, `prompts/roles.txt`): what the target does in this dish
   (binding, moisture, leavening, fat, thickening, flavor, texture, main_protein). Lazy, only this recipe (D10).
4. Evidence mining (`tasks/evidence.py`), code only:
   - **Similar dishes:** recipes whose titles share the dish's head terms (e.g. "banana bread"), via BM25,
     up to `SIMILAR_N`. If too few, widen to the same family of dish (e.g. "quick bread", "pie").
   - **Contrastive evidence (strongest):** split similar dishes into WITH target vs WITHOUT target.
     For each family, compare its frequency in WITHOUT vs WITH; candidates are families over-represented
     in WITHOUT with support >= `MIN_SUPPORT` recipes. Keep the supporting recipe IDs and raw lines.
   - **Explicit evidence:** ingredient lines of the form "X or Y" where X is the target (exclude "or to
     taste", "or more", "or as needed"), and recipes whose text mentions substituting the target.
   - **Omission evidence:** similar dishes that simply do not use the target (valid for minor flavor items).
   - Filter all candidates with the restriction map and, if `dataset_only`, keep only canonicals present in the dataset.
5. Grade per candidate: `direct` (similar dish, same role), `indirect` (other dish types or role inferred),
   `none`. The LLM may propose extra candidates; they are kept only with local evidence, else labelled
   `predicted, no local evidence`.
6. If every candidate is `none`: state the uncertainty and return `can_adapt=false` or a clearly labelled
   predicted adaptation. Web search is out of scope unless everything else is done (then: labelled external, with URL).
7. Generation (LLM, temperature `ADAPT_TEMPERATURE` <= 0.3, `prompts/adapt.txt`) returns `AdaptedRecipe`:

```python
class Evidence(BaseModel):
    source: Literal["local", "external"]
    recipe_id: str | None = None
    url: str | None = None
    strength: Literal["direct", "indirect"]
    note: str

class Substitution(BaseModel):
    original: str
    replacement: str | None            # None = omit
    role_covered: list[str]
    evidence: list[Evidence]
    expected_effects: str
    effects_status: Literal["verified_in_dataset", "predicted"]

class AdaptedRecipe(BaseModel):
    recipe_id: str
    substitutions: list[Substitution]
    ingredients: list[str]             # full updated list
    instructions: list[str]            # full revised steps
    uncertainty: str | None
    can_adapt: bool
```

8. Validators (`tasks/validate.py`), all code: no banned family/product in `ingredients`; `dataset_only`
   respected; every local `recipe_id` was retrieved this turn and contains the replacement; external
   evidence has a URL; no medical/allergy safety claims (phrase list in config); `ingredients` and
   `instructions` non-empty when `can_adapt`. On failure, send the errors back and regenerate, max
   `MAX_RETRIES` (2); then refuse honestly.
9. Render: substitutions with evidence (ID + title + the supporting raw line), full adapted recipe,
   expected effects tagged predicted/verified, uncertainty, label-check reminder.

## 8. LLM usage (`llm.py`)

- Provider from env: `NOSH_LLM_PROVIDER=gemini|anthropic`, keys `GEMINI_API_KEY` / `ANTHROPIC_API_KEY`,
  model names in env/config. Verify current model names from provider docs at build time; do not guess.
- Structured output via pydantic schemas for every call except final phrasing.
- Temperatures: slots 0, roles 0, adapt <= 0.3, phrasing 0.
- Disk cache keyed by (provider, model, prompt hash, input) in `data/llm_cache/`, so evals replay.
- Total LLM calls per turn: slots 1, plus T3 roles 1 + adapt 1-3. Nothing else.
- Without an API key: build, stats and structured CLI paths still work; chat says the key is missing.

## 9. Interfaces

- `uv run nosh build` builds `data/nosh.duckdb` from `recipes_raw/`.
- `uv run nosh chat [--mode auto|search|match|substitute]` is the CLI REPL (required minimum interface).
- `uv run nosh ui` launches the Streamlit app: chat in the main pane; sidebar with mode selector, active
  filters (removable), last results, build stats. Result tables via dataframes; evidence in expanders.
  Built last, after the CLI works end to end.
- `uv run nosh eval` runs `evals/cases.yaml` and writes `evals/results.md`.

## 10. Evals (graded deliverable, committed)

`evals/cases.yaml` holds the required demo conversations and a small eval set. Must include at least:
the spec's example conversations (penne -> no dairy; garlic+onion count and %; tomato fewest steps;
eggs/onions/tomatoes/rice match -> exclude eggs; banana bread without eggs, dataset only; pasta cream
without dairy), multi-turn follow-ups, an aggregate stats query, a no-match case (e.g. "banana custard pie"),
an unsupported-attribute question (calories / cooking time), a substitution with weak or conflicting evidence,
and routing cases covering each branch of `decide()`.

Checks are deterministic where possible: computed numbers equal an independent SQL query; no banned
ingredient in results; cited IDs exist, were retrieved and contain the cited ingredient; correct route.
`evals/baseline.md`: at least one question answered by a plain one-shot LLM call (no retrieval) next to
nosh's answer, showing where the one-shot answer is wrong or ungrounded (e.g. the garlic+onion count).
`evals/results.md` reports what worked, what failed, and why. Failures are reported, not hidden.

**Unit tests are not a deliverable.** You may write quick checks in `tests/` while developing; `tests/`
is gitignored and must never be committed. Do not spend time on test infrastructure.

## 11. Build order and checkpoints

Work in this order; at each checkpoint run the real command on real data and show its output before moving on.

1. pyproject (scripts, deps), config, `scripts/get_data.py`, `.env.example`.
2. Load + clean + steps + parser + canonical map v1 + restrictions + build. Checkpoint: `nosh build` prints
   counts per source, dropped records, map coverage, step stats; spot-check 10 random recipes' parsed lines.
3. Stats. Checkpoint: garlic+onion count/percent and tomato fewest-steps answers with full disclosure text.
4. Search + coverage probe + session + slots + decide + CLI chat. Checkpoint: penne -> "I cannot have dairy" works.
5. T2 match. Checkpoint: the spec's eggs/onions/tomatoes/rice example, then "exclude eggs too".

6. T3 evidence mining. Checkpoint: banana bread without egg shows contrastive candidates with IDs.
7. T3 generation + validators. Checkpoint: both spec T3 examples end to end, plus one weak-evidence case.
8. Evals + baseline + results.md.
9. Streamlit UI.
10. README: setup (uv sync, get data, set key, build, chat/ui), architecture summary linking
    `docs/decisions.md`, normalization + step rule + scoring explained, eval results, limitations,
    AI-tool disclosure.

If behind schedule, cut in this order: vector search, web search, UI polish. Never cut stats correctness,
hard-constraint checks or citation validation.

## 12. Git

- Commit after each checkpoint with a plain message ("add step-counting rule v1").
- Never commit: `recipes_raw/`, `data/`, `tests/`, `.env`, the assignment PDF.
- When a decision changes or a new one is made, update `docs/decisions.md` in the same commit.
- Log AI-tool use per module in the README's disclosure section as you go.
