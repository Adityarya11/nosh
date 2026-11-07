# Design decisions and trade-offs

This is the decision document for the assignment as described at [TASK](AIInternTask.pdf)

Running log of architecture decisions for the Recipe Box chatbot (GenAI intern take-home).
Each entry: what I chose, what I rejected, and why. Numbers come from probes run on the raw dataset on 2026-11-06; re-run them from code before quoting in the README.

Timeline: due within 2 days. Build days: Sun 2026-11-08 and Mon 2026-11-09.

---

## Data findings (raw probes, 2026-11-06)

| Finding                                                                                                                               | Consequence                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| 125,164 recipe keys across the 3 files; 0 key collisions between files                                                                | Dict key is the stable recipe ID (store source file alongside)                     |
| 280 (ar) + 237 (fn) empty records; 122,971 have a non-empty ingredient list                                                           | Every statistic must state its denominator and what was excluded                   |
| 39,518 AllRecipes records carry `ADVERTISEMENT` tokens in ingredient lines                                                            | Strip at load time                                                                 |
| ~20,500 of 25,323 Epicurious instruction texts appear duplicated (heuristic: first 40 chars recur later in the text; verify properly) | Dedupe before counting steps, or "fewest steps" rankings are wrong                 |
| ~16k instruction texts are a single paragraph (no newlines)                                                                           | Step-counting rule must be explicit: newline split, sentence-split fallback        |
| 52 Food Network records have no title; 6,839 titles are duplicated                                                                    | IDs, not titles, identify recipes                                                  |
| Substring `hing` matches 1,691 recipes (e.g. "wasHING"); `asafoetida` matches 35                                                      | Raw substring matching is wrong; ingredient normalization is core work             |
| `paneer` 30, `jackfruit` 11, `tofu` 692                                                                                               | Dataset is American; Indian-specific restrictions (Navratri etc.) are out of scope |
| 134 banana bread recipes, 9 without egg (e.g. "Vegan Banana Bread", "Microwave Tofu Banana Bread")                                    | Task 3's example has real local evidence                                           |
| "banana custard pie": 0 title hits; "banana custard" 4, "banana cream pie" 35, "custard pie" 50                                       | Good routing demo: no exact match, near neighbours exist                           |
| ~60k ingredient lines contain " or ", mostly "or to taste"; 1,244 recipes mention "substitut"                                         | Explicit substitution evidence exists but needs filtering                          |

## D1. One app, shared core, three task handlers

- **Chosen:** one repo with a shared core (loader/cleaner, ingredient normalizer, restriction map,
  stats engine, hybrid search, session state) and three handlers (`search_qa`, `match`, `substitute`).
  One chat UI with a mode selector (Auto / Search & stats / Match / Substitute) plus a CLI entry per
  task for reproducible evals. UI presentation is left to the implementer.
- **Rejected:** three separate apps (duplicates the core, no cross-task follow-ups);
  one app with no way to isolate a task (graders score tasks separately).
- **Why:** graders can test each task in isolation, users get one conversation, and the shared core
  is needed either way.

## D2. Structured data first; retrieval is one arm

- **Chosen:** normalized tables, e.g. `recipes(id, source, title, instructions, n_steps, ...)` and
  `recipe_ingredients(recipe_id, raw_line, canonical_name, qty, unit)`, in DuckDB/SQLite.
  Counts, percentages, medians, rankings and hard filters run as code over the complete filtered set.
- **Why:** the spec requires aggregates over the complete filtered result set and forbids LLM-guessed
  statistics. Semantic top-k cannot answer "how many recipes contain garlic and onion".
- Vector/BM25 search is used only for fuzzy dish names and natural-language requests.

## D3. Plain Python + pydantic + one LLM client (no LangChain)

- **Rejected:** LangChain (originally proposed by Aditya; not required by the spec).
- **Why:** the memory the spec asks for is narrow: "a newly stated restriction should apply to the
  active search unless the user changes it". That is a typed `SessionState` (active filters, last result
  IDs, active recipe, pending offer) updated each turn. Chat-history buffers don't provide it, so we would
  write the state logic anyway. The interview involves modifying the code live, so less framework
  magic is better.

## D4. No external memory systems (mem0, Hindsight, Cognee)

- **Why:** they solve long-term memory across sessions (fact extraction, consolidation). The assignment
  needs memory within one session. They add infra, cost (Hindsight cloud is paid) and non-determinism,
  for no graded benefit.

## D5. No graph database

- **Why:** recipes connect through ingredients, not shared IDs. The `recipe_ingredients` table already
  is that bipartite graph and SQL queries it directly (co-occurrence, "recipes without X", etc.).

## D6. LanceDB hybrid search for the fuzzy path (superseded by D14: now a stretch goal)

- **Chosen:** embed `title + canonical ingredient names` (short texts) plus BM25 on titles. LanceDB is
  known from last-rag. Measure embedding time for ~123k texts on the RTX 3050 before committing; if it
  is too slow, embed titles only.

## D7. Temperature is not the grounding mechanism

- **Chosen:** temperature 0 for intent parsing and Tasks 1-2; at most 0.3 for Task 3 generation.
- **Why:** grounding comes from code-side validators (cited IDs were retrieved, cited recipes contain
  the substitute, no forbidden ingredient in output, no medical-safety claims), with up to 2 regeneration
  retries. Variety in Task 3 comes from retrieval, not sampling.

## D8. LLM provider

- **Chosen:** default to the Gemini free tier (named in the spec, free for graders), provider
  configurable (Claude Haiku as an option), with an on-disk response cache so evals replay deterministically.

## D9. Task 3 uses evidence tiers, not a numeric "alpha" weight

- **Rejected:** weighting KB evidence vs. LLM knowledge with a numeric alpha > 0.5. An LLM's
  internal knowledge cannot be weighted numerically.
- **Chosen:** rank substitutes by evidence tier, enforced in code:
  1. **Contrastive local evidence (strongest):** among similar dishes (e.g. all banana breads), compare
     recipes without the target ingredient to those with it. Ingredients over-represented in the
     "without" group are data-backed substitutes, cited by recipe ID.
  2. **Explicit local evidence:** "X or Y" ingredient lines (filtered, excluding "or to taste") and
     recipes mentioning substitution.
  3. **LLM-proposed:** the LLM may suggest candidates, which are then checked against local data.
     Without local support, effects are labelled "predicted, no local evidence".
  4. **Web:** last resort, labelled external with a link.
- This is the "knowledge base outweighs the LLM" rule, enforced by code instead of a number.

## D10. Lazy ingredient-role tagging

- **Chosen:** tag ingredient roles (binding, moisture, leavening, fat, thickening, flavor, texture,
  main protein) with the LLM only for the target recipe and its candidate neighbours at request time.
- **Rejected:** tagging all ~123k recipes up front (cost and time, given two build days).

## D11. Routing: the LLM extracts slots, code picks the task (decided 2026-11-07)

- Two signals: **intent** from the wording and **coverage** from a cheap title-search probe
  (exact / near / none). The task is NOT picked by retrieval: the user's wording says what they want;
  the dataset only decides how well we can answer it.
- The LLM (temperature 0, structured output) only fills a `Slots` model (dish, have/remove ingredients,
  constraints, aggregate, attribute asked, recipe reference, follow-up flags). A pure function
  `decide(slots, coverage, state)` picks the action with a fixed rule order: pending offer, unsupported
  attribute, stats, substitute, match, follow-up refine, search / no-match, clarify.
- Options considered: regex-only (brittle on natural language); semantic router over example queries
  (picks a route but extracts no slots, so a parser is needed anyway); LLM tool-calling agent
  (non-reproducible routing, can drop hard constraints silently, hard to test or explain).
- Escalation to another task is *offered*, never silent (Task 2 forbids inventing recipes; the spec
  values honest refusal over fabrication). Example: "banana custard pie" has 0 title hits, so the bot
  shows the nearest recipes and offers to adapt Banana Cream Pie; "yes" executes the stored offer.
- Why: routing becomes unit-testable without the LLM, explainable line by line, and easy to modify
  in the live interview.
- A hand-written 30-case routing set was skipped for time; routing cases live inside `evals/cases.yaml`.

## D12. No Docker

- **Chosen:** `uv sync` + `uv run nosh build` + `uv run nosh chat|ui`. The app is a local, single-user
  program with a file database; `uv.lock` already pins the environment.
- **Rejected:** Docker. Adds a toolchain graders may not have, slows iteration, and solves no problem
  uv doesn't already solve here. Revisit only if a dependency proves platform-specific.

## D13. Evals are a deliverable; unit tests are not

- `evals/` (cases, runner, results, one-shot baseline) is committed: the assignment grades it.
- Unit tests written while developing stay in `tests/`, which is gitignored and never committed.

## D14. Vector search optional, off by default

- **Chosen:** BM25 + fuzzy title search plus structured SQL over normalized ingredients. The LLM turns
  natural-language requests into dish / ingredient / constraint slots, which covers most "natural
  language" queries without embeddings.
- **Why:** graders must rebuild the index on their machines; embedding ~123k texts adds minutes and a
  model download. Add `--vectors` only if the eval cases show a gap it fixes.
- This replaces the "LanceDB hybrid" plan in D6, which is now a stretch goal.

## D15. Task 2 term definitions

- The spec lists "matched, missing, extra required" without defining them. Our interpretation:
  `matched` = user ingredients the recipe uses; `missing` = user ingredients the recipe does not use;
  `extra_required` = recipe ingredients the user did not list, split into non-staples and pantry staples.
- Ranking is lexicographic (matched desc, non-staple extra asc, steps asc, ID) instead of a weighted
  sum, so each position can be explained in one sentence. Preferences reorder the keys.

## D16. Python version

- `.python-version` is 3.11.9 (`requires-python >=3.11.9`). Earlier draft said 3.14; dropped in favour
  of 3.11 because graders are likelier to have it and all dependencies ship wheels for it.

## D17. Code norms

- One-line purpose header per file; no multi-line docstrings or triple-quoted prose; prompts in
  `.txt` files; tunables in `config.py`; domain knowledge in `resources/*.json`.
- Why: the code is walked through and modified live in the interview; it must read fast.

## D18. UI: Streamlit, built last

- Sidebar shows mode selector and the active filters, which makes session state visible to graders.
  The CLI REPL is the required minimum and is built first.

## Out of scope

- Indian dietary restrictions (Navratri, Thursday, Jain): dataset is American. Mentioned only as a
  possible extension.
- Long-term cross-session memory, deployment, auth.

## AI tool use (for README disclosure)

- Hermes/Claude: AGENTS.md and spec drafting.
- Claude Code: implementation (to be logged per module as it happens).
