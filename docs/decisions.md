# Design decisions and trade-offs

This is the decision document for the assignment as described at [TASK](AIInternTask.pdf)

Running log of architecture decisions for the Recipe Box chatbot (GenAI intern take-home).
Tools and dependencies, with their roles and rejected alternatives, are in `docs/stack.md`.
Problems caught during the build (symptom, how found, fix, verification) are in `docs/findings.md`.
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
- DuckDB over SQLite: both are embedded single-file databases and SQLite would work at this scale.
  DuckDB is columnar and built for scans/aggregates, with `median()`/`quantile_cont()` built in, so the
  stats SQL is shorter. Cost: the `fts` extension downloads once (SQLite's FTS5 would be offline), which
  is moot because chat needs the network for the LLM anyway. LanceDB is a vector store, not a
  replacement for either (no joins/GROUP BY for stats).

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

## D19. Build-time cleaning choices (step 2)

- Ingredient lines ending in ":" (section headers such as "For the sauce:") are dropped; three
  Food Network recipes had only headers and are excluded, so usable recipes are 122,968 (probe said 122,971).
- Dropped per source: ar 280 (empty records), epi 47, fn 1,869 (237 empty records + 1,629 with no
  ingredient list + 3 header-only). All reported in `build_meta` and by `nosh build`.
- Parser drops descriptor words listed in `resources/descriptors.json` (not in code). Product-changing
  words (ground, dry, condensed, powdered, whole, hot) are deliberately kept.
- Canonical map v1 is generated by `scripts/build_canonical_map.py` from name frequencies plus
  `resources/map_rules.json` (aliases, ordered family phrase rules, generic head words). No LLM used;
  it covers the most frequent names up to `MAP_COVERAGE_TARGET` (85% of lines). Unmapped names use
  the same family rules at build time, so restriction groups still apply to the tail.
- DuckDB `fts` extension is installed on first build (needs network once).

## D20. Stats matching and zero-step recipes (step 3)

- Step rankings and step averages leave out recipes with `n_steps` below `MIN_RANKABLE_STEPS` (1). The rule lives
  in `data/steps.py` (`RANKABLE_SQL`, `no_instructions_note`) and every step answer states the excluded count.
  Without it, "fewest steps" would return recipes with no instructions.
- Step rule is now **v2**: besides v1 (dedupe repeated text, newline split, sentence fallback) it drops boilerplate
  lines before counting: copyright, (c), "all rights reserved", "recipe courtesy of", "photograph by", and any
  line equal to the recipe title. Patterns are in `config.py`. A lone text block is filtered per sentence so one
  copyright sentence cannot erase the whole text. Zero-step recipes went from 33 (v1) to 44 (v2).
- A bare ingredient term ("garlic") matches its whole family, except rows whose canonical contains a modifier
  word (powder, paste, sauce, puree, juice, butter, oil, milk, extract, flour, salt, broth, stock, ...) that is
  not part of the term itself. The words live in `modifier_words` in `resources/map_rules.json`; this replaced an
  explicit `distinct_forms` name list because a rule covers unseen names. A specific term ("heavy cream") matches
  that canonical only. Every stats answer lists the names counted and the modified forms left out.
- "Most common ingredients" counts recipes per family, so the filter terms themselves appear in the list.

## D21. Restrictions: banned, may-contain, safe lists, group tags (step 3 review)

- Each group in `resources/restrictions.json` has `families` and `products` (level `banned`; a product matches as a
  whole phrase, so "condensed cream of chicken soup" is banned for dairy), `definite_keywords` (whole-word hit:
  `banned`, e.g. named cheeses, cake mix, puff pastry, mayonnaise, almond meal), `maybe_keywords` (whole-word hit:
  `may_contain`, e.g. chocolate, margarine, oats, pesto, broth) and `safe` phrases. A safe phrase is removed from the
  text first ("coconut milk", "cream of tartar", "spaghetti squash"), and a safe hit also overrides a family ban.
- Every canonical_map.json entry carries a `groups` tag (group -> level). `scripts/build_canonical_map.py` drafts the
  tags from the keyword lists; I reviewed the whole tagged list and the whole untagged list by hand and fixed the
  gaps in restrictions.json (yolk, crema, bare "worcestershire", ladyfinger, pie shell, pasta shapes, foie gras,
  egg/spring roll wrapper), then regenerated. No LLM was used for this pass (llm.py does not exist yet); the review
  is mine and should be checked by the author. `classify` uses the tag for names in the map and the keyword lists for
  the rest (about 15% of lines).
- Keywords are also checked on the raw ingredient line, because the parser can lose words. The raw scan uses the same
  definite/maybe levels; the stricter of name-level and raw-level wins. A tag cannot clear a raw-line hit, so false
  positives are fixed with `safe` phrases, not by editing a tag.
- Lines containing "recipe follows", "recipe to follow", "recipe below", "see recipe" or "see below"
  (`SUB_RECIPE_PHRASES`) are `may_contain` for every group, because the referenced sub-recipe's ingredients are not in
  the list. 8,392 lines in 6,345 recipes (5.2%) are affected, so any hard constraint drops those recipes.
  `sub_recipe_lines` records them; every answer that applies an exclusion states how many matching recipes were
  excluded only because of such a line (`store.sub_recipe_excluded`, `render.sub_recipe_note`).
- Misspellings: a group may list `fuzzy_keywords` (egg: mayonnaise). Tokens within `FUZZY_MIN_RATIO` 85 of one
  (rapidfuzz ratio, length difference at most 3) count as a definite hit.
- Hard constraints exclude both levels. `may_contain` is shown as a label. Composite groups (vegetarian, vegan)
  use `also_excludes` and combine their parts' flags.
- Flags are computed at build time into `line_flags(recipe_id, pos, grp, level)`; queries use it.
  `restrictions.check_line` is the same logic at chat time to re-check LLM output (rule 9).
- Unicode: `fold()` in ingredients.py applies NFD and strips combining marks before parsing and before restriction
  matching. NFKD was not used because it rewrites the vulgar fractions (143 lines) and the trademark sign (396 lines).
- Irregular plurals (`irregular_singulars`): cookies, brownies, pies, smoothies, veggies, chilies and similar,
  which the generic "ies" rule turned into "cooky", "py", "chily".
- `nosh build` prints a leak report per restriction with line and recipe counts, a safe-list audit, the
  keyword-inside-longer-word list, and an independent probe scan: `resources/leak_probes.json` is a separate term
  list (named cheeses, wrapper and dough words, fish and shellfish names, ...), and the report counts lines and
  recipes that carry those terms but no flag from the group or its constituents. The probe list is not derived from
  the keyword lists, so it can find what they miss.
- Conservative by design: a hard "no dairy" excludes 62.3% of recipes (59.2% with a definite hit), gluten 54.6%,
  vegetarian 46.4%, vegan 84.7%. Known over-flags: "vegan shredded cheese blend", "butter flavored shortening",
  tortilla chips (gluten), rye whiskey, "an ice cream maker". Known gaps by design: curry paste (shrimp paste),
  kimchi, gochujang, ranch and tartar sauce are not flagged.
- Source text is sometimes corrupted (U+FFFD, "&241;"); 3 canonicals still contain "cr me" or "jalape o" because the
  raw line itself has lost the letters. "creme fraeche" and "frache" are keywords for that reason.
- The keyword and safe lists are my judgement; they are plain data and should be reviewed by the author.

## Out of scope

- Indian dietary restrictions (Navratri, Thursday, Jain): dataset is American. Mentioned only as a
  possible extension.
- Long-term cross-session memory, deployment, auth.

## AI tool use (for README disclosure)

- Hermes/Claude: AGENTS.md and spec drafting.
- Claude Code: implementation (to be logged per module as it happens).
