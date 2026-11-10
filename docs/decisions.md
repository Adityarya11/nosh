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
| Substring `rum` hits 12,441 recipes ("crumbs", "drumsticks"); whole word `rum` hits 2,206 | Raw substring matching is wrong; ingredient normalization is core work |
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

## D22. Step 4: search, coverage probe, session, slots, decide, chat

- Search (`tasks/search.py`): title search is DuckDB BM25 with `conjunctive := 1`, so "banana bread" means titles with
  both words (3,503 titles contain either word; 191 contain both). If nothing matches, the pool falls back to the 50
  nearest titles by rapidfuzz `token_sort_ratio`, and the output says "fuzzy title" and is ordered by ID. Ingredient
  search is the same SQL as stats (`match_where`). Hard constraints (restriction groups, `max_steps` with the
  zero-step floor) are SQL filters, so every shown result passes them. Order: BM25 score, ties by recipe ID.
- Coverage probe: candidates are the BM25 top 20 plus the fuzzy top 20; each title is scored by the share of query
  words (stopwords removed, singularized) that match a title word at rapidfuzz ratio >= 85; the best score maps to
  exact (>= 0.99), near (>= 0.70) or none. Raw `token_sort_ratio` was rejected because it punishes extra title words
  ("penne" vs "Meaty Penne" = 62). Values were set by probing 8 queries (penne, banana bread, banana custard pie,
  pasta with cream sauce, spagetti carbonara, chicken tikka masala, xylophone stew, pasta cream): "banana custard
  pie" scores 0.67 (none), "xylophone stew" 0.50 (none), the rest 1.0. That is a calibration on a handful of
  queries, not on the eval set; re-tune in step 8 and record the final numbers here.
- No match for the title (probe `none`) returns `SearchNoMatch` with the three nearest titles and sets
  `pending_offer` to a `Substitute` on the nearest recipe; nothing is adapted until the user says yes, and the
  substitution task itself comes in steps 6-7 (a yes now answers "not available yet").
- Constraint with no survivors: "No match found" first, then up to 5 closest alternatives, each listing the flagged
  ingredient line that violates the constraint (rule 5). Results that survive state, once, that none of their lines
  matched the restriction list and that this is a text match, not a safety guarantee.
- Every answer with an exclusion states the sub-recipe count (D21): `store.sub_recipe_excluded_in` for search,
  `sub_recipe_excluded` for stats, one shared sentence in `render.sub_recipe_note`.
- Session (`session.py`): `mode`, `active_action`, `filters` (exclude groups, max steps, preferences), `last_results`,
  `active_recipe_id`, `pending_offer` (valid for one turn) and `history` (last `HISTORY_TURNS`). New restrictions
  persist until dropped ("dairy is fine now"). A restriction phrase is mapped to a group by the `aliases` list in
  restrictions.json; a phrase with no group ("no cilantro") is reported as not applied, never silently dropped.
  Ordinal references ("the second one") resolve against `last_results`.
  A misspelled group name ("diary", "glutan") is accepted when its Damerau-Levenshtein similarity to the group name is
  at least `ALIAS_FUZZY_MIN_SIM` (0.8) and the word has 5+ letters, and the reply says how it was read ("I read
  'diary' as 'dairy'"). Typos are matched against group names only, not aliases: matching aliases would read
  "batter" or "bitter" as "butter" and silently exclude dairy. First real test: "I cannot have diary" was refused
  as unknown (correct but unhelpful), which led to this rule.
- Slots (`router/slots.py`, `prompts/slots.txt`): the spec's `Slots` model, one structured call per turn at
  temperature 0. `decide` (`router/decide.py`) is the pure function from AGENTS.md section 6, same order. Stats
  requests are built in code from the slots: `have_ingredients` are the counted ingredients (ALL unless the detail says
  "or"/"any"), the number and "most"/"fewest" come from regexes on `aggregate_detail`. A pinned mode only allows its own
  task plus unsupported/clarify.
- `chat.py` (not in the AGENTS.md layout) holds one turn so the CLI stays thin and the turn can be driven without a
  terminal. `cli.py` adds `nosh chat [--mode ...]`; without a key or model name it prints what is missing and exits.
- `llm.py`: Gemini by default through `google-genai` (structured output via `response_schema`), Anthropic through
  `messages.parse(output_format=...)` with no sampling parameters (several current Claude models reject them), JSON
  cache in `data/llm_cache/` keyed by provider, model, schema, temperature, system and user text. Model names are NOT
  defaulted: the docs searched for Gemini gave inconsistent names, so `.env` must set `NOSH_GEMINI_MODEL`.
  First real Gemini call (gemini-3.8-flash, user's key) extracted slots correctly, so the API accepts the `Slots` schema
  with its defaults. The Anthropic path is untested.
- Constraints (F17): a stated constraint is resolved by `constraints.resolve` as a restriction group (exact alias, then
  group-name typo), then an ingredient present in the dataset (`resolve_term` plus a whole-word raw-line regex), and is
  reported "not applied" only when it matches no line. `Filters.exclude_ingredients` holds ingredient exclusions; they
  apply to search and stats, and they also drop recipes with a sub-recipe line, counted in the sub-recipe sentence.
- Each shown result carries its canonical ingredient names and "no X match in N lines" for every active exclusion.
  Sort order is BM25, then fewer steps (zero-step recipes last, `STEP_SORT_LAST`), then ID (F18).
- `llm.py` retries transient Gemini errors (429, 500, 503, 504) up to `LLM_CALL_RETRIES` times with a doubling wait,
  after a live 503 overload on gemini-3.8-flash. `.env.example` pins `gemini-3.6-flash`, the model that worked live.

- Substitute is routed by `decide` but answers "not available yet" until steps 6-7.

## D23. Step 5: T2 ingredient matching (`tasks/match.py`, `render_match.py`)

- User ingredients are parsed with the dataset's own parser (`parse_line`: quantity, unit, name) and matched with the
  same `resolve_term` / `term_sql` rule as stats, so "tomatoes" means the tomato family minus the modified forms
  (paste, sauce, juice, ...). Candidates are the recipes that use at least one of them and pass the hard filters.
- Hard filters run in SQL before ranking: restriction groups, ingredient exclusions (D22, including sub-recipe lines)
  and `max_steps`. "exclude eggs too" is a follow-up that re-runs the active match with the new filter.
- One SELECT returns, per recipe, which of the user's ingredients it uses and the ranking keys. `matched` = user
  ingredients used; `missing` = the rest; `extra_required` = distinct canonical names on lines that match none of
  the user's ingredients, split into pantry staples (family in `pantry.json` families or canonical in its canonicals)
  and non-staples. Only non-staples are ranked; staples are listed, not hidden.
- Order (lexicographic): most matched, fewest non-staple extras, fewest steps (zero-step recipes last, `STEP_SORT_LAST`),
  recipe ID. A preference word moves a key to the front (`MATCH_PREF_KEYS`: "step" -> steps, "extra" -> extras).
  "Why here" is built in code from the keys: row 1 states its values, every later row names the first key on which
  it differs from the row above (or says it ties).
- Quantities are compared only when the user gave one: same unit dimension from `units.json` (volume/mass converted
  to a base unit; count units only when the unit is identical, or both are bare counts) and the recipe line parsed a
  quantity; otherwise the row says "name-level match; quantity sufficiency not verified".
- If every candidate is excluded the answer says "No match found under your constraints" first, then up to 5 closest
  alternatives from the unconstrained ranking, each with the flagged line or step count that violates the constraint.
  If the user listed an ingredient and also excluded it, a note says every result lacks it.
- Verified on real data (independent Python recomputation over the raw lines, `tests/verify_t2.py`): "eggs, onions,
  tomatoes, rice": 65,177 candidates, top-5 IDs, matched counts and extras equal; after "exclude eggs too": 29,263
  candidates, top-5 equal, 0 egg lines in the shown recipes, 1,939 recipes excluded only by a sub-recipe line (line-level
  independent count 1,939).
- Known limitation: equipment lines parsed as ingredients ("wooden skewer", "toothpick", "parchment paper", "a deep-fat
  thermometer", "cheesecloth", ...) count as non-staple extras: 2,025 lines in 1,967 recipes (1.6%).
- `render_match.py` is a separate file because `render.py` was near the 200-line guideline.

## Out of scope

- Long-term cross-session memory, deployment, auth.

## AI tool use (for README disclosure)

- Hermes/Claude: AGENTS.md and spec drafting.
- Claude Code: implementation (to be logged per module as it happens).

D23 addendum (step 5 review): "matched" is always the first ranking key and preferences only reorder the keys below it. Pantry staples
are matched by canonical only. Only group names and synonyms in `restrictions.json` `aliases` resolve to a group; names in
`ingredient_aliases` (milk, cheese, butter, shrimp) exclude that ingredient and the reply states the reading.

D24. T3 evidence mining (step 6, code only). Similar dishes: BM25 on the dish words (conjunctive), titles must end with the dish's
head noun (fallback to all matches below MIN_SIMILAR=30), then the broader type (head noun alone) is used and reported;
SIMILAR_N is 200 (was 50) so the WITHOUT group is not starved. Recipes with a "recipe follows"-type line are in neither the
WITH nor the WITHOUT group (they can hide the target). Contrast keeps a family only with support >= MIN_SUPPORT (3) and a
WITHOUT rate at least MIN_LIFT (2.0) times the WITH rate, after each supporting line passes check_line for every excluded group;
dropped families are reported with the violated group. Explicit "X or Y" lines scan the whole dataset and are split into in-similar
and elsewhere; dataset_only drops tails that are not ingredients in the dataset. Grades: direct only for an explicit "X or Y"
line in a similar dish; contrast is indirect until step 7 verifies the role (F23).

D25. T3 design (step 7): the LLM proposes, the data verifies. (1) roles.txt (temperature 0) tags what each target does in this recipe and names up to three broader dish classes. (2) propose.txt (temperature 0.3) proposes 3-5 substitutes per target. (3) Code verifies each proposal: direct = an explicit "target or substitute" line in a similar dish; indirect = an explicit line in another dish, OR the substitute is over-represented in the WITHOUT-target similar dishes (counts shown), OR the same in the widened dish classes, OR an instruction sentence mentions the substitution; none = "predicted, no local evidence". Every proposal passes the restriction check and dataset_only before it is graded. Targets are the removed ingredient plus every other line that breaks an active restriction (max 5). (4) adapt.txt (temperature 0.2) writes the AdaptedRecipe from the verified table only; validate.py checks restrictions, dataset_only, that each cited ID was retrieved this turn and has a line with the substitute, that evidence is not stronger than the verified grade, external evidence has a URL, no safety-claim phrases, and effects_status is "predicted" (the dataset has no texture or taste outcomes). Up to MAX_RETRIES=2 regenerations with the errors fed back, then an honest refusal. LLM calls per turn: slots 1, roles 1, propose 1, adapt 1-3 (AGENTS.md section 8 said roles 1 + adapt 1-3; the proposal call is new).

D26. Resource safety (step 7). A heavy live run crashed the dev laptop (15 GB RAM, about 3.8 GB free). Added AGENTS.md section 13: ask before heavy commands, never overlap them, DuckDB capped in config (2 GB, 2 threads), heavy runs go under `tests/guard.py` (kill on low RAM/disk/VRAM, job memory cap, peak memory printed). T3 scans are prefiltered in SQL with ILIKE and capped with LIMIT.

D27. T3 changes after the step 7 review. (1) The base recipe must contain the target's own canonical: `resources/target_forms.json` lists the forms ("cream" = cream, heavy cream, whipping cream, light cream, ...), so sour cream and cream cheese are not the target (they are still other targets under a dairy restriction). The base is chosen among similar-dish recipes with such a line, fewest replacements first. (2) Candidates come from two sources, both verified the same way: the LLM (up to 8) and the data (explicit "target or X" partners and WITHOUT-group families over-represented with MIN_SUPPORT and MIN_LIFT, up to DATA_CANDIDATES each); each candidate shows its origin. Lines about brushing, glazing or garnishing are not explicit evidence. (3) The adapt prompt gets the full ingredient lines of every citable recipe and must scale amounts from them; an amount with no cited recipe behind it must carry "(estimate)", checked by the validator. (4) All renderers output Markdown (`render.bullets` for the note lists).

D28. Evals (step 8). `evals/cases.yaml` holds nine conversations (spec T1 penne, garlic+onion, tomato fewest steps, banana custard pie no-match, calories unsupported; spec T2 with "Exclude eggs too"; spec T3 banana bread and cream pasta; one weak-evidence T3) plus ten routing cases that call `decide()` directly (branches 1-8, a no-match search and a pinned mode). `evals/run.py` (also `uv run nosh eval`) runs the conversations through the real chat turn, so LLM calls replay from the disk cache after the first run, and writes `evals/results.md`. Numbers are checked against independent recomputations in `evals/checks.py` (plain Python over the ingredient table, the leak-probe lists, a separate T2 ranking); cited T3 recipe IDs are re-checked against the dataset. `evals/baseline.py` asks one plain LLM call the garlic+onion question and writes `evals/baseline.md`. Dev runs use Anthropic claude-haiku-5-5 (F26).

D29. UI (step 9). `src/nosh/ui/app.py` is a minimal Streamlit app over the same `turn()` function: Markdown chat, a mode selector, removable active filters, the last results and the build stats in the sidebar. `uv run nosh ui` launches it. Tested headless with Streamlit's AppTest (load, a chat turn, a mode change, removing a filter), not in a browser.
